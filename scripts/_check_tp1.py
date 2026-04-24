"""Verifica si el precio toco TP1 de la senal #53 en los datos OHLCV de hoy."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from adapters.database import DatabaseManager

db = DatabaseManager()

DESDE = '2026-04-23T07:00:00'
TP1   = 4668.29
ENTRY = 4715.23

result = db.ejecutar_query(
    "SELECT ts, close, high, low FROM ohlcv "
    "WHERE symbol = ? AND interval = ? AND ts >= ? ORDER BY ts ASC",
    ('GC=F', '5m', DESDE)
)

if not result.rows:
    print("NO HAY DATOS en ohlcv para hoy desde las 07:00 UTC")
    sys.exit(0)

lows   = [float(r['low'])   for r in result.rows]
highs  = [float(r['high'])  for r in result.rows]
closes = [float(r['close']) for r in result.rows]
tss    = [r['ts']           for r in result.rows]

print(f"Velas 5M disponibles : {len(result.rows)}")
print(f"Rango                : {tss[0]}  ->  {tss[-1]}")
print(f"Low minimo del dia   : {min(lows):.2f}")
print(f"High maximo del dia  : {max(highs):.2f}")
print()

tocados = [(r['ts'], float(r['low']), float(r['close']))
           for r in result.rows if float(r['low']) <= TP1]

if tocados:
    print(f"!!! VELAS QUE TOCARON TP1 ({TP1}):")
    for ts, low, close in tocados:
        print(f"  {ts}   Low={low:.2f}   Close={close:.2f}")
else:
    print(f"Ninguna vela bajo hasta TP1 ({TP1}). Low minimo fue {min(lows):.2f}")
    diff = min(lows) - TP1
    print(f"Faltan aun {diff:.2f} pts para TP1")

print()
print("Ultimas 15 velas:")
for r in result.rows[-15:]:
    print(f"  {r['ts']}   C={float(r['close']):.2f}   H={float(r['high']):.2f}   L={float(r['low']):.2f}")
