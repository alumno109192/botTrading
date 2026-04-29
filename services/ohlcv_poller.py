"""
services/ohlcv_poller.py — Poller centralizado de velas OHLCV

Descarga velas de Twelve Data y las persiste en la BD con frecuencia
adaptada por intervalo para minimizar el consumo de quota API.

Flujo:
  1. Al arrancar: fill inicial si la BD está vacía.
  2. Cada N segundos según poll_secs del target → fetch incremental → upsert BD.
  3. Cada 8h: purga de velas antiguas para controlar tamaño de la tabla.

Consumo estimado por target (sesión 06-22 UTC = 16h):
  - GC=F 5m  cada  60s → 960 calls/día  (precio cambia cada minuto)
  - GC=F 4h  cada 1800s →  32 calls/día  (vela dura 4h, no necesita más)
"""
import time
from datetime import datetime, timezone

import logging
logger = logging.getLogger('bottrading')

# ── Activos y configuración ──────────────────────────────────────────────────
# poll_secs: frecuencia de polling por target (respetar cuota API)
POLL_TARGETS = [
    {'ticker_yf': 'GC=F', 'interval': '5m', 'poll_secs':   60, 'max_dias_bd':   8},
    {'ticker_yf': 'GC=F', 'interval': '4h', 'poll_secs': 1800, 'max_dias_bd':  65},
    {'ticker_yf': 'GC=F', 'interval': '1d', 'poll_secs': 3600, 'max_dias_bd': 365},  # 1 req/hora
]

CHECK_INTERVAL = 60   # segundos entre ciclos del bucle principal
_ultimo_purge  = 0    # timestamp del último purge (cada 8h)


def main():
    from adapters.data_provider import poll_ohlcv
    from adapters.database import DatabaseManager

    logger.info("🔄 [ohlcv_poller] Iniciando servicio de polling OHLCV...")

    # Inicializar tabla al arranque
    try:
        db = DatabaseManager()
        db.init_ohlcv_table()
        logger.info("✅ [ohlcv_poller] Tabla ohlcv lista")
    except Exception as e:
        logger.error(f"❌ [ohlcv_poller] Error inicializando tabla ohlcv: {e}")

    # Tracking de último poll por target para respetar poll_secs individuales
    _ultimo_poll: dict = {}  # (ticker_yf, interval) → timestamp float

    ciclo = 0
    global _ultimo_purge

    while True:
        ciclo += 1
        ahora = datetime.now(timezone.utc)
        hora  = ahora.hour

        # Solo operar durante sesión ampliada 06-22 UTC (cubre Asia pre-apertura)
        if 6 <= hora < 22:
            ts_ahora = time.time()
            for target in POLL_TARGETS:
                clave = (target['ticker_yf'], target['interval'])
                ultimo = _ultimo_poll.get(clave, 0)
                if ts_ahora - ultimo < target['poll_secs']:
                    continue  # aún no toca este target
                try:
                    ok = poll_ohlcv(target['ticker_yf'], target['interval'])
                    if ok:
                        _ultimo_poll[clave] = ts_ahora
                    else:
                        logger.warning(f"  ⚠️ [ohlcv_poller] #{ciclo} No se pudo actualizar "
                              f"{target['ticker_yf']} {target['interval']}")
                except Exception as e:
                    logger.error(f"  ❌ [ohlcv_poller] #{ciclo} Excepción: {e}")

            # Purge cada 8h (28800 segundos)
            if ts_ahora - _ultimo_purge > 28800:
                try:
                    db = DatabaseManager()
                    for target in POLL_TARGETS:
                        db.purgar_velas_antiguas(
                            target['ticker_yf'],
                            target['interval'],
                            dias_max=target['max_dias_bd']
                        )
                    _ultimo_purge = ts_ahora
                    logger.info(f"  🧹 [ohlcv_poller] Purge completado")
                except Exception as e:
                    logger.error(f"  ⚠️ [ohlcv_poller] Error en purge: {e}")
        else:
            if ciclo % 30 == 0:  # Log cada 30 min en off-hours
                logger.info(f"  ⏸️ [ohlcv_poller] Fuera de sesión ({hora}h UTC)")

        time.sleep(CHECK_INTERVAL)
