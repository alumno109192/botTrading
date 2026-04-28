"""Comprueba actividad del bot en Render via BD Turso"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv()

from adapters.database import DatabaseManager
db = DatabaseManager()

def q(sql):
    res = db.ejecutar_query(sql)
    return res.rows if res else []

print("=== SEÑALES EN BD ===")
for row in q("SELECT id, simbolo, direccion, score, estado, precio_entrada, tp1, sl, timestamp FROM senales ORDER BY id DESC LIMIT 15"):
    ts = (row['timestamp'] or '')[:19]
    print(f"  #{row['id']} {ts}  {row['simbolo']} {row['direccion']}  "
          f"score={row['score']}  estado={row['estado']}  "
          f"entry={row['precio_entrada']}  TP1={row['tp1']}  SL={row['sl']}")

print()
print("=== ÚLTIMOS LOGS (bot_logs) ===")
for row in q("SELECT id, nivel, mensaje, timestamp FROM bot_logs ORDER BY id DESC LIMIT 20"):
    ts = (row['timestamp'] or '')[:19]
    print(f"  [{ts}] {row['mensaje']}")
