import warnings; warnings.filterwarnings('ignore')
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from adapters.data_provider import get_ohlcv
from core.indicators import calcular_atr

df, _ = get_ohlcv('GC=F', period='5d', interval='5m')
close = df['Close'].iloc[-1]
atr   = calcular_atr(df, 14).iloc[-1]

limit_offset_pct = 0.08
atr_sl_mult      = 1.0
atr_tp1_mult     = 1.0
atr_tp2_mult     = 2.0
atr_tp3_mult     = 3.0

sell_limit = round(close * (1 + limit_offset_pct / 100), 2)
sl_v       = round(sell_limit + atr * atr_sl_mult, 2)
tp1_v      = round(sell_limit - atr * atr_tp1_mult, 2)
tp2_v      = round(sell_limit - atr * atr_tp2_mult, 2)
tp3_v      = round(sell_limit - atr * atr_tp3_mult, 2)

def rr(entry, sl, tp):
    return round(abs(tp - entry) / abs(sl - entry), 1)

print(f"Precio actual  : ${close:.2f}")
print(f"ATR 5M         : ${atr:.2f}")
print()
print(f"SELL LIMIT     : ${sell_limit:.2f}  ← pon la orden aqui")
print(f"Stop Loss      : ${sl_v:.2f}  (+${sl_v - sell_limit:.2f})")
print()
print(f"TP1  ${tp1_v:.2f}   R:R {rr(sell_limit, sl_v, tp1_v)}:1   (-${sell_limit - tp1_v:.2f})")
print(f"TP2  ${tp2_v:.2f}   R:R {rr(sell_limit, sl_v, tp2_v)}:1   (-${sell_limit - tp2_v:.2f})")
print(f"TP3  ${tp3_v:.2f}   R:R {rr(sell_limit, sl_v, tp3_v)}:1   (-${sell_limit - tp3_v:.2f})")
