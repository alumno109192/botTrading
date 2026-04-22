"""Diagnóstico rápido — por qué no hay señales hoy"""
import sys
sys.path.insert(0, '.')

from adapters.data_provider import get_ohlcv
from core.indicators import calcular_rsi, calcular_ema, calcular_atr, calcular_adx, detectar_canal_roto
from services.economic_calendar import hay_evento_impacto
import pandas as pd
from datetime import datetime, timezone

# ── 1. Calendario económico ──────────────────────────────────────
print("=" * 60)
print("1. CALENDARIO ECONOMICO")
try:
    bloqueado, desc = hay_evento_impacto(ventana_minutos=60)
    print(f"   Bloqueado: {bloqueado}  → {desc}")
except RuntimeError as e:
    print(f"   EXPIRADO: {e}")
except Exception as e:
    print(f"   ERROR: {e}")

# ── 2. Datos del mercado ─────────────────────────────────────────
print("\n2. DATOS MERCADO")
try:
    df_5m, delayed = get_ohlcv('GC=F', period='7d', interval='5m')
    df = df_5m.resample('1h').agg({'Open':'first','High':'max','Low':'min','Close':'last','Volume':'sum'}).dropna()
    print(f"   Velas 1H: {len(df)} | Delayed: {delayed}")
    print(f"   UTC ahora: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')}")
except Exception as e:
    print(f"   ERROR descargando datos: {e}")
    sys.exit(1)

df['rsi']       = calcular_rsi(df['Close'], 14)
df['ema_fast']  = calcular_ema(df['Close'], 9)
df['ema_slow']  = calcular_ema(df['Close'], 21)
df['ema_trend'] = calcular_ema(df['Close'], 200)
df['atr']       = calcular_atr(df, 14)
df['total_range'] = df['High'] - df['Low']
df['adx'], df['di_plus'], df['di_minus'] = calcular_adx(df)

row   = df.iloc[-2]
prev  = df.iloc[-3]
p2    = df.iloc[-4]
atr   = float(row['atr'])
close = float(row['Close'])
high  = float(row['High'])
low   = float(row['Low'])
open_ = float(row['Open'])
rsi   = float(row['rsi'])
adx   = float(row['adx'])
di_p  = float(row['di_plus'])
di_m  = float(row['di_minus'])

print(f"\n3. VELA ANALIZADA: {df.index[-2]}")
print(f"   Close={close:.1f} | High={high:.1f} | Low={low:.1f} | Open={open_:.1f}")
print(f"   RSI={rsi:.1f} | ADX={adx:.1f} (DI+={di_p:.1f} DI-={di_m:.1f}) | ATR={atr:.1f}")
print(f"   EMA9={float(row['ema_fast']):.1f} | EMA21={float(row['ema_slow']):.1f} | EMA200={float(row['ema_trend']):.1f}")
is_bearish = close < open_
is_bullish = close > open_
print(f"   Vela: {'BAJISTA' if is_bearish else 'ALCISTA' if is_bullish else 'DOJI'}")

# ── 3. Zonas S/R ─────────────────────────────────────────────────
print("\n4. ZONAS S/R")
lookback = 150
wing = 3
highs = df['High'].iloc[-lookback-1:-1]
lows  = df['Low'].iloc[-lookback-1:-1]
swing_highs, swing_lows = [], []
for i in range(wing, len(highs) - wing):
    v = float(highs.iloc[i])
    if all(v >= float(highs.iloc[i-j]) for j in range(1, wing+1)) and \
       all(v >= float(highs.iloc[i+j]) for j in range(1, wing+1)):
        swing_highs.append(v)
for i in range(wing, len(lows) - wing):
    v = float(lows.iloc[i])
    if all(v <= float(lows.iloc[i-j]) for j in range(1, wing+1)) and \
       all(v <= float(lows.iloc[i+j]) for j in range(1, wing+1)):
        swing_lows.append(v)

if not swing_highs: swing_highs = [float(highs.max())]
if not swing_lows:  swing_lows  = [float(lows.min())]

