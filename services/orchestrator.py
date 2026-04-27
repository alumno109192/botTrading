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
    # ── GOLD ───────────────────────────────────────────────
    'gold_1d':  {'module': 'detectors.gold.detector_gold_1d',  'label': 'DETECTOR GOLD 1D',  'enabled': True},
    'gold_4h':  {'module': 'detectors.gold.detector_gold_4h',  'label': 'DETECTOR GOLD 4H',  'enabled': True},
    'gold_1h':  {'module': 'detectors.gold.detector_gold_1h',  'label': 'DETECTOR GOLD 1H',  'enabled': True},
    'gold_15m': {'module': 'detectors.gold.detector_gold_15m', 'label': 'DETECTOR GOLD 15M', 'enabled': True},
    'gold_5m':  {'module': 'detectors.gold.detector_gold_5m',  'label': 'DETECTOR GOLD 5M',  'enabled': True},
}

# Servicios auxiliares (siempre activos)
SERVICE_REGISTRY = {
    'ohlcv_poller': {'module': 'services.ohlcv_poller',       'label': 'OHLCV POLLER'},
    'monitor':      {'module': 'services.signal_monitor',      'label': 'MONITOR SEÑALES'},
    'noticias':     {'module': 'services.news_monitor',        'label': 'NOTICIAS GOLD'},
    'backtest':     {'module': 'services.backtest_service',    'label': 'BACKTEST MENSUAL'},
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

    logger.info("=" * 60)
    logger.info("🥇 GOLD SIGNAL BOT — Clean Architecture")
    logger.info("=" * 60)
    logger.info(f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    logger.info(f"📊 Detectores activos: {len(activos)}")
    for key in activos:
        logger.info(f"  🔹 {activos[key]['label']}")
    logger.info(f"📊 Servicios: {len(SERVICE_REGISTRY)}")
    for key in SERVICE_REGISTRY:
        logger.info(f"  🔹 {SERVICE_REGISTRY[key]['label']}")
    logger.info("=" * 60)


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
            logger.info(f"  ✓ Thread {config['label']} creado")
        except Exception as e:
            logger.error(f"  ✗ Error creando {config['label']}: {e}")

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
            logger.info(f"  ✓ Thread {config['label']} creado")
        except Exception as e:
            logger.error(f"  ✗ Error creando {config['label']}: {e}")

    # ── Iniciar todos (escalonado 2s) ───────────────────────
    logger.info(f"\n🚀 Iniciando {len(hilos)} threads...")
    for i, hilo in enumerate(hilos, 1):
        try:
            hilo.start()
            logger.info(f"  [{i}/{len(hilos)}] ✓ {hilo.name} iniciado")
            time.sleep(2)
        except Exception as e:
            logger.error(f"  [{i}/{len(hilos)}] ✗ Error iniciando {hilo.name}: {e}")

    logger.info(f"\n[{datetime.now().strftime('%H:%M:%S')}] ✅ Proceso de inicio completado")
    logger.info(f"[{datetime.now().strftime('%H:%M:%S')}] 📊 Total threads: {len(threads_detectores)}")


def reiniciar_detector(clave: str, estado_sistema: dict, threads_detectores: dict) -> bool:
    """
    Reinicia un thread (detector o servicio) que ha muerto.

    Busca la clave en DETECTOR_REGISTRY y SERVICE_REGISTRY, reimporta el módulo
    y lanza un nuevo thread daemon en modo reintento.

    Returns:
        True si el thread se reinició correctamente, False en caso de error.
    """
    config = DETECTOR_REGISTRY.get(clave) or SERVICE_REGISTRY.get(clave)
    if config is None:
        logger.warning(f"⚠️ reiniciar_detector: clave desconocida '{clave}'")
        return False

    try:
        modulo = importlib.import_module(config['module'])
        estado_sistema['detectores'][clave] = 'reiniciando'
        hilo = threading.Thread(
            target=_ejecutar_modulo,
            args=(config['label'], modulo, clave, estado_sistema),
            name=config['label'].replace(' ', ''),
            daemon=True,
        )
        hilo.start()
        threads_detectores[clave] = hilo
        logger.info(f"🔄 Thread '{config['label']}' reiniciado automáticamente")
        return True
    except Exception as e:
        logger.error(f"❌ Error reiniciando '{config['label']}': {e}")
        estado_sistema['detectores'][clave] = f'error_restart: {str(e)}'
        return False

