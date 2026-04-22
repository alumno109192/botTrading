import sys
sys.path.insert(0, '.')
from adapters.database import DatabaseManager
db = DatabaseManager()

# 1. Señales activas/pendientes que bloquean nuevas
r = db.ejecutar_query(
    "SELECT id, simbolo, direccion, estado, precio_entrada, timestamp "
    "FROM senales "
    "WHERE simbolo LIKE '%1H%' AND estado IN ('ACTIVA','PENDIENTE_CONFIRM') "
    "ORDER BY timestamp DESC LIMIT 20"
)
print('=== SEÑALES 1H ACTIVAS/PENDIENTES (bloquean nuevas) ===')
for row in r.rows:
    print(f"  id={row['id']} | {row['simbolo']} | {row['direccion']} | {row['estado']} | entrada={row['precio_entrada']} | ts={str(row['timestamp'])[:16]}")
if not r.rows:
    print('  (ninguna — no es la causa)')

# 2. Últimas 10 señales 1H
r2 = db.ejecutar_query(
    "SELECT id, simbolo, direccion, estado, precio_entrada, timestamp "
    "FROM senales WHERE simbolo LIKE '%1H%' "
    "ORDER BY timestamp DESC LIMIT 10"
)
print()
print('=== ÚLTIMAS 10 SEÑALES 1H ===')
for row in r2.rows:
    print(f"  id={row['id']} | {row['simbolo']} | {row['direccion']} | {row['estado']} | entrada={row['precio_entrada']} | ts={str(row['timestamp'])[:16]}")
if not r2.rows:
    print('  (ninguna todavía en BD)')
