import sys; sys.path.insert(0, '.')
from adapters.database import DatabaseManager
db = DatabaseManager()

r = db.ejecutar_query("SELECT name FROM sqlite_master WHERE type='table'")
print('=== TABLAS ===')
for row in r.rows:
    print(' ', row)

r2 = db.ejecutar_query('SELECT COUNT(*) as n FROM senales')
print('Filas en senales:', r2.rows)

r3 = db.ejecutar_query('PRAGMA table_info(senales)')
print('Columnas de senales:')
for row in r3.rows:
    print(' ', row)

r4 = db.ejecutar_query('SELECT * FROM senales ORDER BY rowid DESC LIMIT 5')
print('Ultimas senales (SELECT *):')
for row in r4.rows:
    print(' ', row)
