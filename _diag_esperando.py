"""Diagnóstico rápido de señales ESPERANDO y precio actual."""
from adapters.database import DatabaseManager
from services.signal_monitor import _fetch_precios_ticker, SIMBOLO_TO_TICKER

db = DatabaseManager()

# Señales ESPERANDO
r = db.ejecutar_query(
    "SELECT id, simbolo, direccion, estado, precio_entrada, sl, tp1, timestamp "
    "FROM senales WHERE estado IN ('ESPERANDO','ACTIVA','PENDIENTE_CONFIRM') "
    "ORDER BY timestamp DESC LIMIT 10"
)
print(f"\n{'='*70}")
print("  SEÑALES ESPERANDO/ACTIVAS")
print(f"{'='*70}")
if not r.rows:
    print("  (ninguna)")
for row in r.rows:
    print(f"  #{row['id']} | {row['simbolo']} | {row['direccion']} | {row['estado']}")
    print(f"       entrada={row['precio_entrada']}  SL={row['sl']}  TP1={row['tp1']}")
    print(f"       ts={str(row['timestamp'])[:19]}")

# Precio actual XAUUSD
print(f"\n{'='*70}")
print("  PRECIO ACTUAL XAUUSD (via signal_monitor._fetch_precios_ticker)")
print(f"{'='*70}")
ticker = SIMBOLO_TO_TICKER.get('XAUUSD', 'GC=F')
precios = _fetch_precios_ticker(ticker, db=db)
if precios:
    actual, maximo, minimo = precios
    print(f"  Actual: {actual:.2f}  |  Max(5v): {maximo:.2f}  |  Min(5v): {minimo:.2f}")
else:
    print("  ⚠️  No se pudo obtener precio")

# Últimas 5 señales (cualquier estado)
print(f"\n{'='*70}")
print("  ÚLTIMAS 5 SEÑALES (cualquier estado)")
print(f"{'='*70}")
r2 = db.ejecutar_query(
    "SELECT id, simbolo, direccion, estado, precio_entrada, timestamp "
    "FROM senales ORDER BY timestamp DESC LIMIT 5"
)
for row in r2.rows:
    print(f"  #{row['id']} | {row['simbolo']} | {row['direccion']} | {row['estado']} | entrada={row['precio_entrada']} | {str(row['timestamp'])[:19]}")
