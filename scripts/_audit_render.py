"""Investiga qué pasó en Render durante la caída de las 05:00 UTC"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv; load_dotenv()
from adapters.database import DatabaseManager
db = DatabaseManager()

def q(sql, params=()):
    res = db.ejecutar_query(sql, params)
    return res.rows if res else []

# 1. Logs entre 04:00 y 06:30 UTC
print("=== LOGS RENDER 04:00 - 06:30 UTC (hora de la caida) ===")
rows = q(
    "SELECT id, nivel, mensaje, timestamp FROM bot_logs "
    "WHERE timestamp >= '2026-04-28T04:00:00' AND timestamp <= '2026-04-28T06:30:00' "
    "ORDER BY id ASC"
)
print(f"  Total entradas: {len(rows)}")
for row in rows:
    print(f"  [{row['timestamp'][:19]}] {row['mensaje']}")

# 2. Último log ANTES de las 06:12 (hora en que yo corrí el bot local)
print()
print("=== ÚLTIMO LOG DE RENDER ANTES DE QUE YO EJECUTARA LOCALMENTE (06:12) ===")
rows2 = q(
    "SELECT id, nivel, mensaje, timestamp FROM bot_logs "
    "WHERE timestamp < '2026-04-28T06:12:00' "
    "ORDER BY id DESC LIMIT 5"
)
for row in rows2:
    print(f"  [{row['timestamp'][:19]}] {row['mensaje']}")

# 3. Primer log disponible (para saber desde cuando corre Render)
print()
print("=== PRIMER LOG EN BD (inicio del bot en Render) ===")
rows3 = q("SELECT id, nivel, mensaje, timestamp FROM bot_logs ORDER BY id ASC LIMIT 5")
for row in rows3:
    print(f"  [{row['timestamp'][:19]}] {row['mensaje']}")

# 4. Total de logs
rows4 = q("SELECT COUNT(*) as total FROM bot_logs")
print()
print(f"Total logs en BD: {rows4[0]['total']}")
