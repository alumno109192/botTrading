"""Diagnóstico urgente: ¿Por qué no saltó SELL en la caída masiva?"""
import os, warnings
warnings.filterwarnings('ignore')
from dotenv import load_dotenv
load_dotenv()

print("="*60)
print(f"SKIP_SESSION_FILTER = {os.getenv('SKIP_SESSION_FILTER')}")

from adapters.data_provider import get_ohlcv
from core.indicators import calcular_rsi, calcular_atr, calcular_adx, calcular_sr_multiples

# === DATOS 1H ===
df, delayed = get_ohlcv('GC=F', period='30d', interval='1h')
df['rsi'] = calcular_rsi(df['Close'], 14)
df['atr'] = calcular_atr(df, 14)
df['adx'], df['dip'], df['dim'] = calcular_adx(df, 14)

print(f"\nVelas 1H: {len(df)} | delayed: {delayed}")
print("\nÚltimas 8 velas 1H cerradas:")
for i in range(-9, -1):
    r = df.iloc[i]
    c = float(r['Close']); h = float(r['High']); l = float(r['Low']); o = float(r['Open'])
    rsi = float(r['rsi']); adx = float(r['adx'])
    bajista = 'BAJA' if c < o else 'SUBE'
    print(f"  {str(df.index[i])[:19]}  O={o:.0f} H={h:.0f} L={l:.0f} C={c:.0f}  [{bajista}]  RSI={rsi:.0f}  ADX={adx:.0f}")

# Vela que debería haber disparado SELL (la gran bajista)
print("\n--- ANÁLISIS VELA GRAN BAJADA ---")
# Buscar la vela con mayor caída
drops = []
for i in range(-12, -1):
    r = df.iloc[i]
    drop = float(r['Open']) - float(r['Close'])
    drops.append((i, drop, df.index[i]))

drops.sort(key=lambda x: -x[1])
idx_sell, drop_val, ts_sell = drops[0]
print(f"Mayor bajada: vela {ts_sell}  caída={drop_val:.1f}")

row  = df.iloc[idx_sell]
prev = df.iloc[idx_sell - 1]
atr_v = float(df.iloc[idx_sell]['atr'])
atr_media = float(df['atr'].rolling(20).mean().iloc[idx_sell])
close = float(row['Close'])
high  = float(row['High'])
low   = float(row['Low'])
open_ = float(row['Open'])
rsi   = float(row['rsi'])
adx   = float(row['adx'])
dip   = float(row['dip'])
dim   = float(row['dim'])

print(f"O={open_:.2f}  H={high:.2f}  L={low:.2f}  C={close:.2f}")
print(f"RSI={rsi:.1f}  ADX={adx:.1f}  DI+={dip:.1f}  DI-={dim:.1f}")
print(f"ATR={atr_v:.2f}  ATR_media={atr_media:.2f}  adx_lateral={adx < 20}")

# === ZONAS S/R en ese momento ===
_df_hist = df.iloc[:df.index.get_loc(df.index[idx_sell])]
lows_h  = _df_hist['Low'].iloc[-150:]
highs_h = _df_hist['High'].iloc[-150:]
n = len(lows_h)
wing = 3
cand_sop, cand_res = [], []
for i in range(wing, n-wing):
    if all(lows_h.iloc[i] <= lows_h.iloc[i-j] for j in range(1,wing+1)) and \
       all(lows_h.iloc[i] <= lows_h.iloc[i+j] for j in range(1,wing+1)):
        cand_sop.append(float(lows_h.iloc[i]))
    if all(highs_h.iloc[i] >= highs_h.iloc[i-j] for j in range(1,wing+1)) and \
       all(highs_h.iloc[i] >= highs_h.iloc[i+j] for j in range(1,wing+1)):
        cand_res.append(float(highs_h.iloc[i]))

res_enc = [r for r in cand_res if r >= close]
sop_deb = [s for s in cand_sop if s <= close]
rp = min(res_enc) if res_enc else float(highs_h.max())
sp = max(sop_deb) if sop_deb else float(lows_h.min())

zrh = round(rp + atr_v*0.5*0.25, 2)
zrl = round(rp - atr_v*0.5*0.75, 2)
zsh = round(sp + atr_v*0.5*0.75, 2)
zsl = round(sp - atr_v*0.5*0.25, 2)
tol = atr_v * 0.8

print(f"\nZona resist: {zrl:.1f}-{zrh:.1f} (pivot={rp:.1f})")
print(f"Zona soporte: {zsl:.1f}-{zsh:.1f} (pivot={sp:.1f})")

en_zona_resist  = (high >= zrl-tol) and (high <= zrh+tol)
en_zona_soporte = (low  >= zsl-tol) and (low  <= zsh+tol)
cancelar_sell   = close > zrh*(1+0.005)

print(f"\nen_zona_resist:  {en_zona_resist}  (high={high:.2f} vs {zrl-tol:.2f}-{zrh+tol:.2f})")
print(f"en_zona_soporte: {en_zona_soporte}")
print(f"cancelar_sell:   {cancelar_sell}  (close={close:.2f} vs zrh*1.005={zrh*1.005:.2f})")

# SR intermedios
sops_i, ress_i = calcular_sr_multiples(_df_hist, atr_v, lookback=150, zone_mult=0.5, n_niveles=8, wing=2)
en_resist_sr = any(abs(high - r) <= tol for r in ress_i)
print(f"en_resist_sr (intermedios): {en_resist_sr}")
en_zona_resist_any = en_zona_resist or en_resist_sr

print(f"\nen_zona_resist_any: {en_zona_resist_any}")
print(f"\n→ PROBLEMA: Para señal SELL, la vela debe estar EN zona de resistencia")
print(f"→ Esta vela cayó DESDE {open_:.0f} HASTA {close:.0f} — es una vela de ruptura de soporte")
print(f"→ El bot está diseñado para señalizar RECHAZOS en resistencia, no rupturas de soporte")

# Ver si hay filtro proximidad activo
cerca_resist = en_zona_resist_any
print(f"\nFILTRO PROXIMIDAD activo (cerca_resistencia={cerca_resist}): si False → SELL ignorada")

# Score aproximado
score_sell = 0
is_bearish = close < open_
body = abs(close - open_)
total_range = high - low
upper_wick = high - max(close, open_)
lower_wick = min(close, open_) - low
score_sell += 2 if en_zona_resist_any else 0
score_sell += 1 if (rsi > 60) else 0
score_sell += 2 if (is_bearish and body > total_range*0.8) else 0  # marubozu
score_sell += 1 if (adx > 25 and dim > dip) else 0
print(f"\nScore SELL estimado para esa vela: {score_sell}")
print(f"Umbral ALERTA: {5+1 if atr_v/atr_media>1.2 else 5}")

# ¿Dónde está el bot corriendo?
print("\n" + "="*60)
print("RESUMEN DEL PROBLEMA:")
if not en_zona_resist_any:
    print("⛔ FILTRO PROXIMIDAD: precio NO estaba en zona de resistencia")
    print(f"   La vela cayó en medio (entre soporte y resist) — bot la ignora por diseño")
    print(f"   La resistencia más cercana estaba en {rp:.0f}, la vela abrió en {open_:.0f}")
    dist = rp - open_
    print(f"   Diferencia: {dist:.0f} pts — la vela bajista ocurrió {dist:.0f} pts BAJO la resistencia")
