"""
Bot Trading - Web Service con detectores en background
Servidor Flask que mantiene vivo el servicio en Render
Los detectores se ejecutan en threads separados
"""

import threading
import time
from datetime import datetime, timezone
import os
import requests
import sys
import logging
import smtplib
import ssl
from email.mime.text import MIMEText
from logging.handlers import RotatingFileHandler
import yfinance as yf

# Forzar flush inmediato de logs (crítico para Render)
sys.stdout.reconfigure(line_buffering=True)

# ── LOGGING PERSISTENTE ────────────────────────────────────────────────────
_LOG_FILE = 'logfile.txt'
_log_handler = RotatingFileHandler(
    _LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3, encoding='utf-8'
)
_log_handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
logging.basicConfig(
    level=logging.INFO,
    handlers=[logging.StreamHandler(sys.stdout), _log_handler],
)
logger = logging.getLogger('bottrading')

# ── REDIRIGIR print() AL FICHERO DE LOG ───────────────────────────────────
# Los detectores usan print() en lugar de logging. Esta clase Tee escribe
# simultáneamente a la consola (stdout original) y al fichero de log.
class _TeeStream:
    """Duplica escrituras a stdout y al fichero de log."""
    def __init__(self, original_stream, log_path, max_bytes=5*1024*1024):
        self._console   = original_stream
        self._log_path  = log_path
        self._max_bytes = max_bytes

    def write(self, text):
        self._console.write(text)
        self._console.flush()
        try:
            # Rotar si excede tamaño
            if os.path.exists(self._log_path) and os.path.getsize(self._log_path) > self._max_bytes:
                import shutil
                shutil.copyfile(self._log_path, self._log_path + '.1')
                open(self._log_path, 'w').close()
            with open(self._log_path, 'a', encoding='utf-8') as f:
                f.write(text)
        except Exception:
            pass  # Nunca bloquear el bot por un error de log

    def flush(self):
        self._console.flush()

    def reconfigure(self, **kwargs):
        # Compatibilidad con el reconfigure() de sys.stdout
        try:
            self._console.reconfigure(**kwargs)
        except Exception:
            pass

    def __getattr__(self, name):
        return getattr(self._console, name)

sys.stdout = _TeeStream(sys.stdout, _LOG_FILE)
sys.stderr = _TeeStream(sys.stderr, _LOG_FILE)
# ── fin Tee ───────────────────────────────────────────────────────────────

# ── PARCHE: serializar yf.download para evitar contaminación entre threads ──
_yf_original_download = yf.download
from adapters.yf_lock import _yf_lock

def _safe_yf_download(*args, **kwargs):
    with _yf_lock:
        return _yf_original_download(*args, **kwargs)

yf.download = _safe_yf_download
# ── fin parche ──

from api.routes import create_app
from services.orchestrator import iniciar_detectores
from adapters.database import DatabaseManager

# Estado del sistema (compartido entre orchestrator y routes)
estado_sistema = {
    'iniciado': datetime.now().isoformat(),
    'ultima_actividad_cron': None,
    'detectores': {}
}
threads_detectores = {}

# Inicializar tabla de cuotas de API keys (best-effort, no bloquea arranque)
try:
    DatabaseManager().init_api_key_usage_table()
    DatabaseManager().init_ohlcv_table()
    DatabaseManager().init_canal_roto_table()
    DatabaseManager().init_macro_events_log_table()
    DatabaseManager().init_bot_logs_table()
    DatabaseManager().init_historial_precios_table()
    DatabaseManager().init_nivel_touches_tables()
    DatabaseManager().init_senal_analisis_table()
    DatabaseManager().init_antispam_table()
    logger.info("✅ Tablas api_key_usage, ohlcv, canal_roto_state, macro_events_log, bot_logs, nivel_touches, senal_analisis, bot_antispam listas")
except Exception as _e:
    logger.warning(f"⚠️ No se pudo inicializar tablas BD: {_e}")
threads_detectores = {}

# Credenciales de Telegram (para keep-alive alerts)
_TG_TOKEN = os.environ.get('TELEGRAM_TOKEN', '')
_TG_CHAT = os.environ.get('TELEGRAM_CHAT_ID', '')

# Credenciales SMTP (para alertas de fallo de detectores)
_SMTP_HOST = os.environ.get('SMTP_HOST', 'smtp.gmail.com')
_SMTP_PORT = int(os.environ.get('SMTP_PORT', 587))
_SMTP_USER = os.environ.get('SMTP_USER', '')
_SMTP_PASSWORD = os.environ.get('SMTP_PASSWORD', '')
_ALERT_EMAIL = os.environ.get('ALERT_EMAIL', 'yesod3d@gmail.com')

def _enviar_alerta_telegram(mensaje: str):
    """Envía un mensaje de alerta al chat de Telegram (uso interno)."""
    if not _TG_TOKEN or not _TG_CHAT:
        return
    try:
        url = f'https://api.telegram.org/bot{_TG_TOKEN}/sendMessage'
        requests.post(url, json={'chat_id': _TG_CHAT, 'text': mensaje, 'parse_mode': 'HTML'}, timeout=10)
    except Exception:
        pass


