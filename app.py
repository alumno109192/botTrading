"""
Bot Trading - Web Service con detectores en background
Servidor Flask que mantiene vivo el servicio en Render
Los detectores se ejecutan en threads separados
"""

from flask import Flask, jsonify, request
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
# yfinance comparte estado global interno; sin lock, close/high/low de un
# símbolo puede aparecer en otro detector que corre simultáneamente.
_yf_original_download = yf.download
_yf_lock = threading.Lock()

def _safe_yf_download(*args, **kwargs):
    with _yf_lock:
        return _yf_original_download(*args, **kwargs)

yf.download = _safe_yf_download
# ── fin parche ──

# Importar los módulos de los detectores organizados
# ── PAUSADOS ──────────────────────────────────────────────
# from detectors.bitcoin import detector_bitcoin_1d
# from detectors.bitcoin import detector_bitcoin_4h
# from detectors.spx import detector_spx_1d
# from detectors.spx import detector_spx_4h
# from detectors.spx import detector_spx_15m
# from detectors.nasdaq import detector_nasdaq_1d
# from detectors.nasdaq import detector_nasdaq_4h
# from detectors.wti import detector_wti_1d
# from detectors.wti import detector_wti_4h
# from detectors.silver import detector_silver_1d
# from detectors.silver import detector_silver_4h
# ── ACTIVOS ───────────────────────────────────────────────
from detectors.gold import detector_gold_1d
from detectors.gold import detector_gold_4h
from detectors.gold import detector_gold_1h
from detectors.gold import detector_gold_15m
from detectors.gold import detector_gold_5m
# ── PAUSADO: EURUSD ───────────────────────────────────────
# from detectors.eurusd import detector_eurusd_1d
# from detectors.eurusd import detector_eurusd_4h
# from detectors.eurusd import detector_eurusd_15m
import signal_monitor
import gold_news_monitor

app = Flask(__name__)

# Estado del sistema
estado_sistema = {
    'iniciado': datetime.now().isoformat(),
    'ultima_actividad_cron': None,
    'detectores': {
        'gold_1d': 'iniciando',
        'gold_4h': 'iniciando',
        'gold_1h': 'iniciando',
        'gold_15m': 'iniciando',
        'gold_5m': 'iniciando',
        'eurusd_1d': 'iniciando',
        'eurusd_4h': 'iniciando',
        'eurusd_15m': 'iniciando',
        'monitor': 'iniciando',
        'noticias': 'iniciando',
    }
}

# Referencias a los threads para monitoreo
threads_detectores = {}

# Token para proteger el endpoint /cron (configura CRON_TOKEN en .env)
CRON_TOKEN = os.environ.get('CRON_TOKEN', '')

# Credenciales de Telegram (reutilizadas de los detectores)
_TG_TOKEN  = os.environ.get('TELEGRAM_BOT_TOKEN', '')
_TG_CHAT   = os.environ.get('TELEGRAM_CHAT_ID', '')

def _enviar_alerta_telegram(mensaje: str):
    """Envía un mensaje de alerta al chat de Telegram (uso interno)."""
    if not _TG_TOKEN or not _TG_CHAT:
        return
    try:
        url = f'https://api.telegram.org/bot{_TG_TOKEN}/sendMessage'
        requests.post(url, json={'chat_id': _TG_CHAT, 'text': mensaje, 'parse_mode': 'HTML'}, timeout=10)
    except Exception:
        pass  # no propagar errores de alerta

def ejecutar_detector(nombre, modulo, clave_estado):
    """Ejecuta un detector en un bucle de reintentos (sin recursión)."""
    while True:
        try:
            logger.info(f"🔵 Iniciando {nombre}...")
            estado_sistema['detectores'][clave_estado] = 'activo'
            modulo.main()
        except KeyboardInterrupt:
            logger.info(f"⚠️ {nombre} detenido por usuario")
            estado_sistema['detectores'][clave_estado] = 'detenido'
            break
        except Exception as e:
            logger.error(f"❌ Error en {nombre}: {e}")
            estado_sistema['detectores'][clave_estado] = f'error: {str(e)}'
            # Reintentar en 60 segundos
            time.sleep(60)
            logger.info(f"🔄 Reintentando {nombre}...")

