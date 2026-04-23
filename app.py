"""
Bot Trading - Web Service con detectores en background
Servidor Flask que mantiene vivo el servicio en Render
Los detectores se ejecutan en threads separados
"""

import threading
import time
from datetime import datetime
import os
import requests
import sys
import logging
from logging.handlers import RotatingFileHandler
import yfinance as yf

# Forzar flush inmediato de logs (crítico para Render)
sys.stdout.reconfigure(line_buffering=True)

# ── LOGGING PERSISTENTE ────────────────────────────────────────────────────
_log_handler = RotatingFileHandler(
    'logfile.txt', maxBytes=5 * 1024 * 1024, backupCount=3, encoding='utf-8'
)
_log_handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
logging.basicConfig(
    level=logging.INFO,
    handlers=[logging.StreamHandler(sys.stdout), _log_handler],
)
logger = logging.getLogger('bottrading')

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
    logger.info("✅ Tablas api_key_usage, ohlcv, canal_roto_state, macro_events_log y bot_logs listas")
except Exception as _e:
    logger.warning(f"⚠️ No se pudo inicializar tablas BD: {_e}")
threads_detectores = {}

# Credenciales de Telegram (para keep-alive alerts)
_TG_TOKEN = os.environ.get('TELEGRAM_TOKEN', '')
_TG_CHAT = os.environ.get('TELEGRAM_CHAT_ID', '')

def _enviar_alerta_telegram(mensaje: str):
    """Envía un mensaje de alerta al chat de Telegram (uso interno)."""
    if not _TG_TOKEN or not _TG_CHAT:
        return
    try:
        url = f'https://api.telegram.org/bot{_TG_TOKEN}/sendMessage'
        requests.post(url, json={'chat_id': _TG_CHAT, 'text': mensaje, 'parse_mode': 'HTML'}, timeout=10)
    except Exception:
        pass

def keep_alive():
    """Mantiene la instancia activa haciendo ping interno cada minuto."""
    logger.info("💚 Keep-alive iniciado")
    time.sleep(120)

    fallos_consecutivos = 0
    UMBRAL_ALERTA = 3

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
