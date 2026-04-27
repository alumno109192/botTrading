"""Diagnóstico rápido del estado actual de Gold en 5M."""
import warnings; warnings.filterwarnings('ignore')
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from adapters.data_provider import get_ohlcv
from core.indicators import (calcular_rsi, calcular_atr, calcular_adx,
    detectar_canal_roto, detectar_precio_en_canal,
    detectar_stop_hunt_alcista, detectar_stop_hunt_bajista,
    patron_envolvente_alcista, patron_envolvente_bajista)
import services.tf_bias as tf_bias

df, _ = get_ohlcv('GC=F', period='5d', interval='5m')
close = df['Close'].iloc[-1]
high  = df['High'].iloc[-1]
low   = df['Low'].iloc[-1]
rsi   = calcular_rsi(df['Close'], 14).iloc[-1]
atr   = calcular_atr(df, 14).iloc[-1]
adx_s, _, _  = calcular_adx(df, 14)
adx   = adx_s.iloc[-1]

canal_alc_roto, canal_baj_roto, linea_sop, linea_res = detectar_canal_roto(df, atr, lookback=100, wing=3)
en_resist, en_soporte, lr_p, ls_p = detectar_precio_en_canal(df, atr, lookback=100, wing=3)
sh_baj = detectar_stop_hunt_bajista(df)
sh_alc = detectar_stop_hunt_alcista(df)
env_baj = patron_envolvente_bajista(df)
env_alc = patron_envolvente_alcista(df)

sesgo_4h = tf_bias.obtener_sesgo('GC=F', '4H')
canal_4h = tf_bias.obtener_canal_4h('GC=F')

print("=" * 50)
print("  GOLD 5M — Diagnóstico")
print("=" * 50)
print(f"  Precio actual  : ${close:.2f}")
print(f"  High / Low     : ${high:.2f} / ${low:.2f}")
print(f"  RSI            : {rsi:.1f}")
print(f"  ATR            : {atr:.2f}")
print(f"  ADX            : {adx:.1f}")
print()
print("── Canales ──────────────────────────────────")
print(f"  Canal alcista roto : {canal_alc_roto}  (soporte ${linea_sop:.2f})")
print(f"  Canal bajista roto : {canal_baj_roto}  (resist  ${linea_res:.2f})")
print(f"  En directriz BAJ   : {en_resist}  → ${lr_p:.2f}")
print(f"  En directriz ALC   : {en_soporte}  → ${ls_p:.2f}")
print()
print("── Patrones ─────────────────────────────────")
print(f"  Stop Hunt BAJISTA  : {sh_baj}")
print(f"  Stop Hunt ALCISTA  : {sh_alc}")
print(f"  Envolvente BAJISTA : {env_baj}")
print(f"  Envolvente ALCISTA : {env_alc}")
print()
print("── Contexto 4H ──────────────────────────────")
print(f"  Sesgo 4H           : {sesgo_4h}")
if canal_4h:
    print(f"  Canal 4H alc_roto  : {canal_4h.get('alcista_roto')}")
    print(f"  Canal 4H baj_roto  : {canal_4h.get('bajista_roto')}")
else:
    print("  Canal 4H           : no publicado aún")
print()
print("── Scoring rápido ───────────────────────────")
score_sell = 0
score_buy  = 0
if rsi >= 65: score_sell += 2
elif rsi >= 60: score_sell += 1
if rsi <= 35: score_buy += 2
elif rsi <= 40: score_buy += 1
if canal_alc_roto:  score_sell += 2
if en_resist:       score_sell += 3
if canal_baj_roto:  score_buy  += 2
if en_soporte:      score_buy  += 3
if sh_baj:          score_sell += 3
if sh_alc:          score_buy  += 3
if env_baj:         score_sell += 2
if env_alc:         score_buy  += 2
print(f"  Score SELL : {score_sell}/24")
print(f"  Score BUY  : {score_buy}/24")
bias = "→ BAJISTA" if score_sell > score_buy + 1 else "→ ALCISTA" if score_buy > score_sell + 1 else "→ LATERAL / INDECISO"
print(f"  SESGO 5M   : {bias}")
print()
print("── Últimas 5 velas ──────────────────────────")
print(df[['Open','High','Low','Close','Volume']].tail(5).to_string())
print("=" * 50)