def keep_alive():
    """Mantiene la instancia activa haciendo ping interno cada minuto.
    Envía alerta a Telegram si el bot deja de responder 3 veces seguidas."""
    logger.info("💚 Keep-alive iniciado")
    time.sleep(120)  # Esperar 2 minutos al inicio para que Flask esté listo

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

def iniciar_detectores():
    """Inicia todos los detectores en background"""
    print("="*60)
    print("🥇 GOLD SIGNAL BOT — XAUUSD ONLY")
    print("="*60)
    print(f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()
    print("📊 Detectores activos — XAUUSD (Gold):")
    print("  🥇 1D  → Swing (señales fuertes, R:R ≥ 2.5)")
    print("  🥇 4H  → Swing (R:R ≥ 2.0, score ≥ 12)")
    print("  🥇 1H  → Intraday (R:R ≥ 2.0, sesión 07-17 UTC)")
    print("  🥇 15M → Scalping (R:R ≥ 1.5, sesión 07-17 UTC)")
    print("  🥇 5M  → Micro-Scalp (R:R ≥ 1.5, sesión 07-17 UTC)")
    print("  🔍 MONITOR → Tracking TP/SL")
    print("  📊 TOTAL: 6 threads activos")
    print("="*60)
    print()
    
    # Crear hilos para cada detector
    hilos = []
    
    print("🔧 Creando threads...")
    print()
    print("  📊 DETECTORES TIMEFRAME 1D (Diario):")
    
    # ── PAUSADO: Bitcoin 1D ──────────────────────────────────
    # hilo_btc_1d = threading.Thread(
    #     target=ejecutar_detector,
    #     args=("DETECTOR BITCOIN 1D", detector_bitcoin_1d, "bitcoin_1d"),
    #     name="DetectorBitcoin1D", daemon=True)
    # hilos.append(hilo_btc_1d); threads_detectores['bitcoin_1d'] = hilo_btc_1d
    
    try:
        # Gold 1D
        print("  📦 Creando thread: Gold 1D...")
        hilo_gold_1d = threading.Thread(
            target=ejecutar_detector,
            args=("DETECTOR GOLD 1D", detector_gold_1d, "gold_1d"),
            name="DetectorGold1D",
            daemon=True
        )
        hilos.append(hilo_gold_1d)
        threads_detectores['gold_1d'] = hilo_gold_1d
        print("    ✓ Thread Gold 1D creado")
    except Exception as e:
        print(f"    ✗ Error creando thread Gold 1D: {e}")
    
    # ── PAUSADO: SPX 1D ──────────────────────────────────────
    # hilo_spx_1d = threading.Thread(
    #     target=ejecutar_detector,
    #     args=("DETECTOR SPX 1D", detector_spx_1d, "spx_1d"),
    #     name="DetectorSPX1D", daemon=True)
    # hilos.append(hilo_spx_1d); threads_detectores['spx_1d'] = hilo_spx_1d
    
    print()
    print("  📊 DETECTORES TIMEFRAME 4H (4 Horas):")
    
    # ── PAUSADO: Bitcoin 4H ──────────────────────────────────
    # hilo_btc_4h = threading.Thread(
    #     target=ejecutar_detector,
    #     args=("DETECTOR BITCOIN 4H", detector_bitcoin_4h, "bitcoin_4h"),
    #     name="DetectorBitcoin4H", daemon=True)
    # hilos.append(hilo_btc_4h); threads_detectores['bitcoin_4h'] = hilo_btc_4h
    
    try:
        # Gold 4H
        print("  📦 Creando thread: Gold 4H...")
        hilo_gold_4h = threading.Thread(
            target=ejecutar_detector,
            args=("DETECTOR GOLD 4H", detector_gold_4h, "gold_4h"),
            name="DetectorGold4H",
            daemon=True
        )
        hilos.append(hilo_gold_4h)
        threads_detectores['gold_4h'] = hilo_gold_4h
        print("    ✓ Thread Gold 4H creado")
    except Exception as e:
        print(f"    ✗ Error creando thread Gold 4H: {e}")

    try:
        # Gold 1H (Intradía)
        print("  📦 Creando thread: Gold 1H (Intradía)...")
        hilo_gold_1h = threading.Thread(
            target=ejecutar_detector,
            args=("DETECTOR GOLD 1H", detector_gold_1h, "gold_1h"),
            name="DetectorGold1H",
            daemon=True
        )
        hilos.append(hilo_gold_1h)
        threads_detectores['gold_1h'] = hilo_gold_1h
        print("    ✓ Thread Gold 1H creado")
    except Exception as e:
        print(f"    ✗ Error creando thread Gold 1H: {e}")

    # ── PAUSADO: SPX 4H ──────────────────────────────────────
    # hilo_spx_4h = threading.Thread(
    #     target=ejecutar_detector,
    #     args=("DETECTOR SPX 4H", detector_spx_4h, "spx_4h"),
    #     name="DetectorSPX4H", daemon=True)
    # hilos.append(hilo_spx_4h); threads_detectores['spx_4h'] = hilo_spx_4h

    # ── PAUSADO: NAS100 1D ───────────────────────────────────
    # hilo_nas_1d = threading.Thread(
    #     target=ejecutar_detector,
    #     args=("DETECTOR NAS100 1D", detector_nasdaq_1d, "nasdaq_1d"),
    #     name="DetectorNAS1001D", daemon=True)
    # hilos.append(hilo_nas_1d); threads_detectores['nasdaq_1d'] = hilo_nas_1d

    # ── PAUSADO: NAS100 4H ───────────────────────────────────
    # hilo_nas_4h = threading.Thread(
    #     target=ejecutar_detector,
    #     args=("DETECTOR NAS100 4H", detector_nasdaq_4h, "nasdaq_4h"),
    #     name="DetectorNAS1004H", daemon=True)
    # hilos.append(hilo_nas_4h); threads_detectores['nasdaq_4h'] = hilo_nas_4h

    # ── PAUSADO: EURUSD 1D ──────────────────────────────────
    # hilo_eur_1d = threading.Thread(
    #     target=ejecutar_detector,
    #     args=("DETECTOR EURUSD 1D", detector_eurusd_1d, "eurusd_1d"),
    #     name="DetectorEURUSD1D", daemon=True)
    # hilos.append(hilo_eur_1d); threads_detectores['eurusd_1d'] = hilo_eur_1d

    # ── PAUSADO: EURUSD 4H ──────────────────────────────────
    # hilo_eur_4h = threading.Thread(
    #     target=ejecutar_detector,
    #     args=("DETECTOR EURUSD 4H", detector_eurusd_4h, "eurusd_4h"),
    #     name="DetectorEURUSD4H", daemon=True)
    # hilos.append(hilo_eur_4h); threads_detectores['eurusd_4h'] = hilo_eur_4h

    # ── PAUSADO: WTI 1D ──────────────────────────────────────
    # hilo_wti_1d = threading.Thread(
    #     target=ejecutar_detector,
    #     args=("DETECTOR WTI 1D", detector_wti_1d, "wti_1d"),
    #     name="DetectorWTI1D", daemon=True)
    # hilos.append(hilo_wti_1d); threads_detectores['wti_1d'] = hilo_wti_1d

    # ── PAUSADO: WTI 4H ──────────────────────────────────────
    # hilo_wti_4h = threading.Thread(
    #     target=ejecutar_detector,
    #     args=("DETECTOR WTI 4H", detector_wti_4h, "wti_4h"),
    #     name="DetectorWTI4H", daemon=True)
    # hilos.append(hilo_wti_4h); threads_detectores['wti_4h'] = hilo_wti_4h

    # ── PAUSADO: Silver 1D ───────────────────────────────────
    # hilo_sil_1d = threading.Thread(
    #     target=ejecutar_detector,
    #     args=("DETECTOR SILVER 1D", detector_silver_1d, "silver_1d"),
    #     name="DetectorSilver1D", daemon=True)
    # hilos.append(hilo_sil_1d); threads_detectores['silver_1d'] = hilo_sil_1d

    # ── PAUSADO: Silver 4H ───────────────────────────────────
    # hilo_sil_4h = threading.Thread(
    #     target=ejecutar_detector,
    #     args=("DETECTOR SILVER 4H", detector_silver_4h, "silver_4h"),
    #     name="DetectorSilver4H", daemon=True)
    # hilos.append(hilo_sil_4h); threads_detectores['silver_4h'] = hilo_sil_4h

    print()
    print("  📊 DETECTORES TIMEFRAME 15M (Scalping):")

    try:
        # Gold 15M Scalping
        print("  📦 Creando thread: Gold 15M Scalping...")
        hilo_gold_15m = threading.Thread(
            target=ejecutar_detector,
            args=("DETECTOR GOLD 15M", detector_gold_15m, "gold_15m"),
            name="DetectorGold15M",
            daemon=True
        )
        hilos.append(hilo_gold_15m)
        threads_detectores['gold_15m'] = hilo_gold_15m
        print("    ✓ Thread Gold 15M creado")
    except Exception as e:
        print(f"    ✗ Error creando thread Gold 15M: {e}")

    try:
        # Gold 5M Micro-Scalping
        print("  📦 Creando thread: Gold 5M Micro-Scalp...")
        hilo_gold_5m = threading.Thread(
            target=ejecutar_detector,
            args=("DETECTOR GOLD 5M", detector_gold_5m, "gold_5m"),
            name="DetectorGold5M",
            daemon=True
        )
        hilos.append(hilo_gold_5m)
        threads_detectores['gold_5m'] = hilo_gold_5m
        print("    ✓ Thread Gold 5M creado")
    except Exception as e:
        print(f"    ✗ Error creando thread Gold 5M: {e}")

    # ── PAUSADO: SPX 15M ─────────────────────────────────────
    # hilo_spx_15m = threading.Thread(
    #     target=ejecutar_detector,
    #     args=("DETECTOR SPX 15M", detector_spx_15m, "spx_15m"),
    #     name="DetectorSPX15M", daemon=True)
    # hilos.append(hilo_spx_15m); threads_detectores['spx_15m'] = hilo_spx_15m

    # ── PAUSADO: EURUSD 15M ─────────────────────────────────
    # hilo_eur_15m = threading.Thread(
    #     target=ejecutar_detector,
    #     args=("DETECTOR EURUSD 15M", detector_eurusd_15m, "eurusd_15m"),
    #     name="DetectorEURUSD15M", daemon=True)
    # hilos.append(hilo_eur_15m); threads_detectores['eurusd_15m'] = hilo_eur_15m

    print()

    print("  📊 OTROS SERVICIOS:")
    
    try:
        # Hilo para monitor de señales
        print("  📦 Creando thread: Monitor...")
        hilo_monitor = threading.Thread(
            target=ejecutar_detector,
            args=("MONITOR SEÑALES", signal_monitor, "monitor"),
            name="SignalMonitor",
            daemon=True
        )
        hilos.append(hilo_monitor)
        threads_detectores['monitor'] = hilo_monitor
        print("    ✓ Thread MONITOR creado")
    except Exception as e:
        print(f"    ✗ Error creando thread MONITOR: {e}")

    try:
        # Hilo para monitor de noticias gold (análisis fundamental RSS)
        print("  📦 Creando thread: Noticias Gold...")
        hilo_noticias = threading.Thread(
            target=ejecutar_detector,
            args=("NOTICIAS GOLD", gold_news_monitor, "noticias"),
            name="GoldNewsMonitor",
            daemon=True
        )
        hilos.append(hilo_noticias)
        threads_detectores['noticias'] = hilo_noticias
        print("    ✓ Thread NOTICIAS GOLD creado")
    except Exception as e:
        print(f"    ✗ Error creando thread NOTICIAS GOLD: {e}")

    try:
        # Hilo para keep-alive (evita que Render duerma la instancia)
        print("  📦 Creando thread: Keep-alive...")
        hilo_keepalive = threading.Thread(
            target=keep_alive,
            name="KeepAlive",
            daemon=True
        )
        hilos.append(hilo_keepalive)
        print("    ✓ Thread KEEP-ALIVE creado")
    except Exception as e:
        print(f"    ✗ Error creando thread KEEP-ALIVE: {e}")
    
    # Iniciar todos los hilos
    print(f"\n🚀 Iniciando {len(hilos)} threads...")
    print()
    for i, hilo in enumerate(hilos, 1):
        try:
            hilo.start()
            print(f"  [{i}/{len(hilos)}] ✓ {hilo.name} iniciado")
            time.sleep(1)  # Reducido a 1 segundo entre threads
        except Exception as e:
            print(f"  [{i}/{len(hilos)}] ✗ Error iniciando {hilo.name}: {e}")
    
    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] ✅ Proceso de inicio completado")
    print(f"[{datetime.now().strftime('%H:%M:%S')}] 🥇 XAUUSD: 1D + 4H + 1H + 15M + 5M")
    print(f"[{datetime.now().strftime('%H:%M:%S')}] 📊 Otros: Monitor + Keep-alive")
    print(f"[{datetime.now().strftime('%H:%M:%S')}] 📊 Total threads: {len(threads_detectores)} detectores + 1 keep-alive")
    print(f"[{datetime.now().strftime('%H:%M:%S')}] 💚 Keep-alive activo (ping cada 1 min)\n")
    sys.stdout.flush()

