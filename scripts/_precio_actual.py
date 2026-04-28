from adapters.data_provider import get_ohlcv
import pandas as pd

df, _ = get_ohlcv('GC=F', period='2d', interval='1h')
if isinstance(df.columns, pd.MultiIndex):
    df.columns = df.columns.get_level_values(0)

last = df.iloc[-1]
prev = df.iloc[-2]
print(f"Hora ultima vela : {df.index[-1]}")
print(f"Ultima vela  : O={last['Open']:.2f}  H={last['High']:.2f}  L={last['Low']:.2f}  C={last['Close']:.2f}")
print(f"Vela anterior: O={prev['Open']:.2f}  H={prev['High']:.2f}  L={prev['Low']:.2f}  C={prev['Close']:.2f}")
print(f"Precio actual (close): {last['Close']:.2f}")
