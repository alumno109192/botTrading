"""Script para caducar señales ACTIVA que superaron su vigencia máxima por TF."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from adapters.database import get_db
from services.signal_monitor import cerrar_senales_antiguas, _MAX_VIGENCIA_ACTIVA_HORAS
from datetime import datetime, timezone

db = get_db()

# Mostrar estado ANTES
from adapters.database import get_db as _gdb
senales_antes = db.obtener_senales_activas()
print(f"\nAntes: {len(senales_antes)} señal(es) ACTIVA")
ahora = datetime.now(timezone.utc)
for s in senales_antes:
    ts_raw = s['timestamp']
    ts = datetime.fromisoformat(ts_raw.replace('Z', '+00:00')) if isinstance(ts_raw, str) else ts_raw
    if ts.tzinfo is None:
        from datetime import timezone as tz
        ts = ts.replace(tzinfo=tz.utc)
    horas = (ahora - ts).total_seconds() / 3600
    sufijo = s['simbolo'].split('_')[-1].upper() if '_' in s['simbolo'] else ''
    max_h = _MAX_VIGENCIA_ACTIVA_HORAS.get(sufijo, 336)
    estado = "VENCE" if horas > max_h else "OK"
    print(f"  ID {s['id']} | {s['simbolo']} | {s['direccion']} | "
          f"{horas:.1f}h abierta | límite {max_h}h → {estado}")

print("\nEjecutando cerrar_senales_antiguas()...")
cerrar_senales_antiguas(db, dias=14)

senales_despues = db.obtener_senales_activas()
print(f"\nDespués: {len(senales_despues)} señal(es) ACTIVA")
if senales_despues:
    for s in senales_despues:
        print(f"  ID {s['id']} | {s['simbolo']} | {s['direccion']}")
else:
    print("  (ninguna)")
print()
