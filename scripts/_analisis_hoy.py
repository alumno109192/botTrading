import sys; sys.path.insert(0, '.')
from adapters.database import get_db
db = get_db()
r = db.ejecutar_query("SELECT id, simbolo, direccion, estado, precio_entrada, sl, timestamp FROM senales WHERE timestamp >= '2026-05-12' ORDER BY id ASC")
print('Señales hoy:', len(r.rows))
estados = {}
for row in r.rows:
    e = row['estado']
    estados[e] = estados.get(e, 0) + 1
    print(' #%d %s %s %-12s entrada=%.2f sl=%.2f' % (row['id'], row['simbolo'], row['direccion'], row['estado'], row['precio_entrada'] or 0, row['sl'] or 0))
print()
print('Resumen por estado:')
for e, n in sorted(estados.items()):
    print('  %s: %d' % (e, n))