zone_width    = atr * 0.8
min_dist      = atr * 0.3
cand_resist   = sorted([v for v in set(swing_highs + swing_lows) if v > close + min_dist])
cand_sop      = sorted([v for v in set(swing_lows + swing_highs) if v < close - min_dist], reverse=True)
resist_pivot  = cand_resist[0] if cand_resist else float(highs.max())
support_pivot = cand_sop[0]    if cand_sop    else float(lows.min())

zrh = round(resist_pivot + zone_width * 0.25, 1)
zrl = round(resist_pivot - zone_width * 0.75, 1)
zsh = round(support_pivot + zone_width * 0.75, 1)
zsl = round(support_pivot - zone_width * 0.25, 1)
tol = atr * 0.4

print(f"   Resistencia: {zrl:.1f}-{zrh:.1f}  (pivot: {resist_pivot:.1f})")
print(f"   Soporte:     {zsl:.1f}-{zsh:.1f}  (pivot: {support_pivot:.1f})")
print(f"   Tolerancia:  ±{tol:.1f}")
print(f"   Todos swing highs (cercanos): {cand_resist[:5]}")
print(f"   Todos swing lows  (cercanos): {cand_sop[:5]}")

en_zona_resist  = (high >= zrl - tol) and (high <= zrh + tol)
en_zona_soporte = (low >= zsl - tol) and (low <= zsh + tol)
avg_range = float(df['total_range'].iloc[-6:-1].mean())
aprox_av = 3
aproximando_resist  = (zrl - close > 0 and zrl - close < avg_range * aprox_av and close > float(df['Close'].iloc[-5]))
aproximando_soporte = (close - zsh > 0 and close - zsh < avg_range * aprox_av and close < float(df['Close'].iloc[-5]))

print(f"\n5. CONDICIONES ZONA")
print(f"   en_zona_resist={en_zona_resist}   (high {high:.1f} vs zrl-tol={zrl-tol:.1f}..zrh+tol={zrh+tol:.1f})")
print(f"   en_zona_soporte={en_zona_soporte}  (low {low:.1f} vs zsl-tol={zsl-tol:.1f}..zsh+tol={zsh+tol:.1f})")
print(f"   aproximando_resist={aproximando_resist}  (dist {zrl-close:.1f} pts vs ventana {avg_range*aprox_av:.1f})")
print(f"   aproximando_soporte={aproximando_soporte} (dist {close-zsh:.1f} pts vs ventana {avg_range*aprox_av:.1f})")

# ── 4. Canal roto ────────────────────────────────────────────────
print("\n6. CANAL ROTO")
canal_al, canal_baj, linea_sop, linea_res = detectar_canal_roto(df, atr, lookback=40)
print(f"   canal_alcista_roto={canal_al}  linea_soporte={linea_sop:.1f}")
print(f"   canal_bajista_roto={canal_baj}  linea_resist={linea_res:.1f}")

# ── 5. ADX ───────────────────────────────────────────────────────
print("\n7. ADX / TENDENCIA")
adx_tendencia_fuerte = adx > 25
adx_lateral = adx < 20
penalizacion_adx = -3 if adx_lateral else 0
print(f"   ADX={adx:.1f} | lateral={adx_lateral} | tendencia_fuerte={adx_tendencia_fuerte}")
print(f"   Penalización score por ADX lateral: {penalizacion_adx}")

# ── 6. Score estimado mínimo ─────────────────────────────────────
print("\n8. SCORE MÍNIMO ESTIMADO")
score_base_sell = (
    (2 if en_zona_resist else 0) +
    (2 if canal_al else 0) +
    penalizacion_adx
)
score_base_buy = (
    (2 if en_zona_soporte else 0) +
    (2 if canal_baj else 0) +
    penalizacion_adx
)
print(f"   Score SELL mínimo (solo zona+canal+adx): {score_base_sell}")
print(f"   Score BUY mínimo  (solo zona+canal+adx): {score_base_buy}")
print(f"   Umbral ALERTA: >=5 | MEDIA: >=6 | FUERTE: >=9 | MAX: >=12")

