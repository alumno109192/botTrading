"""Analisis estructura canal 5M para entrada SELL"""
import os, sys, warnings
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
warnings.filterwarnings('ignore')
from dotenv import load_dotenv; load_dotenv()

from adapters.data_provider import get_ohlcv
from core.indicators import (calcular_atr, calcular_adx, calcular_rsi,
    calcular_sr_multiples, detectar_cuña_descendente, detectar_cuña_ascendente,
    detectar_ruptura_soporte_horizontal)

df, _ = get_ohlcv('GC=F', period='5d', interval='5m')
atr   = float(calcular_atr(df, 14).iloc[-2])
rsi   = float(calcular_rsi(df['Close'], 14).iloc[-2])
adx, dip, dim = calcular_adx(df, 14)
adx_v = float(adx.iloc[-2])
dip_v = float(dip.iloc[-2])
dim_v = float(dim.iloc[-2])

close = float(df['Close'].iloc[-2])
high  = float(df['High'].iloc[-2])
low   = float(df['Low'].iloc[-2])
ts    = df.index[-2]
close_viva = float(df['Close'].iloc[-1])

print(f"Vela cerrada: {ts}")
print(f"O={float(df['Open'].iloc[-2]):.2f}  H={high:.2f}  L={low:.2f}  C={close:.2f}")
print(f"Vela viva:  {df.index[-1]}  C={close_viva:.2f}")
print(f"ATR={atr:.2f}  RSI={rsi:.1f}  ADX={adx_v:.1f}  DI+={dip_v:.1f}  DI-={dim_v:.1f}")
print()

# Patrones
e_desc, t_desc, s_desc = detectar_cuña_descendente(df, atr, lookback=60, wing=2, max_amplitud_pct=0.015)
e_asc,  t_asc,  s_asc  = detectar_cuña_ascendente( df, atr, lookback=60, wing=2, max_amplitud_pct=0.015)
rup_sop, niv_sop = detectar_ruptura_soporte_horizontal(df, atr, lookback=80, wing=2)

print(f"Cuña DESCENDENTE: {e_desc:16s}  techo={t_desc:.2f}  suelo={s_desc:.2f}")
print(f"Cuña ASCENDENTE:  {e_asc:16s}  techo={t_asc:.2f}  suelo={s_asc:.2f}")
print(f"Ruptura soporte:  {str(rup_sop):5s}  nivel={niv_sop:.2f}")
print()

sops, ress = calcular_sr_multiples(df, atr, lookback=80, n_niveles=8, wing=2)
sops_cerca = sorted([s for s in sops if s < close + 20], reverse=True)
ress_cerca = sorted([r for r in ress if r > close - 20])

print(f"Precio actual:    {close:.2f}")
print(f"Resistencias 5M:  {[round(r,1) for r in ress_cerca[:4]]}")
print(f"Soportes 5M:      {[round(s,1) for s in sops_cerca[:4]]}")
print()

# ── ESCENARIOS DE ENTRADA ──────────────────────────────────────────────────
print("=" * 55)
print("ESCENARIOS DE ENTRADA EN VENTA")
print("=" * 55)

# Escenario A: rebote al techo del canal
if e_desc in ('compresion', 'ruptura_bajista') and t_desc > 0:
    sl_a   = round(t_desc + atr * 1.2, 2)
    tp1_a  = round(s_desc - atr * 0.5, 2) if s_desc > 0 else round(close - atr * 3, 2)
    tp2_a  = round(tp1_a - atr * 2, 2)
    rr_a   = round((t_desc - tp1_a) / (sl_a - t_desc), 1) if sl_a != t_desc else 0
    print(f"\n[A] REBOTE AL TECHO DEL CANAL")
    print(f"    Entry:  {t_desc:.2f}  (techo cuña descendente)")
    print(f"    SL:     {sl_a:.2f}  (-{sl_a-t_desc:.1f} pts)")
    print(f"    TP1:    {tp1_a:.2f}  (+{t_desc-tp1_a:.1f} pts)  R:R {rr_a}")
    print(f"    TP2:    {tp2_a:.2f}  (+{t_desc-tp2_a:.1f} pts)")
    print(f"    Estado cuña: {e_desc}")

# Escenario B: ruptura soporte horizontal
if rup_sop:
    sl_b  = round(niv_sop + atr * 1.0, 2)
    tp1_b = round(niv_sop - atr * 3, 2)
    tp2_b = round(niv_sop - atr * 5, 2)
    rr_b  = round((niv_sop - tp1_b) / (sl_b - niv_sop), 1)
    print(f"\n[B] RUPTURA SOPORTE HORIZONTAL (YA ACTIVA)")
    print(f"    Nivel roto: {niv_sop:.2f}")
    print(f"    Entry:  {close:.2f}  (precio actual — entrada agresiva)")
    print(f"    SL:     {sl_b:.2f}  (-{sl_b-close:.1f} pts)")
    print(f"    TP1:    {tp1_b:.2f}  (+{close-tp1_b:.1f} pts)  R:R {rr_b}")
    print(f"    TP2:    {tp2_b:.2f}  (+{close-tp2_b:.1f} pts)")

# Escenario C: pullback al nivel roto como resistencia
if rup_sop:
    entry_c = round(niv_sop, 2)
    sl_c    = round(niv_sop + atr * 1.5, 2)
    tp1_c   = round(niv_sop - atr * 3, 2)
    tp2_c   = round(niv_sop - atr * 5, 2)
    rr_c    = round((entry_c - tp1_c) / (sl_c - entry_c), 1)
    print(f"\n[C] PULLBACK AL SOPORTE ROTO (nivel roto = nueva resist)")
    print(f"    Entry:  {entry_c:.2f}  (esperar retest del nivel roto)")
    print(f"    SL:     {sl_c:.2f}  (-{sl_c-entry_c:.1f} pts)")
    print(f"    TP1:    {tp1_c:.2f}  (+{entry_c-tp1_c:.1f} pts)  R:R {rr_c}")
    print(f"    TP2:    {tp2_c:.2f}  (+{entry_c-tp2_c:.1f} pts)")
    print(f"    [MAS CONSERVADOR — mejor R:R]")

print()
print(f"ADX={adx_v:.1f}  DI-={dim_v:.1f} > DI+={dip_v:.1f} → {'BAJISTA confirmado' if dim_v > dip_v else 'NO confirmado'}")
print(f"RSI={rsi:.1f} → {'sobrevendido, cuidado con rebote' if rsi < 30 else 'ok para venta' if rsi < 50 else 'neutral'}")
