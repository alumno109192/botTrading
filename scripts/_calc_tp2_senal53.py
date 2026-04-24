"""Calcula el TP2 correcto para senal #53 usando los soportes S/R reales."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pandas as pd
import numpy as np
from adapters.data_provider import get_ohlcv
from core.indicators import calcular_sr_multiples

SIMBOLO = 'GC=F'
ATR_LENGTH = 14
SR_LOOKBACK = 150
SR_ZONE_MULT = 0.8
TP1 = 4668.29
TP3 = 4648.39
ATR_SIGNAL = 16.71  # atr en el momento de la senal

# Obtener datos 5M y resamplear a 1H (igual que el detector)
result = get_ohlcv(SIMBOLO, period='60d', interval='5m')
df_5m = result[0] if isinstance(result, tuple) else result
df = df_5m.resample('1h').agg({
    'Open': 'first', 'High': 'max', 'Low': 'min',
    'Close': 'last', 'Volume': 'sum'
}).dropna()

# Calcular ATR
high_low   = df['High'] - df['Low']
high_close = (df['High'] - df['Close'].shift()).abs()
low_close  = (df['Low']  - df['Close'].shift()).abs()
atr_s = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1).rolling(ATR_LENGTH).mean()
atr = float(atr_s.iloc[-2])  # vela cerrada
print(f"ATR actual (1H): {atr:.2f}  |  ATR en senal: {ATR_SIGNAL:.2f}")

# Soportes S/R multiples (usando velas cerradas como el detector)
df_c = df.iloc[:-1]
soportes, _ = calcular_sr_multiples(df_c, atr, SR_LOOKBACK, SR_ZONE_MULT, n_niveles=5)
print(f"\nSoportes detectados (nearest-first): {[round(s,2) for s in soportes]}")
print(f"TP1 actual: {TP1}")
print(f"TP3 actual: {TP3}")

# Encontrar soportes por debajo de TP1
sep_min = ATR_SIGNAL * 0.4
candidatos = sorted([s for s in soportes if s < TP1 - sep_min], reverse=True)
print(f"\nCandidatos TP2 (soportes < TP1 - {sep_min:.1f}): {[round(s,2) for s in candidatos]}")

if candidatos:
    tp2_nuevo = round(candidatos[0], 2)
    print(f"\n>>> TP2 recomendado: {tp2_nuevo}")
else:
    tp2_nuevo = round(TP1 - ATR_SIGNAL * 1.0, 2)
    print(f"\n>>> TP2 fallback ATR: {tp2_nuevo}")
