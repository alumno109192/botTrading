"""Diagnóstico: por qué no salió señal BUY"""
import sys, time
sys.path.insert(0, '.')

import yfinance as yf
import pandas as pd

df = yf.Ticker('GC=F').history(period='5d', interval='5m')
df = df.resample('1h').agg({'Open':'first','High':'max','Low':'min','Close':'last','Volume':'sum'}).dropna()

print(f"Velas 1H: {len(df)}")
print(f"Ultima cerrada: {df.index[-2]}  Close={df.iloc[-2]['Close']:.2f}")
print(f"Vela viva:      {df.index[-1]}  Close={df.iloc[-1]['Close']:.2f}")
print()

# Mostrar ultimas 6 velas
print("=== ULTIMAS 6 VELAS 1H ===")
for i in range(-6, 0):
    row = df.iloc[i]
    print(f"  {df.index[i]}  O={row['Open']:.2f}  H={row['High']:.2f}  L={row['Low']:.2f}  C={row['Close']:.2f}")

print()

# Verificar estado BD
from adapters.database import DatabaseManager
db = DatabaseManager()
q = "SELECT id, simbolo, direccion, precio_entrada, tp1, sl, estado, timestamp FROM senales WHERE estado IN ('ACTIVA','PENDIENTE_CONFIRM') ORDER BY id DESC"
r = db.ejecutar_query(q)
print("=== SEÑALES ACTIVAS EN BD ===")
if r.rows:
    for row in r.rows:
        d = dict(row)
        print(f"  #{d['id']} {d['simbolo']} {d['direccion']} entrada={d['precio_entrada']:.2f} sl={d['sl']:.2f} estado={d['estado']} ts={d['timestamp'][:16]}")
else:
    print("  (ninguna)")

print()

# Ahora ejecutar el detector para ver qué genera HOY
print("=== EJECUTANDO DETECTOR 1H AHORA ===")
from detectors.gold.detector_gold_1h import analizar, SIMBOLOS
analizar('XAUUSD', SIMBOLOS['XAUUSD'])
