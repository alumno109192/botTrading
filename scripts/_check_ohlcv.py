import sys
sys.path.insert(0, '.')
from adapters.database import DatabaseManager

db = DatabaseManager()
r = db.ejecutar_query(
    "SELECT interval, COUNT(*) as n, MIN(timestamp) as min_ts, MAX(timestamp) as max_ts "
    "FROM ohlcv WHERE ticker = 'GC=F' GROUP BY interval ORDER BY interval"
)
print("=== Velas OHLCV en BD para GC=F ===")
if not r.rows:
    print("  (tabla vacía)")
for row in r.rows:
    print(f"  {row['interval']:>4s}: {row['n']:>5} velas | {row['min_ts'][:16]} → {row['max_ts'][:16]}")