# ========================================
# RUTAS FLASK
# ========================================

@app.route('/')
def home():
    """Endpoint principal - Health check"""
    return jsonify({
        'status': 'online',
        'servicio': 'Bot Trading - Detectores de Señales',
        'iniciado': estado_sistema['iniciado'],
        'detectores': estado_sistema['detectores']
    })

@app.route('/health')
def health():
    """Health check para Render"""
    return jsonify({'status': 'healthy'}), 200

@app.route('/status')
def status():
    """Estado detallado del sistema"""
    return jsonify(estado_sistema)

@app.route('/cron')
def cron_ping():
    # Verificar token si está configurado
    if CRON_TOKEN:
        token = request.headers.get('X-Cron-Token') or request.args.get('token', '')
        if token != CRON_TOKEN:
            return jsonify({'error': 'Unauthorized'}), 401
    """Endpoint para CRON jobs - Mantiene el servicio activo y verifica threads"""
    ahora = datetime.now()
    estado_sistema['ultima_actividad_cron'] = ahora.isoformat()
    
    # Verificar si hay threads registrados
    if not threads_detectores:
        print(f"[{ahora.strftime('%H:%M:%S')}] 🔔 CRON ping recibido - ⚠️ NO HAY THREADS REGISTRADOS (error en inicialización)")
        sys.stdout.flush()
        return jsonify({
            'status': 'alive_sin_detectores',
            'timestamp': ahora.isoformat(),
            'error': 'No hay threads de detectores registrados',
            'threads': {},
            'threads_activos': 0,
            'threads_totales': 0,
            'alerta': 'Sistema arrancó sin detectores - revisar logs de inicio'
        }), 200
    
    # Verificar estado de threads
    threads_vivos = {}
    threads_muertos = []
    
    for nombre, thread in threads_detectores.items():
        if thread.is_alive():
            threads_vivos[nombre] = 'vivo'
        else:
            threads_vivos[nombre] = 'muerto'
            threads_muertos.append(nombre)
    
    # Log de actividad
    print(f"[{ahora.strftime('%H:%M:%S')}] 🔔 CRON ping recibido - Threads vivos: {len([t for t in threads_vivos.values() if t == 'vivo'])}/{len(threads_detectores)}")
    sys.stdout.flush()  # Forzar flush inmediato para Render
    
    if threads_muertos:
        print(f"[{ahora.strftime('%H:%M:%S')}] ⚠️ Threads muertos detectados: {', '.join(threads_muertos)}")
        sys.stdout.flush()
    
    return jsonify({
        'status': 'alive',
        'timestamp': ahora.isoformat(),
        'threads': threads_vivos,
        'threads_activos': len([t for t in threads_vivos.values() if t == 'vivo']),
        'threads_totales': len(threads_detectores),
        'alerta': 'Hay threads muertos' if threads_muertos else None,
        'detectores': estado_sistema['detectores']
    }), 200

# ========================================
# INICIO DE LA APLICACIÓN
# ========================================

if __name__ == '__main__':
    print("\n" + "="*60)
    print("🌟 INICIANDO BOT TRADING")
    print("="*60)
    
    try:
        # Iniciar detectores en background
        print("📦 Iniciando detectores en background...")
        iniciar_detectores()
        print("✅ Detectores iniciados correctamente\n")
    except Exception as e:
        print(f"❌ ERROR FATAL iniciando detectores: {e}")
        import traceback
        traceback.print_exc()
        print("⚠️ El servidor Flask arrancará SIN detectores\n")
    
    # Iniciar servidor Flask
    port = int(os.environ.get('PORT', 5000))
    print(f"🌐 Servidor Flask iniciando en puerto {port}...")
    print("="*60 + "\n")
    app.run(host='0.0.0.0', port=port, debug=False)
