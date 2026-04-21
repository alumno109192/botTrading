"""
services/ohlcv_poller.py — Poller centralizado de velas OHLCV

Descarga velas de Twelve Data cada 60 segundos y las persiste en la BD.
Los detectores leen de BD en lugar de llamar a la API individualmente,
lo que reduce el consumo de cuota de ~1800 a ~1450 llamadas/día.

Flujo:
  1. Al arrancar: fill inicial de 7 días si la BD está vacía.
  2. Cada 60s (sesión 06-22 UTC): fetch incremental de 1 día → upsert en BD.
  3. Cada 8h: purga de velas > 8 días para controlar el tamaño de la tabla.
"""
import time
from datetime import datetime, timezone

# ── Activos y configuración ──────────────────────────────────────────────────
# interval: intervalo que se almacena en BD (siempre 5m para Gold intraday)
# poll_secs: frecuencia de polling
POLL_TARGETS = [
    {'ticker_yf': 'GC=F', 'interval': '5m', 'poll_secs': 60, 'max_dias_bd': 8},
]

CHECK_INTERVAL = 60   # segundos entre ciclos del bucle principal
_ultimo_purge  = 0    # timestamp del último purge (cada 8h)


def main():
    from adapters.data_provider import poll_ohlcv
    from adapters.database import DatabaseManager

    print("🔄 [ohlcv_poller] Iniciando servicio de polling OHLCV...")

    # Inicializar tabla al arranque
    try:
        db = DatabaseManager()
        db.init_ohlcv_table()
        print("✅ [ohlcv_poller] Tabla ohlcv lista")
    except Exception as e:
        print(f"❌ [ohlcv_poller] Error inicializando tabla ohlcv: {e}")

    ciclo = 0
    global _ultimo_purge

    while True:
        ciclo += 1
        ahora = datetime.now(timezone.utc)
        hora  = ahora.hour

        # Solo operar durante sesión ampliada 06-22 UTC (cubre Asia pre-apertura)
        if 6 <= hora < 22:
            for target in POLL_TARGETS:
                try:
                    ok = poll_ohlcv(target['ticker_yf'], target['interval'])
                    if not ok:
                        print(f"  ⚠️ [ohlcv_poller] #{ciclo} No se pudo actualizar "
                              f"{target['ticker_yf']} {target['interval']}")
                except Exception as e:
                    print(f"  ❌ [ohlcv_poller] #{ciclo} Excepción: {e}")

            # Purge cada 8h (28800 segundos)
            ts_ahora = time.time()
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
                    print(f"  🧹 [ohlcv_poller] Purge completado")
                except Exception as e:
                    print(f"  ⚠️ [ohlcv_poller] Error en purge: {e}")
        else:
            if ciclo % 30 == 0:  # Log cada 30 min en off-hours
                print(f"  ⏸️ [ohlcv_poller] Fuera de sesión ({hora}h UTC)")

        time.sleep(CHECK_INTERVAL)
