import sys
sys.path.insert(0, '.')
from adapters.data_provider import get_ohlcv
from core.indicators import calcular_atr, calcular_sr_multiples
import pandas as pd

df, _ = get_ohlcv('GC=F', period='60d', interval='4h')
df['atr'] = calcular_atr(df, 28)
atr = float(df['atr'].iloc[-2])
low_ult = float(df['Low'].iloc[-2])
close_ult = float(df['Close'].iloc[-2])
print(f'ATR: {atr:.2f}  | tol (0.4): {atr*0.4:.2f}')
print(f'Low ultima vela:   {low_ult:.1f}')
print(f'Close ultima vela: {close_ult:.1f}')

soportes, resistencias = calcular_sr_multiples(df, atr, lookback=80, zone_mult=0.6)
print('\nSoportes detectados (calcular_sr_multiples):')
for s in sorted(soportes):
    dist = close_ult - s
    en_zona = abs(low_ult - s) <= atr * 0.4
    print(f'  {s:.1f}  (dist close={dist:.1f})  EN_ZONA={en_zona}')

print('\nResistencias detectadas:')
for r in sorted(resistencias):
    print(f'  {r:.1f}')

# Simular calcular_zonas_sr real (con swings) del detector
from core.base_detector import BaseDetector

class TmpDet(BaseDetector):
    def analizar(self, *a, **k): pass

det = TmpDet('XAUUSD', '4H', {}, None)
try:
    zrl, zrh, zsl, zsh = det.calcular_zonas_sr(df, atr, 80, 0.6)
    print(f'\ncalcular_zonas_sr (detector real):')
    print(f'  Resistencia: {zrl:.1f} - {zrh:.1f}')
    print(f'  Soporte:     {zsl:.1f} - {zsh:.1f}')
    en_zona_soporte = (low_ult >= zsl - atr*0.4) and (low_ult <= zsh + atr*0.4)
    dist = close_ult - zsh
    print(f'  en_zona_soporte (low vs zona+-tol): {en_zona_soporte}')
    print(f'  dist_to_support (close-zsh): {dist:.1f}  vs avg_range*3: {float(df["High"].sub(df["Low"]).iloc[-6:-1].mean())*3:.1f}')
except Exception as e:
    print(f'Error: {e}')
