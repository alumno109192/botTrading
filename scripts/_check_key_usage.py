from dotenv import load_dotenv; load_dotenv()
from adapters.database import DatabaseManager
db = DatabaseManager()

# Tabla existe?
r = db.ejecutar_query("SELECT name FROM sqlite_master WHERE type='table' AND name='api_key_usage'")
print('Tabla api_key_usage existe:', bool(r.rows))

# Uso hoy
uso = db.obtener_uso_keys_hoy()
print('Uso hoy:', uso if uso else '(vacio)')

# Historico 7 dias
hist = db.obtener_uso_keys_periodo(7)
print(f'Historico 7d ({len(hist)} registros):')
for row in hist[:20]:
    print(' ', row)

# Ver _is_daily_limit_exceeded en vivo
import sys; sys.path.insert(0, '.')
from adapters.data_provider import _td_keys, _is_daily_limit_exceeded, _MAX_DAILY_FREE
print(f'\nLimite diario FREE: {_MAX_DAILY_FREE}')
for alias, _ in _td_keys:
    excedida = _is_daily_limit_exceeded(alias)
    print(f'  {alias}: excedida={excedida}')
