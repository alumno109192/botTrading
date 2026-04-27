"""Niveles SELL para Gold 1H con parámetros reales del detector."""
import warnings; warnings.filterwarnings('ignore')
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from adapters.data_provider import get_ohlcv
from core.indicators import calcular_atr, calcular_sr_multiples

df, _ = get_ohlcv('GC=F', period='30d', interval='1h')
close = df['Close'].iloc[-1]
high  = df['High'].iloc[-1]
low   = df['Low'].iloc[-1]
atr   = calcular_atr(df, 14).iloc[-1]

# Parámetros detector_gold_1h.py
limit_offset_pct = 0.3
atr_sl_mult      = 1.0
atr_tp1_mult     = 1.5
atr_tp2_mult     = 2.5
atr_tp3_mult     = 4.0

sell_limit = round(close * (1 + limit_offset_pct / 100), 2)
sl_v       = round(sell_limit + atr * atr_sl_mult, 2)

# TPs base (sin ajuste S/R)
tp1_base = round(sell_limit - atr * atr_tp1_mult, 2)
tp2_base = round(sell_limit - atr * atr_tp2_mult, 2)
tp3_base = round(sell_limit - atr * atr_tp3_mult, 2)

# Zonas S/R del 1H para referencia
soportes, resistencias = calcular_sr_multiples(df, atr=atr, lookback=200, n_niveles=8)
soportes_bajo_precio   = sorted([s for s in soportes if s < sell_limit], reverse=True)[:5]

def rr(entry, sl, tp):
    return round(abs(tp - entry) / abs(sl - entry), 1)

print(f"Precio actual   : ${close:.2f}")
print(f"ATR 1H          : ${atr:.2f}")
print()
print(f"SELL LIMIT      : ${sell_limit:.2f}  ← entrada")
print(f"Stop Loss       : ${sl_v:.2f}         (+${sl_v - sell_limit:.2f})")
print()
print(f"TP1  ${tp1_base:.2f}   R:R {rr(sell_limit, sl_v, tp1_base)}:1   (-${sell_limit - tp1_base:.2f})")
print(f"TP2  ${tp2_base:.2f}   R:R {rr(sell_limit, sl_v, tp2_base)}:1   (-${sell_limit - tp2_base:.2f})")
print(f"TP3  ${tp3_base:.2f}   R:R {rr(sell_limit, sl_v, tp3_base)}:1   (-${sell_limit - tp3_base:.2f})")
print()
print("── Soportes S/R 1H por debajo del precio ──")
for i, s in enumerate(soportes_bajo_precio, 1):
    dist = sell_limit - s
    print(f"  S{i}  ${s:.2f}   (-${dist:.2f} desde entry)")
