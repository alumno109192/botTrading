"""
services/orchestrator.py — Orquestador de detectores en threads

Reemplaza ~300 líneas de código comentado en app.py con un registro
declarativo de detectores. Activar/desactivar = cambiar enabled.
"""
import importlib
import threading
import time
import logging
from datetime import datetime

logger = logging.getLogger('bottrading')


# ═══════════════════════════════════════════════════════════
# REGISTRO DE DETECTORES
# enabled: True → se arranca | False → pausado
# ═══════════════════════════════════════════════════════════
DETECTOR_REGISTRY = {
    # ── GOLD (activos) ──────────────────────────────────────
    'gold_1d':  {'module': 'detectors.gold.detector_gold_1d',  'label': 'DETECTOR GOLD 1D',  'enabled': True},
    'gold_4h':  {'module': 'detectors.gold.detector_gold_4h',  'label': 'DETECTOR GOLD 4H',  'enabled': True},
    'gold_1h':  {'module': 'detectors.gold.detector_gold_1h',  'label': 'DETECTOR GOLD 1H',  'enabled': True},
    'gold_15m': {'module': 'detectors.gold.detector_gold_15m', 'label': 'DETECTOR GOLD 15M', 'enabled': True},
    'gold_5m':  {'module': 'detectors.gold.detector_gold_5m',  'label': 'DETECTOR GOLD 5M',  'enabled': True},
    # ── BITCOIN (pausados) ──────────────────────────────────
    'bitcoin_1d': {'module': 'detectors.bitcoin.detector_bitcoin_1d', 'label': 'DETECTOR BITCOIN 1D', 'enabled': False},
    'bitcoin_4h': {'module': 'detectors.bitcoin.detector_bitcoin_4h', 'label': 'DETECTOR BITCOIN 4H', 'enabled': False},
    # ── SPX (pausados) ──────────────────────────────────────
    'spx_1d':  {'module': 'detectors.spx.detector_spx_1d',  'label': 'DETECTOR SPX 1D',  'enabled': False},
    'spx_4h':  {'module': 'detectors.spx.detector_spx_4h',  'label': 'DETECTOR SPX 4H',  'enabled': False},
    'spx_15m': {'module': 'detectors.spx.detector_spx_15m', 'label': 'DETECTOR SPX 15M', 'enabled': False},
    # ── EURUSD (pausados) ───────────────────────────────────
    'eurusd_1d':  {'module': 'detectors.eurusd.detector_eurusd_1d',  'label': 'DETECTOR EURUSD 1D',  'enabled': False},
    'eurusd_4h':  {'module': 'detectors.eurusd.detector_eurusd_4h',  'label': 'DETECTOR EURUSD 4H',  'enabled': False},
    'eurusd_15m': {'module': 'detectors.eurusd.detector_eurusd_15m', 'label': 'DETECTOR EURUSD 15M', 'enabled': False},
    # ── NASDAQ (pausados) ───────────────────────────────────
    'nasdaq_1d': {'module': 'detectors.nasdaq.detector_nasdaq_1d', 'label': 'DETECTOR NAS100 1D', 'enabled': False},
    'nasdaq_4h': {'module': 'detectors.nasdaq.detector_nasdaq_4h', 'label': 'DETECTOR NAS100 4H', 'enabled': False},
    # ── WTI (pausados) ──────────────────────────────────────
    'wti_1d': {'module': 'detectors.wti.detector_wti_1d', 'label': 'DETECTOR WTI 1D', 'enabled': False},
    'wti_4h': {'module': 'detectors.wti.detector_wti_4h', 'label': 'DETECTOR WTI 4H', 'enabled': False},
    # ── SILVER (pausados) ───────────────────────────────────
    'silver_1d': {'module': 'detectors.silver.detector_silver_1d', 'label': 'DETECTOR SILVER 1D', 'enabled': False},
    'silver_4h': {'module': 'detectors.silver.detector_silver_4h', 'label': 'DETECTOR SILVER 4H', 'enabled': False},
}

# Servicios auxiliares (siempre activos)
SERVICE_REGISTRY = {
    'monitor':  {'module': 'services.signal_monitor',  'label': 'MONITOR SEÑALES'},
    'noticias': {'module': 'services.news_monitor',     'label': 'NOTICIAS GOLD'},
}


def _ejecutar_modulo(nombre, modulo, clave_estado, estado_sistema):
    """Ejecuta un módulo en un bucle de reintentos (sin recursión)."""
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
            time.sleep(60)
            logger.info(f"🔄 Reintentando {nombre}...")


def iniciar_detectores(estado_sistema, threads_detectores):
    """Inicia todos los detectores habilitados + servicios auxiliares."""
    hilos = []
    activos = {k: v for k, v in DETECTOR_REGISTRY.items() if v['enabled']}

    print("=" * 60)
    print("🥇 GOLD SIGNAL BOT — Clean Architecture")
    print("=" * 60)
    print(f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()
    print(f"📊 Detectores activos: {len(activos)}")
    for key in activos:
        print(f"  🔹 {activos[key]['label']}")
    print(f"📊 Servicios: {len(SERVICE_REGISTRY)}")
    for key in SERVICE_REGISTRY:
        print(f"  🔹 {SERVICE_REGISTRY[key]['label']}")
    print("=" * 60)
    print()

    # ── Detectores ──────────────────────────────────────────
    for clave, config in DETECTOR_REGISTRY.items():
        if not config['enabled']:
            continue
        try:
            modulo = importlib.import_module(config['module'])
            estado_sistema['detectores'][clave] = 'iniciando'
            hilo = threading.Thread(
                target=_ejecutar_modulo,
                args=(config['label'], modulo, clave, estado_sistema),
                name=config['label'].replace(' ', ''),
                daemon=True,
            )
            hilos.append(hilo)
            threads_detectores[clave] = hilo
            print(f"  ✓ Thread {config['label']} creado")
        except Exception as e:
            print(f"  ✗ Error creando {config['label']}: {e}")

    # ── Servicios auxiliares ────────────────────────────────
    for clave, config in SERVICE_REGISTRY.items():
        try:
            modulo = importlib.import_module(config['module'])
            estado_sistema['detectores'][clave] = 'iniciando'
            hilo = threading.Thread(
                target=_ejecutar_modulo,
                args=(config['label'], modulo, clave, estado_sistema),
                name=config['label'].replace(' ', ''),
                daemon=True,
            )
            hilos.append(hilo)
            threads_detectores[clave] = hilo
            print(f"  ✓ Thread {config['label']} creado")
        except Exception as e:
            print(f"  ✗ Error creando {config['label']}: {e}")

    # ── Iniciar todos (escalonado 2s) ───────────────────────
    print(f"\n🚀 Iniciando {len(hilos)} threads...")
    for i, hilo in enumerate(hilos, 1):
        try:
            hilo.start()
            print(f"  [{i}/{len(hilos)}] ✓ {hilo.name} iniciado")
            time.sleep(2)
        except Exception as e:
            print(f"  [{i}/{len(hilos)}] ✗ Error iniciando {hilo.name}: {e}")

    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] ✅ Proceso de inicio completado")
    print(f"[{datetime.now().strftime('%H:%M:%S')}] 📊 Total threads: {len(threads_detectores)}")