print("\n" + "=" * 60)
print("RESUMEN CAUSAS POSIBLES DE 0 SEÑALES:")
causas = []
if not en_zona_resist and not aproximando_resist:
    causas.append(f"  ❌ Precio NO cerca de resistencia ({zrl-close:.0f} pts de distancia)")
if not en_zona_soporte and not aproximando_soporte:
    causas.append(f"  ❌ Precio NO cerca de soporte ({close-zsh:.0f} pts de distancia)")
if adx_lateral:
    causas.append(f"  ❌ ADX lateral ({adx:.1f} < 20) — penaliza score -3")
if score_base_sell < 5 and score_base_buy < 5:
    causas.append(f"  ❌ Score insuficiente: SELL={score_base_sell}, BUY={score_base_buy}")
for c in causas:
    print(c)
if not causas:
    print("  ⚠️ Condiciones básicas OK — revisar score completo y confluencia TF")
print("=" * 60)

# ── 7. Replay completo de TODAS las velas de hoy ─────────────────
print("\n" + "=" * 60)
print("REPLAY COMPLETO HOY (todas las velas, score real)")
print("=" * 60)

from core.indicators import (
    calcular_bollinger_bands, calcular_macd, calcular_obv,
    detectar_evening_star, detectar_morning_star, calcular_sr_multiples
)
import time as _time

df['vol_avg']  = df['Volume'].rolling(20).mean()
df['bb_upper'], df['bb_mid'], df['bb_lower'], df['bb_width'] = calcular_bollinger_bands(df['Close'])
df['macd'], df['macd_signal'], df['macd_hist'] = calcular_macd(df['Close'])
df['obv']     = calcular_obv(df)
df['obv_ema'] = calcular_ema(df['obv'], 20)
df['body']       = (df['Close'] - df['Open']).abs()
df['upper_wick'] = df['High'] - df[['Close','Open']].max(axis=1)
df['lower_wick'] = df[['Close','Open']].min(axis1=1) - df['Low']

# Velas de hoy UTC (índice -2 es la última cerrada)
from datetime import date as _date
today_utc = datetime.now(timezone.utc).date()
# Buscar todas las velas cuya fecha es hoy
hoy_idx = [i for i, ts in enumerate(df.index) if ts.date() == today_utc]
if not hoy_idx:
    print("  (sin velas de hoy todavía)")