def _enviar_email_fallo(fallidos: list):
    """Envía un correo de alerta a _ALERT_EMAIL cuando hay detectores caídos."""
    if not _SMTP_USER or not _SMTP_PASSWORD:
        logger.warning("⚠️ SMTP no configurado — no se puede enviar email de fallo de detectores")
        return
    ahora = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
    cuerpo = (
        f"Bot Trading — Alerta de detectores caídos\n"
        f"Fecha: {ahora} UTC\n\n"
        f"Los siguientes detectores han dejado de estar activos:\n"
        + "\n".join(f"  - {nombre}" for nombre in fallidos)
    )
    try:
        msg = MIMEText(cuerpo, 'plain', 'utf-8')
        msg['Subject'] = f"🚨 Bot Trading: {len(fallidos)} detector(es) caído(s)"
        msg['From'] = _SMTP_USER
        msg['To'] = _ALERT_EMAIL
        context = ssl.create_default_context()
        with smtplib.SMTP(_SMTP_HOST, _SMTP_PORT, timeout=15) as smtp:
            smtp.ehlo()
            smtp.starttls(context=context)
            smtp.login(_SMTP_USER, _SMTP_PASSWORD)
            smtp.sendmail(_SMTP_USER, [_ALERT_EMAIL], msg.as_string())
        logger.info(f"📧 Email de fallo enviado a {_ALERT_EMAIL}: {fallidos}")
    except Exception as e:
        logger.error(f"❌ Error enviando email de fallo de detectores: {e}")


def _enviar_heartbeat():
    """Registra en log el estado de los detectores cada hora.
    Si algún detector ha fallado, envía un email de alerta."""
    activos = sum(1 for t in threads_detectores.values() if t.is_alive())
    total   = len(threads_detectores)
    ahora   = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')
    fallidos = []
    lineas   = []
    for nombre, hilo in threads_detectores.items():
        if hilo.is_alive():
            lineas.append(f"  🟢 {nombre}")
        else:
            lineas.append(f"  🔴 {nombre}")
            fallidos.append(nombre)
    estado_txt = "\n".join(lineas) if lineas else "  ℹ️ Sin detectores registrados"
    logger.info(
        f"💓 Heartbeat Bot Trading | {ahora} UTC | "
        f"Detectores activos: {activos}/{total}\n{estado_txt}"
    )
    if fallidos:
        _enviar_email_fallo(fallidos)

def keep_alive():
    """Mantiene la instancia activa haciendo ping interno cada minuto."""
    logger.info("💚 Keep-alive iniciado")
    time.sleep(120)

    fallos_consecutivos = 0
    UMBRAL_ALERTA = 3
    ultimo_heartbeat = time.time()   # primera notificación después de 1 hora

    while True:
        try:
            port = int(os.environ.get('PORT', 5000))
            url = f"http://localhost:{port}/health"
            response = requests.get(url, timeout=10)
            if response.status_code == 200:
                if fallos_consecutivos >= UMBRAL_ALERTA:
                    logger.info("💚 Keep-alive recuperado después de fallos")
                    _enviar_alerta_telegram("✅ <b>Bot Trading recuperado</b>\nEl servidor vuelve a responder correctamente.")
                fallos_consecutivos = 0
                logger.info("💚 Keep-alive ping OK")
            else:
                fallos_consecutivos += 1
                logger.warning(f"⚠️ Keep-alive ping failed: {response.status_code} (fallo {fallos_consecutivos})")
                if fallos_consecutivos >= UMBRAL_ALERTA:
                    _enviar_alerta_telegram(
                        f"🚨 <b>Bot Trading no responde</b>\n"
                        f"El endpoint /health devolvió {response.status_code} "
                        f"{fallos_consecutivos} veces seguidas."
                    )
        except Exception as e:
            fallos_consecutivos += 1
            logger.warning(f"⚠️ Keep-alive error: {e} (fallo {fallos_consecutivos})")
            if fallos_consecutivos >= UMBRAL_ALERTA:
                _enviar_alerta_telegram(
                    f"🚨 <b>Bot Trading no responde</b>\n"
                    f"Error de conexión {fallos_consecutivos} veces seguidas: <code>{e}</code>"
                )

        # ── Heartbeat horario: enviar estado de detectores a Telegram ──
        ahora_ts = time.time()
        if ahora_ts - ultimo_heartbeat >= 3600:
            try:
                _enviar_heartbeat()
                ultimo_heartbeat = ahora_ts
            except Exception as _hb_e:
                logger.warning(f"⚠️ Heartbeat error: {_hb_e}")

        time.sleep(60)

# Crear app Flask con factory pattern
app = create_app(estado_sistema, threads_detectores)

if __name__ == '__main__':
    print("\n" + "=" * 60)
    print("🌟 INICIANDO BOT TRADING")
    print("=" * 60)

    try:
        iniciar_detectores(estado_sistema, threads_detectores)

        # Keep-alive thread
        hilo_keepalive = threading.Thread(target=keep_alive, name="KeepAlive", daemon=True)
        hilo_keepalive.start()

        print("✅ Detectores y keep-alive iniciados correctamente\n")
    except Exception as e:
        print(f"❌ ERROR FATAL iniciando detectores: {e}")
        import traceback
        traceback.print_exc()
        print("⚠️ El servidor Flask arrancará SIN detectores\n")

    port = int(os.environ.get('PORT', 5000))
    print(f"🌐 Servidor Flask iniciando en puerto {port}...")
    print("=" * 60 + "\n")
    app.run(host='0.0.0.0', port=port, debug=False)