else:
    for vi in hoy_idx:
        if vi < 4 or vi >= len(df) - 1:
            continue  # necesitamos iloc[-2] = vi → vi no puede ser el último
        _row  = df.iloc[vi]
        _prev = df.iloc[vi-1]
        _p2   = df.iloc[vi-2]
        _atr  = float(_row['atr'])
        _close= float(_row['Close'])
        _high = float(_row['High'])
        _low  = float(_row['Low'])
        _open = float(_row['Open'])
        _rsi  = float(_row['rsi'])
        _rsi_prev = float(_prev['rsi'])
        _adx  = float(_row['adx'])
        _di_p = float(_row['di_plus'])
        _di_m = float(_row['di_minus'])
        _vol  = float(_row['Volume'])
        _vol_avg = float(_row['vol_avg'])
        _ema_f = float(_row['ema_fast'])
        _ema_s = float(_row['ema_slow'])
        _ema_t = float(_row['ema_trend'])
        _macd  = float(_row['macd'])
        _macd_sig = float(_row['macd_signal'])
        _macd_h   = float(_row['macd_hist'])
        _macd_h_prev = float(_prev['macd_hist'])
        _obv   = float(_row['obv'])
        _obv_p = float(_prev['obv'])
        _obv_e = float(_row['obv_ema'])
        _bb_u  = float(_row['bb_upper'])
        _bb_l  = float(_row['bb_lower'])
        _body  = float(_row['body'])
        _uwi   = float(_row['upper_wick'])
        _lwi   = float(_row['lower_wick'])
        _trng  = float(_row['total_range'])
        _is_bear = _close < _open
        _is_bull = _close > _open

        # S/R para esta vela (lookback en historia anterior)
        _lkb = 150
        _wing = 3
        _highs2 = df['High'].iloc[max(0,vi-_lkb-1):vi]
        _lows2  = df['Low'].iloc[max(0,vi-_lkb-1):vi]
        _sh2, _sl2 = [], []
        for _i in range(_wing, len(_highs2)-_wing):
            _v = float(_highs2.iloc[_i])
            if all(_v >= float(_highs2.iloc[_i-_j]) for _j in range(1,_wing+1)) and \
               all(_v >= float(_highs2.iloc[_i+_j]) for _j in range(1,_wing+1)):
                _sh2.append(_v)
        for _i in range(_wing, len(_lows2)-_wing):
            _v = float(_lows2.iloc[_i])
            if all(_v <= float(_lows2.iloc[_i-_j]) for _j in range(1,_wing+1)) and \
               all(_v <= float(_lows2.iloc[_i+_j]) for _j in range(1,_wing+1)):
                _sl2.append(_v)
        if not _sh2: _sh2 = [float(_highs2.max())]
        if not _sl2: _sl2 = [float(_lows2.min())]
        _zw = _atr * 0.8; _md = _atr * 0.3
        _cr = sorted([v for v in set(_sh2+_sl2) if v > _close+_md])
        _cs = sorted([v for v in set(_sl2+_sh2) if v < _close-_md], reverse=True)
        _rp = _cr[0] if _cr else float(_highs2.max())
        _sp = _cs[0] if _cs else float(_lows2.min())
        _zrh = _rp + _zw*0.25; _zrl = _rp - _zw*0.75
        _zsh = _sp + _zw*0.75; _zsl = _sp - _zw*0.25
        _tol = _atr * 0.4
        _en_r = (_high >= _zrl-_tol) and (_high <= _zrh+_tol)
        _en_s = (_low  >= _zsl-_tol) and (_low  <= _zsh+_tol)

        # Scoring SELL
        _lkb5 = 5
        _phi = _high > float(df['High'].iloc[max(0,vi-_lkb5-2):vi-1].max())
        _rl  = _rsi < float(df['rsi'].iloc[max(0,vi-_lkb5-2):vi-1].max()) if vi > _lkb5+2 else False
        _div_baj = _phi and _rl and _rsi > 50
        _adx_tend = _adx > 25
        _adx_lat  = _adx < 20
        _adx_baj  = (_di_m > _di_p) and _adx_tend
        _shoot = _is_bear and _uwi > _body*2 and _lwi < _body*0.3 and _en_r
        _be_eng = _is_bear and _open >= float(_prev['High']) and _close <= float(_prev['Low']) and _en_r
        _vela_rec = _shoot or _be_eng or (_is_bear and _body > _trng*0.8 and _en_r) or (_body < _trng*0.1 and _en_r and _uwi > _body*2)
        _rsi_ag = (_rsi >= 55) and (_rsi < _rsi_prev)
        _rsi_sob = _rsi >= 70
        _eve = detectar_evening_star(df, vi)
        _macd_cb = (_macd < _macd_sig) and (_macd_h < 0) and (_macd_h_prev >= 0)
        _bb_sup = _close >= _bb_u or _high >= _bb_u
        _emas_baj = _ema_f < _ema_s
        _bajo200  = _close < _ema_t
        _struct_baj = ((_high < float(_prev['High']) and float(_prev['High']) < float(_p2['High'])) or
                       (_low  < float(_prev['Low'])  and float(_prev['Low'])  < float(_p2['Low'])))
        _obv_dec = _obv < _obv_p and _obv < _obv_e
        _vol_ar = _vol > _vol_avg * 1.2
        _canal_al, _, _, _ = detectar_canal_roto(df.iloc[:vi+1], _atr, lookback=40)

        ss = (
            (2 if _en_r          else 0) + (2 if _vela_rec      else 0) + (2 if _vol_ar        else 0) +
            (1 if _rsi_ag        else 0) + (1 if _rsi_sob       else 0) + (1 if _div_baj       else 0) +
            (1 if _emas_baj      else 0) + (1 if _struct_baj    else 0) + (1 if _bajo200       else 0) +
            (2 if _bb_sup        else 0) + (2 if _eve           else 0) + (2 if _macd_cb       else 0) +
            (2 if _adx_baj       else 0) + (1 if _obv_dec       else 0) + (2 if _canal_al      else 0)
        )
        if _adx_lat: ss = max(0, ss-3)

        # Scoring BUY
        _pnl = _low < float(df['Low'].iloc[max(0,vi-_lkb5-2):vi-1].min())
        _rhl = _rsi > float(df['rsi'].iloc[max(0,vi-_lkb5-2):vi-1].min()) if vi > _lkb5+2 else False
        _div_alc = _pnl and _rhl and _rsi < 50
        _adx_alc = (_di_p > _di_m) and _adx_tend
        _hammer = _is_bull and _lwi > _body*2 and _uwi < _body*0.3 and _en_s
        _bu_eng = _is_bull and _open <= float(_prev['Low']) and _close >= float(_prev['High']) and _en_s
        _vela_reb = _hammer or _bu_eng or (_is_bull and _body > _trng*0.8 and _en_s) or (_body < _trng*0.1 and _en_s and _lwi > _body*2)
        _rsi_bg = (_rsi <= 45) and (_rsi > _rsi_prev)
        _rsi_svt = _rsi <= 30
        _mor = detectar_morning_star(df, vi)
        _macd_ca = (_macd > _macd_sig) and (_macd_h > 0) and (_macd_h_prev <= 0)
        _bb_inf = _close <= _bb_l or _low <= _bb_l
        _emas_alc = _ema_f > _ema_s
        _sob200   = _close > _ema_t
        _struct_alc = ((_high > float(_prev['High']) and float(_prev['High']) > float(_p2['High'])) or
                       (_low  > float(_prev['Low'])  and float(_prev['Low'])  > float(_p2['Low'])))
        _obv_cr = _obv > _obv_p and _obv > _obv_e
        _, _canal_baj2, _, _ = detectar_canal_roto(df.iloc[:vi+1], _atr, lookback=40)

        sb = (
            (2 if _en_s          else 0) + (2 if _vela_reb      else 0) + (2 if _vol_ar        else 0) +
            (1 if _rsi_bg        else 0) + (1 if _rsi_svt       else 0) + (1 if _div_alc       else 0) +
            (1 if _emas_alc      else 0) + (1 if _struct_alc    else 0) + (1 if _sob200        else 0) +
            (2 if _bb_inf        else 0) + (2 if _mor           else 0) + (2 if _macd_ca       else 0) +
            (2 if _adx_alc       else 0) + (1 if _obv_cr        else 0) + (2 if _canal_baj2    else 0)
        )
        if _adx_lat: sb = max(0, sb-3)

        _hora = df.index[vi].strftime('%H:%M')
        _seal_s = "✅SELL" if ss >= 5 else ("~" if ss >= 3 else "·")
        _seal_b = "✅BUY"  if sb >= 5 else ("~" if sb >= 3 else "·")
        _zona   = "R" if _en_r else ("Sr" if _en_s else " ")
        # Bloqueo PMI 14:00 UTC hoy
        _hora_dt = df.index[vi]
        _bloqueado = ""
        from datetime import timezone as _tz2
        import bisect as _b2
        from services.economic_calendar import _EVENTOS_DT, hay_evento_impacto
        try:
            _bk, _bd = hay_evento_impacto.__wrapped__(_hora_dt.to_pydatetime()) if hasattr(hay_evento_impacto,'__wrapped__') else (False,"")
        except:
            _bk = False
        # Chequeo manual del PMI hoy
        _pmi_dt = datetime(2026,4,22,14,0,tzinfo=timezone.utc)
        if abs((_hora_dt.to_pydatetime() - _pmi_dt).total_seconds()) <= 3600:
            _bloqueado = " [PMI BLOQUEADO]"

        print(f"  {_hora} | C={_close:.0f} | SELL={ss:2d} {_seal_s} | BUY={sb:2d} {_seal_b} | zona={_zona} | RSI={_rsi:.0f} ADX={_adx:.0f}{_bloqueado}")

