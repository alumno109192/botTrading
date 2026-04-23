import os
from services import tf_bias
from services.dxy_bias import get_dxy_bias, ajustar_score_por_dxy
from services.economic_calendar import obtener_aviso_macro
from services.news_monitor import obtener_sesgo_actual
from adapters.data_provider import get_ohlcv

import yfinance as yf
import pandas as pd
import numpy as np
import requests
import time
import json
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()
from adapters.telegram import enviar_telegram as _enviar_telegram_base

_aviso_macro = ""

def enviar_telegram(mensaje):
    sufijo = f"\n⚠️ <b>Evento macro próximo:</b> {_aviso_macro}" if _aviso_macro else ""
    return _enviar_telegram_base(mensaje + sufijo, TELEGRAM_THREAD_ID)

db = None
try:
    turso_url = os.environ.get('TURSO_DATABASE_URL')
    turso_token = os.environ.get('TURSO_AUTH_TOKEN')
    if turso_url and turso_token:
        from adapters.database import DatabaseManager
        db = DatabaseManager()
        print("✅ Sistema de tracking de BD activado (Gold 1H)")
    else:
        print("⚠️  Variables Turso no configuradas - Sistema funcionará sin tracking de BD")
except Exception as e:
    print(f"⚠️  No se pudo inicializar BD: {e}")
    db = None

TELEGRAM_TOKEN   = os.environ.get('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID   = os.environ.get('TELEGRAM_CHAT_ID')
TELEGRAM_THREAD_ID = int(os.environ.get('THREAD_ID_INTRADAY') or 0) or None

# Verificar cada 60 segundos (velas 1H cierran cada 60 min)
CHECK_INTERVAL = 60

# ══════════════════════════════════════
# PARÁMETROS — GOLD 1H INTRADÍA
# ══════════════════════════════════════
SIMBOLOS = {
    'XAUUSD': {
        'ticker_yf':          'GC=F',
        # Zonas S/R calculadas automáticamente en analizar() — sin mantenimiento manual
        'sr_lookback':        150,          # 150 velas 1H ≈ 6 días de historia
        'sr_zone_mult':       0.8,          # ancho de zona = atr × 0.8
        'limit_offset_pct':   0.3,
        'anticipar_velas':    3,
        'cancelar_dist':      1.0,
        'rsi_length':         14,     # Estándar para 1H
        'rsi_min_sell':       55.0,   # Igual que oro 4H (metal precioso)
        'rsi_max_buy':        45.0,
        'ema_fast_len':       9,      # Estándar 1H
        'ema_slow_len':       21,
        'ema_trend_len':      200,
        'atr_length':         14,
        'atr_sl_mult':        1.0,    # SL ajustado para intradía
        'atr_tp1_mult':       1.5,    # TP1: 1.5× ATR (intradía alcanzable)
        'atr_tp2_mult':       2.5,    # TP2: 2.5× ATR
        'atr_tp3_mult':       4.0,    # TP3: 4.0× ATR (objetivo ambicioso)
        'vol_mult':           1.2,
    }
}

alertas_enviadas = {}
ultimo_analisis  = {}


from core.indicators import (
    calcular_rsi, calcular_ema, calcular_atr,
    calcular_bollinger_bands, calcular_macd, calcular_obv, calcular_adx,
    detectar_evening_star, detectar_morning_star,
    detectar_canal_roto, calcular_sr_multiples,
)

def calcular_zonas_sr(df, atr, lookback, zone_mult):
    """
    Detecta zonas S/R usando swing highs/lows locales (no solo máx/mín absoluto).
    Principio: soporte roto = nueva resistencia; resistencia rota = nuevo soporte.
    Selecciona el pivote más cercano al precio en cada dirección.
    Returns: (zrl, zrh, zsl, zsh)
    """
    close      = float(df['Close'].iloc[-1])
    zone_width = atr * zone_mult
    wing       = 3  # velas a cada lado para confirmar swing
    highs = df['High'].iloc[-lookback-1:-1]
    lows  = df['Low'].iloc[-lookback-1:-1]

    swing_highs = []
    for i in range(wing, len(highs) - wing):
        val = float(highs.iloc[i])
        if all(val >= float(highs.iloc[i-j]) for j in range(1, wing+1)) and \
           all(val >= float(highs.iloc[i+j]) for j in range(1, wing+1)):
            swing_highs.append(val)

    swing_lows = []
    for i in range(wing, len(lows) - wing):
        val = float(lows.iloc[i])
        if all(val <= float(lows.iloc[i-j]) for j in range(1, wing+1)) and \
           all(val <= float(lows.iloc[i+j]) for j in range(1, wing+1)):
            swing_lows.append(val)

    if not swing_highs: swing_highs = [float(highs.max())]
    if not swing_lows:  swing_lows  = [float(lows.min())]

    min_dist = atr * 0.3
    # Resistencia: cualquier pivote encima (incluye soportes rotos)
    candidatos_resist = [v for v in set(swing_highs + swing_lows) if v > close + min_dist]
    # Soporte: cualquier pivote debajo (incluye resistencias rotas)
    candidatos_sop    = [v for v in set(swing_lows + swing_highs) if v < close - min_dist]

    resist_pivot  = min(candidatos_resist) if candidatos_resist else float(highs.max())
    support_pivot = max(candidatos_sop)    if candidatos_sop    else float(lows.min())

    zrh = round(resist_pivot + zone_width * 0.25, 2)
    zrl = round(resist_pivot - zone_width * 0.75, 2)
    zsh = round(support_pivot + zone_width * 0.75, 2)
    zsl = round(support_pivot - zone_width * 0.25, 2)
    return zrl, zrh, zsl, zsh


def analizar(simbolo, params):
    simbolo_db = f"{simbolo}_1H"

    # ── Aviso calendario económico (no bloquea, solo advierte en el mensaje) ──
    global _aviso_macro
    _aviso_macro = obtener_aviso_macro(60, '1H', simbolo)

    print(f"\n🔍 Analizando {simbolo} [1H intradía]...")

    try:
        df_5m, is_delayed = get_ohlcv(params['ticker_yf'], period='7d', interval='5m')
        df = df_5m.resample('1h').agg({'Open': 'first', 'High': 'max', 'Low': 'min', 'Close': 'last', 'Volume': 'sum'}).dropna()
        if is_delayed:
            print("  ⚠️  [1H] Datos con 15 min de delay (yfinance). Configura TWELVE_DATA_API_KEY para tiempo real.")
        if df.empty or len(df) < 80:
            print(f"⚠️ Datos insuficientes para {simbolo} 1H")
            return
    except Exception as e:
        print(f"❌ Error descargando {simbolo}: {e}")
        return

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.copy()

    df['rsi']       = calcular_rsi(df['Close'], params['rsi_length'])
    df['ema_fast']  = calcular_ema(df['Close'], params['ema_fast_len'])
    df['ema_slow']  = calcular_ema(df['Close'], params['ema_slow_len'])
    df['ema_trend'] = calcular_ema(df['Close'], params['ema_trend_len'])
    df['atr']       = calcular_atr(df, params['atr_length'])
    df['vol_avg']   = df['Volume'].rolling(20).mean()
    df['bb_upper'], df['bb_mid'], df['bb_lower'], df['bb_width'] = calcular_bollinger_bands(df['Close'], length=20)
    df['macd'], df['macd_signal'], df['macd_hist'] = calcular_macd(df['Close'])
    df['obv']       = calcular_obv(df)
    df['obv_ema']   = calcular_ema(df['obv'], 20)
    df['adx'], df['di_plus'], df['di_minus'] = calcular_adx(df)
    df['body']        = (df['Close'] - df['Open']).abs()
    df['upper_wick']  = df['High'] - df[['Close','Open']].max(axis=1)
    df['lower_wick']  = df[['Close','Open']].min(axis=1) - df['Low']
    df['total_range'] = df['High'] - df['Low']
    df['is_bearish']  = df['Close'] < df['Open']
    df['is_bullish']  = df['Close'] > df['Open']

    row  = df.iloc[-2]; prev = df.iloc[-3]; p2 = df.iloc[-4]
    close = row['Close']; high = row['High']; low = row['Low']; open_ = row['Open']; vol = row['Volume']
    rsi = row['rsi']; rsi_prev = prev['rsi']
    ema_fast = row['ema_fast']; ema_slow = row['ema_slow']; ema_trend = row['ema_trend']
    atr = row['atr']; vol_avg = row['vol_avg']
    bb_upper = row['bb_upper']; bb_lower = row['bb_lower']
    macd = row['macd']; macd_signal = row['macd_signal']
    macd_hist = row['macd_hist']; macd_hist_prev = prev['macd_hist']
    obv = row['obv']; obv_prev = prev['obv']; obv_ema = row['obv_ema']
    adx = row['adx']; di_plus = row['di_plus']; di_minus = row['di_minus']
    body = row['body']; upper_wick = row['upper_wick']; lower_wick = row['lower_wick']
    total_range = row['total_range']; is_bearish = row['is_bearish']; is_bullish = row['is_bullish']

    # df.iloc[:-1]: excluir vela viva para que close = última vela cerrada
    _df_cerrado = df.iloc[:-1]
    zrl, zrh, zsl, zsh = calcular_zonas_sr(_df_cerrado, atr, params['sr_lookback'], params['sr_zone_mult'])

    # ── Niveles S/R múltiples para TPs estructurales ──────────────────────────
    soportes_sr, resistencias_sr = calcular_sr_multiples(
        _df_cerrado, atr, params['sr_lookback'], params['sr_zone_mult'], n_niveles=5
    )

    # ── Detección de canal roto ────────────────────────────────────────────────
    canal_alcista_roto, canal_bajista_roto, linea_soporte_canal, linea_resist_canal = \
        detectar_canal_roto(df, atr, lookback=40)

    # Persistir rotura en memoria (TTL 4h) para que el retest se detecte
    # aunque en ese momento el precio ya esté cerca de la línea y no parezca "roto"
    if canal_alcista_roto or canal_bajista_roto:
        tf_bias.publicar_canal_1h(simbolo, canal_alcista_roto, canal_bajista_roto,
                                   linea_soporte_canal, linea_resist_canal)

    # Recuperar estado persistido: durante el retest el canal ya NO parece roto
    # (el precio está cerca de la línea), pero la memoria dice que sí lo fue
    _mem_canal_1h = tf_bias.obtener_canal_1h(simbolo)
    if _mem_canal_1h and not canal_alcista_roto and not canal_bajista_roto:
        canal_alcista_roto  = _mem_canal_1h['alcista_roto']
        canal_bajista_roto  = _mem_canal_1h['bajista_roto']
        linea_soporte_canal = _mem_canal_1h['linea_soporte']
        linea_resist_canal  = _mem_canal_1h['linea_resist']

    if canal_alcista_roto:
        print(f"  🔻 Canal alcista ROTO — sesgo bajista reforzado (soporte canal ${linea_soporte_canal:.2f})")
    if canal_bajista_roto:
        print(f"  🔺 Canal bajista ROTO — sesgo alcista reforzado (resist canal ${linea_resist_canal:.2f})")

    tol  = round(atr * 0.4, 2)   # tolerancia dinámica: 40% del ATR
    lop = params['limit_offset_pct']; cd = params['cancelar_dist']
    av   = params['anticipar_velas']; vm = params['vol_mult']
    rsms = params['rsi_min_sell']; rsmb = params['rsi_max_buy']; asm = params['atr_sl_mult']
    print(f"  📍 Zonas auto — Resist: ${zrl:.1f}-${zrh:.1f} | Soporte: ${zsl:.1f}-${zsh:.1f}")

    sell_limit = zrl + (zrh - zrl) * (lop / 100 * 10)
    buy_limit  = zsh - (zsh - zsl) * (lop / 100 * 10)

    # ── SL anclado a estructura swing (último swing high/low + buffer ATR) ────
    swing_wing  = 3
    sub_sl      = df.iloc[-30:]
    swing_h_vals = []
    swing_l_vals = []
    for i in range(swing_wing, len(sub_sl) - swing_wing):
        h = float(sub_sl['High'].iloc[i])
        l = float(sub_sl['Low'].iloc[i])
        if all(h >= float(sub_sl['High'].iloc[i-j]) for j in range(1, swing_wing+1)) and \
           all(h >= float(sub_sl['High'].iloc[i+j]) for j in range(1, swing_wing+1)):
            swing_h_vals.append(h)
        if all(l <= float(sub_sl['Low'].iloc[i-j]) for j in range(1, swing_wing+1)) and \
           all(l <= float(sub_sl['Low'].iloc[i+j]) for j in range(1, swing_wing+1)):
            swing_l_vals.append(l)

    # Para SELL: SL en último swing HIGH por encima de la entrada + 0.3×ATR buffer
    # Cap: si el swing está muy lejos, usar el techo de zona + buffer (invalidación natural)
    sl_zona_sell = round(zrh + atr * 0.5, 2)
    sl_swing_sell_candidates = [v for v in swing_h_vals if v > sell_limit]
    if sl_swing_sell_candidates:
        sl_venta = round(min(sl_swing_sell_candidates) + atr * 0.3, 2)
    else:
        sl_venta = round(sell_limit + atr * asm, 2)  # fallback ATR
    sl_venta = min(sl_venta, sl_zona_sell)  # nunca más lejos que la zona rota

    # Para BUY: SL en último swing LOW por debajo de la entrada - 0.3×ATR buffer
    # Cap: si el swing está muy lejos, usar el suelo de zona + buffer (invalidación natural)
    sl_zona_buy = round(zsl - atr * 0.5, 2)
    sl_swing_buy_candidates = [v for v in swing_l_vals if v < buy_limit]
    if sl_swing_buy_candidates:
        sl_compra = round(max(sl_swing_buy_candidates) - atr * 0.3, 2)
    else:
        sl_compra = round(buy_limit - atr * asm, 2)   # fallback ATR
    sl_compra = max(sl_compra, sl_zona_buy)  # nunca más lejos que la zona rota

    # ── TPs en zonas S/R reales (con fallback a ATR si no hay suficientes niveles) ──
    def _tp_desde_sr(niveles, n, fallback):
        return round(niveles[n-1], 2) if len(niveles) >= n else round(fallback, 2)

    def _tp1_viable_sell(soportes, entry, sl, min_rr, fallback):
        """Primer soporte que dé R:R >= min_rr; si ninguno califica, usa fallback ATR."""
        dist_sl = abs(sl - entry)
        if dist_sl > 0:
            for nivel in soportes:   # ya ordenados nearest-first (sorted reverse=True)
                if abs(nivel - entry) / dist_sl >= min_rr:
                    return round(nivel, 2)
        return round(fallback, 2)

    def _tp1_viable_buy(resistencias, entry, sl, min_rr, fallback):
        """Primera resistencia que dé R:R >= min_rr; si ninguna califica, usa fallback ATR."""
        dist_sl = abs(sl - entry)
        resis_sobre = sorted([v for v in resistencias if v > entry])
        if dist_sl > 0:
            for nivel in resis_sobre:  # ascendentes = nearest-first
                if abs(nivel - entry) / dist_sl >= min_rr:
                    return round(nivel, 2)
        return round(fallback, 2)

    tp1_v = _tp1_viable_sell(soportes_sr, sell_limit, sl_venta, 1.2,
                             sell_limit - atr * params['atr_tp1_mult'])
    tp2_v = _tp_desde_sr(soportes_sr, 2, sell_limit - atr * params['atr_tp2_mult'])
    tp3_v = _tp_desde_sr(soportes_sr, 3, sell_limit - atr * params['atr_tp3_mult'])
    tp1_c = _tp1_viable_buy(resistencias_sr, buy_limit, sl_compra, 1.2,
                            buy_limit + atr * params['atr_tp1_mult'])
    tp2_c = _tp_desde_sr(sorted([v for v in resistencias_sr if v > buy_limit]), 2,
                         buy_limit + atr * params['atr_tp2_mult'])
    tp3_c = _tp_desde_sr(sorted([v for v in resistencias_sr if v > buy_limit]), 3,
                         buy_limit + atr * params['atr_tp3_mult'])

    avg_candle_range    = df['total_range'].iloc[-6:-1].mean()
    aproximando_resist  = (zrl - close > 0 and zrl - close < avg_candle_range * av and close > float(df['Close'].iloc[-5]))
    aproximando_soporte = (close - zsh > 0 and close - zsh < avg_candle_range * av and close < float(df['Close'].iloc[-5]))
    en_zona_resist      = (high >= zrl - tol) and (high <= zrh + tol)
    en_zona_soporte     = (low  >= zsl - tol) and (low  <= zsh + tol)
    cancelar_sell       = close > zrh * (1 + cd / 100)
    cancelar_buy        = close < zsl * (1 - cd / 100)

    # ── DETECCIÓN RETEST DE CANAL ROTO ────────────────────────────────────────
    # Tras romper el canal, el precio retrocede hacia la línea rota (ahora resistencia/soporte)
    # Este es el setup de alta probabilidad: entrada en el retest con SL ajustado
    _tol_canal = atr * 1.2   # zona de retest: ±1.2 ATR alrededor de la línea del canal

    # SELL retest: canal alcista roto + precio rebota hacia línea (ahora resistencia)
    retest_canal_sell = (
        canal_alcista_roto and
        abs(high - linea_soporte_canal) <= _tol_canal and   # precio cerca de la línea rota
        close < linea_soporte_canal and                      # cierra POR DEBAJO (rechazo)
        is_bearish and                                       # vela bajista de rechazo
        rsi < 65                                             # RSI no en sobrecompra extrema (no perseguir)
    )
    # BUY retest: canal bajista roto + precio cae hacia línea (ahora soporte)
    retest_canal_buy = (
        canal_bajista_roto and
        abs(low - linea_resist_canal) <= _tol_canal and     # precio cerca de la línea rota
        close > linea_resist_canal and                       # cierra POR ENCIMA (rebote)
        is_bullish and                                       # vela alcista de rebote
        rsi > 35                                             # RSI no en sobreventa extrema
    )

    if retest_canal_sell:
        print(f"  🎯 RETEST CANAL SELL detectado — línea ${linea_soporte_canal:.2f}, precio ${close:.2f}")
    if retest_canal_buy:
        print(f"  🎯 RETEST CANAL BUY detectado — línea ${linea_resist_canal:.2f}, precio ${close:.2f}")

    # ── SCORING VENTA ──────────────────────────────────────────
    intento_rotura_fallido = (high >= zrl) and (close < zrl)
    shooting_star     = is_bearish and upper_wick > body*2 and lower_wick < body*0.3 and en_zona_resist
    bearish_engulfing = is_bearish and open_ >= float(prev['High']) and close <= float(prev['Low']) and en_zona_resist
    bearish_marubozu  = is_bearish and body > total_range*0.8 and en_zona_resist
    doji_resist       = body < total_range*0.1 and en_zona_resist and upper_wick > body*2
    vela_rechazo      = shooting_star or bearish_engulfing or bearish_marubozu or doji_resist
    rsi_alto_girando  = (rsi >= rsms) and (rsi < rsi_prev)
    rsi_sobrecompra   = rsi >= 70
    lookback          = 5
    price_new_high      = high > float(df['High'].iloc[-lookback-2:-2].max())
    rsi_lower_high      = rsi  < float(df['rsi'].iloc[-lookback-2:-2].max())
    divergencia_bajista = price_new_high and rsi_lower_high and rsi > 50
    vol_alto_rechazo    = vol > vol_avg * vm
    vol_decreciente     = (vol < float(prev['Volume'])) and (float(prev['Volume']) < float(p2['Volume'])) and is_bullish
    emas_bajistas       = ema_fast < ema_slow
    bajo_ema200         = close < ema_trend
    estructura_bajista  = ((high < float(prev['High']) and float(prev['High']) < float(p2['High'])) or
                           (low  < float(prev['Low'])  and float(prev['Low'])  < float(p2['Low'])))
    bb_toca_superior        = close >= bb_upper or high >= bb_upper
    macd_cruce_bajista      = (macd < macd_signal) and (macd_hist < 0) and (macd_hist_prev >= 0)
    macd_divergencia_bajista = price_new_high and (macd < float(df['macd'].iloc[-lookback-2:-2].max()))
    macd_negativo           = macd < 0
    adx_tendencia_fuerte    = adx > 25
    adx_bajista             = (di_minus > di_plus) and adx_tendencia_fuerte
    adx_lateral             = adx < 20
    obv_divergencia_bajista = price_new_high and (obv < float(df['obv'].iloc[-lookback-2:-2].max()))
    obv_decreciente         = obv < obv_prev and obv < obv_ema
    evening_star            = detectar_evening_star(df, len(df) - 2)

    score_sell = (
        (2 if en_zona_resist           else 0) +
        (2 if vela_rechazo             else 0) +
        (2 if vol_alto_rechazo         else 0) +
        (1 if rsi_alto_girando         else 0) +
        (1 if rsi_sobrecompra          else 0) +
        (1 if divergencia_bajista      else 0) +
        (1 if emas_bajistas            else 0) +
        (1 if estructura_bajista       else 0) +
        (1 if intento_rotura_fallido   else 0) +
        (1 if vol_decreciente          else 0) +
        (1 if (shooting_star and vol_alto_rechazo)      else 0) +
        (1 if (divergencia_bajista and rsi_sobrecompra) else 0) +
        (1 if bajo_ema200              else 0) +
        (2 if bb_toca_superior         else 0) +
        (2 if evening_star             else 0) +
        (2 if macd_cruce_bajista       else 0) +
        (2 if adx_bajista              else 0) +
        (1 if macd_divergencia_bajista else 0) +
        (1 if obv_divergencia_bajista  else 0) +
        (1 if obv_decreciente          else 0) +
        (1 if macd_negativo            else 0) +
        (2 if canal_alcista_roto       else 0)   # canal alcista roto → sesgo bajista fuerte
    )
    if adx_lateral: score_sell = max(0, score_sell - 3)

    # ── SCORING COMPRA ─────────────────────────────────────────
    intento_caida_fallido   = (low <= zsh) and (close > zsh)
    hammer            = is_bullish and lower_wick > body*2 and upper_wick < body*0.3 and en_zona_soporte
    bullish_engulfing = is_bullish and open_ <= float(prev['Low']) and close >= float(prev['High']) and en_zona_soporte
    bullish_marubozu  = is_bullish and body > total_range*0.8 and en_zona_soporte
    doji_soporte      = body < total_range*0.1 and en_zona_soporte and lower_wick > body*2
    vela_rebote       = hammer or bullish_engulfing or bullish_marubozu or doji_soporte
    rsi_bajo_girando  = (rsi <= rsmb) and (rsi > rsi_prev)
    rsi_sobreventa    = rsi <= 30
    price_new_low       = low < float(df['Low'].iloc[-lookback-2:-2].min())
    rsi_higher_low      = rsi > float(df['rsi'].iloc[-lookback-2:-2].min())
    divergencia_alcista = price_new_low and rsi_higher_low and rsi < 50
    vol_alto_rebote      = vol > vol_avg * vm
    vol_decreciente_sell = (vol < float(prev['Volume'])) and (float(prev['Volume']) < float(p2['Volume'])) and is_bearish
    emas_alcistas       = ema_fast > ema_slow
    sobre_ema200        = close > ema_trend
    estructura_alcista  = ((high > float(prev['High']) and float(prev['High']) > float(p2['High'])) or
                           (low  > float(prev['Low'])  and float(prev['Low'])  > float(p2['Low'])))
    bb_toca_inferior        = close <= bb_lower or low <= bb_lower
    macd_cruce_alcista      = (macd > macd_signal) and (macd_hist > 0) and (macd_hist_prev <= 0)
    macd_divergencia_alcista = price_new_low and (macd > float(df['macd'].iloc[-lookback-2:-2].min()))
    macd_positivo           = macd > 0
    adx_alcista             = (di_plus > di_minus) and adx_tendencia_fuerte
    obv_divergencia_alcista = price_new_low and (obv > float(df['obv'].iloc[-lookback-2:-2].min()))
    obv_creciente           = obv > obv_prev and obv > obv_ema
    morning_star            = detectar_morning_star(df, len(df) - 2)

    score_buy = (
        (2 if en_zona_soporte            else 0) +
        (2 if vela_rebote                else 0) +
        (2 if vol_alto_rebote            else 0) +
        (1 if rsi_bajo_girando           else 0) +
        (1 if rsi_sobreventa             else 0) +
        (1 if divergencia_alcista        else 0) +
        (1 if emas_alcistas              else 0) +
        (1 if estructura_alcista         else 0) +
        (1 if intento_caida_fallido      else 0) +
        (1 if vol_decreciente_sell       else 0) +
        (1 if (hammer and vol_alto_rebote)              else 0) +
        (1 if (divergencia_alcista and rsi_sobreventa)  else 0) +
        (1 if sobre_ema200               else 0) +
        (2 if bb_toca_inferior           else 0) +
        (2 if morning_star               else 0) +
        (2 if macd_cruce_alcista         else 0) +
        (2 if adx_alcista                else 0) +
        (1 if macd_divergencia_alcista   else 0) +
        (1 if obv_divergencia_alcista    else 0) +
        (1 if obv_creciente              else 0) +
        (1 if macd_positivo              else 0) +
        (2 if canal_bajista_roto         else 0)   # canal bajista roto → sesgo alcista fuerte
    )
    if adx_lateral: score_buy = max(0, score_buy - 3)

    # ── Ajuste por sesgo DXY (correlación inversa Gold/USD) ──
    dxy_bias = get_dxy_bias()
    score_buy, score_sell = ajustar_score_por_dxy(score_buy, score_sell, dxy_bias)

    # ── Sesgo fundamental de noticias ──────────────────────────────────────────
    _noticias      = obtener_sesgo_actual()
    _sesgo_news    = _noticias.get('conclusion', 'ESPERAR')   # BUSCAR_COMPRAS | BUSCAR_VENTAS | ESPERAR
    _sesgo_etiq    = _noticias.get('sesgo', 'NEUTRAL')
    _sesgo_score   = _noticias.get('score_medio', 0.0)
    # Ajuste suave: +1 si noticias alinean con la señal, -1 si contradicen (máx ±1)
    if _sesgo_news == 'BUSCAR_COMPRAS':
        score_buy  = min(score_buy  + 1, 23)
        score_sell = max(score_sell - 1, 0)
    elif _sesgo_news == 'BUSCAR_VENTAS':
        score_sell = min(score_sell + 1, 23)
        score_buy  = max(score_buy  - 1, 0)

    # ── Contexto multi-TF: ¿es el canal roto un PULLBACK dentro de tendencia? ──
    _bias_1d   = tf_bias.obtener_sesgo(simbolo, '1D')
    _bias_1w   = tf_bias.obtener_sesgo(simbolo, '1W')
    _bias_4h   = tf_bias.obtener_sesgo(simbolo, '4H')
    _canal_4h  = tf_bias.obtener_canal_4h(simbolo)
    _canal_1d  = tf_bias.obtener_canal_1d(simbolo)
    _canal_1w  = tf_bias.obtener_canal_1w(simbolo)
    _dir_1d    = _bias_1d['bias']  if _bias_1d  else tf_bias.BIAS_NEUTRAL
    _dir_1w    = _bias_1w['bias']  if _bias_1w  else tf_bias.BIAS_NEUTRAL
    _dir_4h    = _bias_4h['bias']  if _bias_4h  else tf_bias.BIAS_NEUTRAL

    # Estado de canal por TF superior
    _canal_4h_alcista_roto = _canal_4h['alcista_roto']  if _canal_4h else False
    _canal_4h_bajista_roto = _canal_4h['bajista_roto']  if _canal_4h else False
    _linea_canal_4h_sop    = _canal_4h['linea_soporte'] if _canal_4h else 0.0
    _linea_canal_4h_res    = _canal_4h['linea_resist']  if _canal_4h else 0.0

    _canal_1d_alcista_roto = _canal_1d['alcista_roto']  if _canal_1d else False
    _canal_1d_bajista_roto = _canal_1d['bajista_roto']  if _canal_1d else False
    _linea_canal_1d_sop    = _canal_1d['linea_soporte'] if _canal_1d else 0.0
    _linea_canal_1d_res    = _canal_1d['linea_resist']  if _canal_1d else 0.0

    _canal_1w_alcista_roto = _canal_1w['alcista_roto']  if _canal_1w else False
    _canal_1w_bajista_roto = _canal_1w['bajista_roto']  if _canal_1w else False
    _linea_canal_1w_sop    = _canal_1w['linea_soporte'] if _canal_1w else 0.0
    _linea_canal_1w_res    = _canal_1w['linea_resist']  if _canal_1w else 0.0

    # HTF alcista/bajista por sesgo de precio
    _htf_alcista = (_dir_1d == tf_bias.BIAS_BULLISH or
                    _dir_1w == tf_bias.BIAS_BULLISH or
                    _dir_4h == tf_bias.BIAS_BULLISH)
    _htf_bajista = (_dir_1d == tf_bias.BIAS_BEARISH or
                    _dir_1w == tf_bias.BIAS_BEARISH or
                    _dir_4h == tf_bias.BIAS_BEARISH)

    # Confirmación multi-TF: el mismo canal se ve roto en TFs superiores
    # Cuantos más TFs confirman, más fuerte es la señal (no es un pullback)
    _n_confirm_sell = sum([_canal_4h_alcista_roto, _canal_1d_alcista_roto, _canal_1w_alcista_roto])
    _n_confirm_buy  = sum([_canal_4h_bajista_roto, _canal_1d_bajista_roto, _canal_1w_bajista_roto])

    canal_sell_confirmado_4h = canal_alcista_roto and _canal_4h_alcista_roto
    canal_buy_confirmado_4h  = canal_bajista_roto and _canal_4h_bajista_roto
    canal_sell_confirmado_1d = canal_alcista_roto and _canal_1d_alcista_roto
    canal_buy_confirmado_1d  = canal_bajista_roto and _canal_1d_bajista_roto
    canal_sell_confirmado_1w = canal_alcista_roto and _canal_1w_alcista_roto
    canal_buy_confirmado_1w  = canal_bajista_roto and _canal_1w_bajista_roto

    # Si algún TF superior confirma el canal roto → señal real, no pullback
    _sell_confirmado_htf = canal_sell_confirmado_4h or canal_sell_confirmado_1d or canal_sell_confirmado_1w
    _buy_confirmado_htf  = canal_buy_confirmado_4h  or canal_buy_confirmado_1d  or canal_buy_confirmado_1w

    # Bonus de score por cada TF superior que confirma (+1 por cada uno, máx +3)
    score_sell = min(score_sell + _n_confirm_sell, 23)
    score_buy  = min(score_buy  + _n_confirm_buy,  23)

    pullback_alcista = canal_alcista_roto and _htf_alcista and not _sell_confirmado_htf
    pullback_bajista = canal_bajista_roto and _htf_bajista and not _buy_confirmado_htf

    if pullback_alcista:
        score_sell = max(0, score_sell - 4)
        print(f"  ⚠️ PULLBACK alcista — canal roto bajista pero HTF (1D/1W/4H) es BULLISH → penalizar SELL")
    if pullback_bajista:
        score_buy  = max(0, score_buy  - 4)
        print(f"  ⚠️ PULLBACK bajista — canal roto alcista pero HTF (1D/1W/4H) es BEARISH → penalizar BUY")
    if _sell_confirmado_htf:
        _htf_labels = " | ".join(filter(None, [
            f"4H(${_linea_canal_4h_sop:.0f})" if canal_sell_confirmado_4h else "",
            f"1D(${_linea_canal_1d_sop:.0f})" if canal_sell_confirmado_1d else "",
            f"1W(${_linea_canal_1w_sop:.0f})" if canal_sell_confirmado_1w else "",
        ]))
        print(f"  ✅ Canal SELL confirmado en TFs superiores: {_htf_labels}")
    if _buy_confirmado_htf:
        _htf_labels = " | ".join(filter(None, [
            f"4H(${_linea_canal_4h_res:.0f})" if canal_buy_confirmado_4h else "",
            f"1D(${_linea_canal_1d_res:.0f})" if canal_buy_confirmado_1d else "",
            f"1W(${_linea_canal_1w_res:.0f})" if canal_buy_confirmado_1w else "",
        ]))
        print(f"  ✅ Canal BUY confirmado en TFs superiores: {_htf_labels}")

    # Umbrales 1H (estrictos para filtrar ruido intradía)
    senal_sell_maxima = score_sell >= 12
    senal_sell_fuerte = score_sell >= 9
    senal_sell_media  = score_sell >= 6
    senal_sell_alerta = score_sell >= 5
    senal_buy_maxima  = score_buy  >= 12
    senal_buy_fuerte  = score_buy  >= 9
    senal_buy_media   = score_buy  >= 6
    senal_buy_alerta  = score_buy  >= 5

    def rr(limit, sl, tp):
        return round(abs(tp - limit) / abs(sl - limit), 1) if abs(sl - limit) > 0 else 0

    fecha = df.index[-2].strftime('%Y-%m-%d %H:%M')

    clave_simbolo = simbolo
    if clave_simbolo in ultimo_analisis:
        ua = ultimo_analisis[clave_simbolo]
        if (ua['fecha'] == fecha and
                abs(int(ua['score_sell']) - score_sell) <= 1 and
                abs(int(ua['score_buy']) - score_buy) <= 1):
            print(f"  ℹ️  Vela {fecha} ya analizada")
            return

    ultimo_analisis[clave_simbolo] = {'fecha': fecha, 'score_sell': score_sell, 'score_buy': score_buy}
    print(f"  📅 {fecha} | Close: ${close:.2f} | ATR: ${atr:.2f} | SELL: {score_sell}/21 | BUY: {score_buy}/21")

    clave_vela = f"{simbolo}_1H_{fecha}"

    def ya_enviada(tipo): return alertas_enviadas.get(f"{clave_vela}_{tipo}", 0) > time.time() - 172800
    def marcar_enviada(tipo): alertas_enviadas[f"{clave_vela}_{tipo}"] = time.time()

    # ── FILTRO R:R MÍNIMO 1.2 (evaluar ANTES de cualquier señal) ──
    rr_sell_tp1 = rr(sell_limit, sl_venta, tp1_v)
    rr_buy_tp1  = rr(buy_limit,  sl_compra, tp1_c)
    cancelar_sell_rr = rr_sell_tp1 < 1.2
    cancelar_buy_rr  = rr_buy_tp1 < 1.2
    if cancelar_sell_rr:
        print(f"  ⛔ SELL bloqueada: R:R TP1 = {rr_sell_tp1}:1 < 1.2 mínimo")
    if cancelar_buy_rr:
        print(f"  ⛔ BUY bloqueada: R:R TP1 = {rr_buy_tp1}:1 < 1.2 mínimo")

    # ── EXCLUSIÓN MUTUA: una sola dirección por vela ──
    if senal_sell_alerta and senal_buy_alerta:
        if score_sell >= score_buy:
            senal_buy_alerta = False
            print(f"  ⚖️ Exclusión mutua: BUY suprimida (SELL {score_sell} >= BUY {score_buy})")
        else:
            senal_sell_alerta = False
            print(f"  ⚖️ Exclusión mutua: SELL suprimida (BUY {score_buy} > SELL {score_sell})")

    # ── PUBLICAR + FILTRO CONFLUENCIA MULTI-TF (GOLD 1H) ──
    _sesgo_dir = tf_bias.BIAS_BEARISH if score_sell > score_buy else tf_bias.BIAS_BULLISH if score_buy > score_sell else tf_bias.BIAS_NEUTRAL
    tf_bias.publicar_sesgo(simbolo, '1H', _sesgo_dir, max(score_sell, score_buy))
    _conf_sell = ""; _conf_buy = ""
    if senal_sell_fuerte:
        _ok, _desc = tf_bias.verificar_confluencia(simbolo, '1H', tf_bias.BIAS_BEARISH)
        if not _ok:
            print(f"  🚫 SELL bloqueada por TF superior: {_desc[:80]}")
            senal_sell_maxima = senal_sell_fuerte = False
        else:
            _conf_sell = _desc
    if senal_buy_fuerte:
        _ok, _desc = tf_bias.verificar_confluencia(simbolo, '1H', tf_bias.BIAS_BULLISH)
        if not _ok:
            print(f"  🚫 BUY bloqueada por TF superior: {_desc[:80]}")
            senal_buy_maxima = senal_buy_fuerte = False
        else:
            _conf_buy = _desc

    # ── ALERTAS DE APROXIMACIÓN → SEÑAL ACCIONABLE (pon la orden limit ahora) ──
    if aproximando_resist and not en_zona_resist and not cancelar_sell and not cancelar_sell_rr and senal_sell_alerta and not ya_enviada('PREP_SELL'):
        nv = ("🔥 SELL MÁXIMA" if senal_sell_maxima else
              "🔴 SELL FUERTE" if senal_sell_fuerte else
              "⚡ SELL MEDIA"  if senal_sell_media  else
              "👀 SELL ALERTA")
        msg = (f"⏳ <b>SETUP SELL 1H — ORO (XAUUSD)</b> | ESPERANDO CONFIRMACIÓN 15M/5M\n"
               f"━━━━━━━━━━━━━━━━━━━━\n"
               f"📊 <b>Nivel:</b> {nv}\n"
               f"💰 <b>Precio actual:</b> ${close:.2f}\n"
               f"📌 <b>SELL LIMIT previsto:</b> ${sell_limit:.2f}\n"
               f"🛑 <b>Stop Loss:</b> ${sl_venta:.2f}  ← swing high estructural\n"
               f"━━━━━━━━━━━━━━━━━━━━\n"
               f"🎯 <b>TP1:</b> ${tp1_v:.2f}  R:R {rr(sell_limit, sl_venta, tp1_v)}:1  (zona S/R)\n"
               f"🎯 <b>TP2:</b> ${tp2_v:.2f}  R:R {rr(sell_limit, sl_venta, tp2_v)}:1  (zona S/R)\n"
               f"🎯 <b>TP3:</b> ${tp3_v:.2f}  R:R {rr(sell_limit, sl_venta, tp3_v)}:1  (zona S/R)\n"
               f"━━━━━━━━━━━━━━━━━━━━\n"
               + (f"🔻 <b>Canal alcista ROTO</b> — nivel canal ${linea_soporte_canal:.2f}\n" if canal_alcista_roto else "")
               + f"📊 <b>Score:</b> {score_sell}/23  📉 <b>RSI:</b> {round(rsi, 1)}  📐 <b>ATR:</b> ${atr:.2f}\n"
               f"📰 <b>Noticias:</b> {_sesgo_etiq} ({_sesgo_score:+.1f})  ➜  {_sesgo_news.replace('_', ' ')}\n"
               f"⏱️ <b>TF:</b> 1H  📅 {fecha}  🔒 Aguardando alineación 15M/5M...")
        if db and not db.existe_senal_reciente(simbolo_db, "VENTA", horas=1):
            try:
                db.guardar_senal({
                    'timestamp': datetime.now(timezone.utc), 'simbolo': simbolo_db,
                    'direccion': 'VENTA', 'precio_entrada': sell_limit,
                    'tp1': tp1_v, 'tp2': tp2_v, 'tp3': tp3_v, 'sl': sl_venta,
                    'score': score_sell,
                    'indicadores': json.dumps({'rsi': round(rsi, 1), 'atr': round(atr, 2), 'adx': round(adx, 2)}),
                    'patron_velas': f"Evening Star:{evening_star}, Shooting Star:{shooting_star}",
                    'version_detector': 'GOLD 1H-v2.0',
                    'estado': 'PENDIENTE_CONFIRM'
                })
            except Exception as e:
                print(f"  ⚠️ Error BD: {e}")
        enviar_telegram(msg); marcar_enviada('PREP_SELL')

    if aproximando_soporte and not en_zona_soporte and not cancelar_buy and not cancelar_buy_rr and senal_buy_alerta and not ya_enviada('PREP_BUY'):
        nv = ("🔥 BUY MÁXIMA" if senal_buy_maxima else
              "🟢 BUY FUERTE" if senal_buy_fuerte else
              "⚡ BUY MEDIA"  if senal_buy_media  else
              "👀 BUY ALERTA")
        msg = (f"⏳ <b>SETUP BUY 1H — ORO (XAUUSD)</b> | ESPERANDO CONFIRMACIÓN 15M/5M\n"
               f"━━━━━━━━━━━━━━━━━━━━\n"
               f"📊 <b>Nivel:</b> {nv}\n"
               f"💰 <b>Precio actual:</b> ${close:.2f}\n"
               f"📌 <b>BUY LIMIT previsto:</b> ${buy_limit:.2f}\n"
               f"🛑 <b>Stop Loss:</b> ${sl_compra:.2f}  ← swing low estructural\n"
               f"━━━━━━━━━━━━━━━━━━━━\n"
               f"🎯 <b>TP1:</b> ${tp1_c:.2f}  R:R {rr(buy_limit, sl_compra, tp1_c)}:1  (zona S/R)\n"
               f"🎯 <b>TP2:</b> ${tp2_c:.2f}  R:R {rr(buy_limit, sl_compra, tp2_c)}:1  (zona S/R)\n"
               f"🎯 <b>TP3:</b> ${tp3_c:.2f}  R:R {rr(buy_limit, sl_compra, tp3_c)}:1  (zona S/R)\n"
               f"━━━━━━━━━━━━━━━━━━━━\n"
               + (f"🔺 <b>Canal bajista ROTO</b> — nivel canal ${linea_resist_canal:.2f}\n" if canal_bajista_roto else "")
               + f"📊 <b>Score:</b> {score_buy}/23  📉 <b>RSI:</b> {round(rsi, 1)}  📐 <b>ATR:</b> ${atr:.2f}\n"
               f"📰 <b>Noticias:</b> {_sesgo_etiq} ({_sesgo_score:+.1f})  ➜  {_sesgo_news.replace('_', ' ')}\n"
               f"⏱️ <b>TF:</b> 1H  📅 {fecha}  🔒 Aguardando alineación 15M/5M...")
        if db and not db.existe_senal_reciente(simbolo_db, "COMPRA", horas=1):
            try:
                db.guardar_senal({
                    'timestamp': datetime.now(timezone.utc), 'simbolo': simbolo_db,
                    'direccion': 'COMPRA', 'precio_entrada': buy_limit,
                    'tp1': tp1_c, 'tp2': tp2_c, 'tp3': tp3_c, 'sl': sl_compra,
                    'score': score_buy,
                    'indicadores': json.dumps({'rsi': round(rsi, 1), 'atr': round(atr, 2), 'adx': round(adx, 2)}),
                    'patron_velas': f"Morning Star:{morning_star}, Hammer:{hammer}",
                    'version_detector': 'GOLD 1H-v2.0',
                    'estado': 'PENDIENTE_CONFIRM'
                })
            except Exception as e:
                print(f"  ⚠️ Error BD: {e}")
        enviar_telegram(msg); marcar_enviada('PREP_BUY')

    # ── SEÑALES SELL (en zona) — confirmación si ya hubo señal accionable ──
    if senal_sell_alerta and not cancelar_sell and rr_sell_tp1 >= 1.2:
        if ya_enviada('PREP_SELL') and not (senal_sell_fuerte or senal_sell_maxima):
            print(f"  ℹ️  SELL ALERTA/MEDIA ignorada: señal accionable ya enviada")
        else:
            if senal_sell_maxima:  nivel = "🔥 SELL MÁXIMA (1H)"
            elif senal_sell_fuerte: nivel = "🔴 SELL FUERTE (1H)"
            elif senal_sell_media:  nivel = "⚠️ SELL MEDIA (1H)"
            else:                   nivel = "👀 SELL ALERTA (1H)"
            tipo_clave = ("SELL_MAX" if senal_sell_maxima else
                          "SELL_FUE" if senal_sell_fuerte else
                          "SELL_MED" if senal_sell_media  else "SELL_ALE")
            if not ya_enviada(tipo_clave):
                if ya_enviada('PREP_SELL'):
                    # Breve confirmación — señal accionable ya fue enviada antes
                    msg = (f"✅ <b>CONFIRMACIÓN SELL — ORO 1H</b>\n"
                           f"━━━━━━━━━━━━━━━━━━━━\n"
                           f"{nivel} — precio ahora en zona\n"
                           f"💰 <b>Precio:</b> ${close:.2f}\n"
                           f"📊 <b>Score:</b> {score_sell}/21  📉 <b>RSI:</b> {round(rsi, 1)}\n"
                           f"⏱️ <b>TF:</b> 1H  📅 {fecha}")
                    enviar_telegram(msg)
                    marcar_enviada(tipo_clave)
                else:
                    # Precio saltó directo a la zona sin pre-alerta → señal completa con DB
                    if db and db.existe_senal_reciente(simbolo_db, "VENTA", horas=1):
                        print(f"  ℹ️  Señal VENTA 1H duplicada"); return
                    msg = (f"{nivel} — <b>ORO (XAUUSD) ⏰ INTRADÍA</b>\n"
                           f"━━━━━━━━━━━━━━━━━━━━\n"
                           f"💰 <b>Precio:</b>     ${close:.2f}\n"
                           f"📌 <b>SELL LIMIT:</b> ${sell_limit:.2f}\n"
                           f"🛑 <b>Stop Loss:</b>  ${sl_venta:.2f}  (-${round(sl_venta - sell_limit, 2)})\n"
                           f"━━━━━━━━━━━━━━━━━━━━\n"
                           f"🎯 <b>TP1:</b> ${tp1_v:.2f}  R:R {rr(sell_limit, sl_venta, tp1_v)}:1\n"
                           f"🎯 <b>TP2:</b> ${tp2_v:.2f}  R:R {rr(sell_limit, sl_venta, tp2_v)}:1\n"
                           f"🎯 <b>TP3:</b> ${tp3_v:.2f}  R:R {rr(sell_limit, sl_venta, tp3_v)}:1\n"
                           f"━━━━━━━━━━━━━━━━━━━━\n"
                           f"📊 <b>Score:</b> {score_sell}/21  📉 <b>RSI:</b> {round(rsi, 1)}  📐 <b>ADX:</b> {round(adx, 1)}\n"
                           f"📐 <b>ATR:</b> ${atr:.2f}\n"
                           f"⏱️ <b>TF:</b> 1H  📅 {fecha}\n"
                           f"🔒 <b>INTRADÍA — Cerrar antes del cierre de sesión</b>")
                    if _conf_sell:
                        msg += f"\n━━━━━━━━━━━━━━━━━━━━\n{_conf_sell}"
                    if db:
                        try:
                            db.guardar_senal({
                                'timestamp': datetime.now(timezone.utc), 'simbolo': simbolo_db,
                                'direccion': 'VENTA', 'precio_entrada': sell_limit,
                                'tp1': tp1_v, 'tp2': tp2_v, 'tp3': tp3_v, 'sl': sl_venta,
                                'score': score_sell,
                                'indicadores': json.dumps({'rsi': round(rsi, 1), 'atr': round(atr, 2), 'adx': round(adx, 2)}),
                                'patron_velas': f"Evening Star:{evening_star}, Shooting Star:{shooting_star}",
                                'version_detector': 'GOLD 1H-v2.0'
                            })
                        except Exception as e:
                            print(f"  ⚠️ Error BD: {e}")
                    enviar_telegram(msg)
                    marcar_enviada(tipo_clave)

    # ── SEÑALES BUY (en zona) — confirmación si ya hubo señal accionable ──
    if senal_buy_alerta and not cancelar_buy and rr_buy_tp1 >= 1.2:
        if ya_enviada('PREP_BUY') and not (senal_buy_fuerte or senal_buy_maxima):
            print(f"  ℹ️  BUY ALERTA/MEDIA ignorada: señal accionable ya enviada")
        else:
            if senal_buy_maxima:   nivel = "🔥 BUY MÁXIMA (1H)"
            elif senal_buy_fuerte:  nivel = "🟢 BUY FUERTE (1H)"
            elif senal_buy_media:   nivel = "⚠️ BUY MEDIA (1H)"
            else:                   nivel = "👀 BUY ALERTA (1H)"
            tipo_clave = ("BUY_MAX" if senal_buy_maxima else
                          "BUY_FUE" if senal_buy_fuerte else
                          "BUY_MED" if senal_buy_media  else "BUY_ALE")
            if not ya_enviada(tipo_clave):
                if ya_enviada('PREP_BUY'):
                    # Breve confirmación — señal accionable ya fue enviada antes
                    msg = (f"✅ <b>CONFIRMACIÓN BUY — ORO 1H</b>\n"
                           f"━━━━━━━━━━━━━━━━━━━━\n"
                           f"{nivel} — precio ahora en zona\n"
                           f"💰 <b>Precio:</b> ${close:.2f}\n"
                           f"📊 <b>Score:</b> {score_buy}/21  📉 <b>RSI:</b> {round(rsi, 1)}\n"
                           f"⏱️ <b>TF:</b> 1H  📅 {fecha}")
                    enviar_telegram(msg)
                    marcar_enviada(tipo_clave)
                else:
                    # Precio saltó directo a la zona sin pre-alerta → señal completa con DB
                    if db and db.existe_senal_reciente(simbolo_db, "COMPRA", horas=1):
                        print(f"  ℹ️  Señal COMPRA 1H duplicada"); return
                    msg = (f"{nivel} — <b>ORO (XAUUSD) ⏰ INTRADÍA</b>\n"
                           f"━━━━━━━━━━━━━━━━━━━━\n"
                           f"💰 <b>Precio:</b>    ${close:.2f}\n"
                           f"📌 <b>BUY LIMIT:</b> ${buy_limit:.2f}\n"
                           f"🛑 <b>Stop Loss:</b> ${sl_compra:.2f}  (-${round(buy_limit - sl_compra, 2)})\n"
                           f"━━━━━━━━━━━━━━━━━━━━\n"
                           f"🎯 <b>TP1:</b> ${tp1_c:.2f}  R:R {rr(buy_limit, sl_compra, tp1_c)}:1\n"
                           f"🎯 <b>TP2:</b> ${tp2_c:.2f}  R:R {rr(buy_limit, sl_compra, tp2_c)}:1\n"
                           f"🎯 <b>TP3:</b> ${tp3_c:.2f}  R:R {rr(buy_limit, sl_compra, tp3_c)}:1\n"
                           f"━━━━━━━━━━━━━━━━━━━━\n"
                           f"📊 <b>Score:</b> {score_buy}/21  📉 <b>RSI:</b> {round(rsi, 1)}  📐 <b>ADX:</b> {round(adx, 1)}\n"
                           f"📐 <b>ATR:</b> ${atr:.2f}\n"
                           f"⏱️ <b>TF:</b> 1H  📅 {fecha}\n"
                           f"🔒 <b>INTRADÍA — Cerrar antes del cierre de sesión</b>")
                    if _conf_buy:
                        msg += f"\n━━━━━━━━━━━━━━━━━━━━\n{_conf_buy}"
                    if db:
                        try:
                            db.guardar_senal({
                                'timestamp': datetime.now(timezone.utc), 'simbolo': simbolo_db,
                                'direccion': 'COMPRA', 'precio_entrada': buy_limit,
                                'tp1': tp1_c, 'tp2': tp2_c, 'tp3': tp3_c, 'sl': sl_compra,
                                'score': score_buy,
                                'indicadores': json.dumps({'rsi': round(rsi, 1), 'atr': round(atr, 2), 'adx': round(adx, 2)}),
                                'patron_velas': f"Morning Star:{morning_star}, Hammer:{hammer}",
                                'version_detector': 'GOLD 1H-v2.0'
                            })
                        except Exception as e:
                            print(f"  ⚠️ Error BD: {e}")
                    enviar_telegram(msg)
                    marcar_enviada(tipo_clave)

    # ── SEÑALES RETEST DE CANAL ROTO (alta probabilidad) ─────────────────────
    # Setup: precio rompe canal → retrocede al nivel roto → rechaza → ENTRADA
    # CASO ESPECIAL: si el canal roto va CONTRA la tendencia superior (1D/1W),
    # el retest NO es una señal en esa dirección, sino un PULLBACK de la tendencia
    # mayor → aviso de zona de compra/venta en dirección de la tendencia principal.

    if retest_canal_sell and not ya_enviada('RETEST_SELL'):
        if pullback_alcista:
            # Canal alcista roto en 1H PERO 1D/1W es BULLISH → pullback, no reversión
            # El retest de la línea rota es una ZONA DE COMPRA (rebote esperado)
            if not ya_enviada('PULLBACK_BUY'):
                _dir_str = f"1D: {_dir_1d}" + (f" | 1W: {_dir_1w}" if _dir_1w != tf_bias.BIAS_NEUTRAL else "")
                msg = (
                    f"⚠️ <b>PULLBACK EN TENDENCIA ALCISTA — ORO (XAUUSD) 1H</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"🔻 Canal 1H roto a la baja — PERO tendencia superior es ALCISTA\n"
                    f"🔺 Contexto: <b>{_dir_str}</b> → corrección dentro de uptrend\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"📌 <b>ZONA DE COMPRA (pullback):</b> ~${linea_soporte_canal:.2f}\n"
                    f"💡 Esperar confirmación alcista en 1H en esa zona\n"
                    f"💡 No operar SELL — va contra la tendencia principal\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"📊 RSI: {round(rsi, 1)}  ATR: ${atr:.2f}  💰 Precio: ${close:.2f}\n"
                    f"⏱️ 1H  📅 {fecha}"
                )
                enviar_telegram(msg)
                marcar_enviada('PULLBACK_BUY')
            print(f"  ⛔ Retest SELL suprimido — pullback alcista (1D/1W bullish)")
        elif db and db.existe_senal_reciente(simbolo_db, 'VENTA', horas=1):
            print(f"  ℹ️  Retest SELL duplicado")
        else:
            # SL: por encima de la línea del canal + buffer
            sl_rt_sell  = round(linea_soporte_canal + atr * 0.4, 2)
            # TPs: primeros niveles de soporte reales
            tp1_rt_sell = _tp_desde_sr(soportes_sr, 1, close - atr * params['atr_tp1_mult'])
            tp2_rt_sell = _tp_desde_sr(soportes_sr, 2, close - atr * params['atr_tp2_mult'])
            tp3_rt_sell = _tp_desde_sr(soportes_sr, 3, close - atr * params['atr_tp3_mult'])
            entry_rt    = round(close, 2)
            rr1 = rr(entry_rt, sl_rt_sell, tp1_rt_sell)
            rr2 = rr(entry_rt, sl_rt_sell, tp2_rt_sell)
            rr3 = rr(entry_rt, sl_rt_sell, tp3_rt_sell)
            if rr1 >= 1.5:   # R:R mínimo más estricto para este setup
                _htf_conf_sell_lines = "\n".join(filter(None, [
                    f"📈 <b>4H también roto</b> — soporte ${_linea_canal_4h_sop:.0f}" if canal_sell_confirmado_4h else "",
                    f"📈 <b>1D también roto</b> — soporte ${_linea_canal_1d_sop:.0f}" if canal_sell_confirmado_1d else "",
                    f"📈 <b>1W también roto</b> — soporte ${_linea_canal_1w_sop:.0f}" if canal_sell_confirmado_1w else "",
                ]))
                _conf_htf_sell = (f"\n{_htf_conf_sell_lines}  ← multi-TF confirmado"
                                  if _htf_conf_sell_lines else "")
                msg = (
                    f"🎯 <b>RETEST CANAL SELL — ORO (XAUUSD) 1H</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"🔻 Canal alcista ROTO — precio retestea línea{_conf_htf_sell}\n"
                    f"📌 <b>SELL MARKET:</b> ${entry_rt:.2f}  ← ENTRA AHORA\n"
                    f"🛑 <b>Stop Loss:</b> ${sl_rt_sell:.2f}  (sobre línea canal)\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"🎯 <b>TP1:</b> ${tp1_rt_sell:.2f}  R:R {rr1}:1  (zona S/R)\n"
                    f"🎯 <b>TP2:</b> ${tp2_rt_sell:.2f}  R:R {rr2}:1  (zona S/R)\n"
                    f"🎯 <b>TP3:</b> ${tp3_rt_sell:.2f}  R:R {rr3}:1  (zona S/R)\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"📐 <b>Línea canal:</b> ${linea_soporte_canal:.2f}  📉 <b>RSI:</b> {round(rsi, 1)}\n"
                    f"📐 <b>ATR:</b> ${atr:.2f}  📊 <b>Score:</b> {score_sell}/23\n"
                    f"⏱️ 1H  📅 {fecha}  🏆 Setup retest canal"
                )
                if db:
                    try:
                        db.guardar_senal({
                            'timestamp': datetime.now(timezone.utc), 'simbolo': simbolo_db,
                            'direccion': 'VENTA', 'precio_entrada': entry_rt,
                            'tp1': tp1_rt_sell, 'tp2': tp2_rt_sell, 'tp3': tp3_rt_sell,
                            'sl': sl_rt_sell, 'score': score_sell,
                            'indicadores': json.dumps({'rsi': round(rsi, 1), 'atr': round(atr, 2), 'adx': round(adx, 2)}),
                            'patron_velas': f"Retest canal alcista roto, línea ${linea_soporte_canal:.2f}",
                            'version_detector': 'GOLD 1H-v2.0-RETEST'
                        })
                    except Exception as e:
                        print(f"  ⚠️ Error BD: {e}")
                enviar_telegram(msg)
                marcar_enviada('RETEST_SELL')
            else:
                print(f"  ⛔ Retest SELL bloqueado: R:R {rr1}:1 < 1.5 mínimo")

    if retest_canal_buy and not ya_enviada('RETEST_BUY'):
        if pullback_bajista:
            # Canal bajista roto en 1H PERO 1D/1W es BEARISH → pullback, no reversión
            if not ya_enviada('PULLBACK_SELL'):
                _dir_str = f"1D: {_dir_1d}" + (f" | 1W: {_dir_1w}" if _dir_1w != tf_bias.BIAS_NEUTRAL else "")
                msg = (
                    f"⚠️ <b>PULLBACK EN TENDENCIA BAJISTA — ORO (XAUUSD) 1H</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"🔺 Canal 1H roto al alza — PERO tendencia superior es BAJISTA\n"
                    f"🔻 Contexto: <b>{_dir_str}</b> → rebote dentro de downtrend\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"📌 <b>ZONA DE VENTA (pullback):</b> ~${linea_resist_canal:.2f}\n"
                    f"💡 Esperar confirmación bajista en 1H en esa zona\n"
                    f"💡 No operar BUY — va contra la tendencia principal\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"📊 RSI: {round(rsi, 1)}  ATR: ${atr:.2f}  💰 Precio: ${close:.2f}\n"
                    f"⏱️ 1H  📅 {fecha}"
                )
                enviar_telegram(msg)
                marcar_enviada('PULLBACK_SELL')
            print(f"  ⛔ Retest BUY suprimido — pullback bajista (1D/1W bearish)")
        elif db and db.existe_senal_reciente(simbolo_db, 'COMPRA', horas=1):
            print(f"  ℹ️  Retest BUY duplicado")
        else:
            sl_rt_buy   = round(linea_resist_canal - atr * 0.4, 2)
            tp1_rt_buy  = _tp_desde_sr(sorted([v for v in resistencias_sr if v > close]), 1,
                                       close + atr * params['atr_tp1_mult'])
            tp2_rt_buy  = _tp_desde_sr(sorted([v for v in resistencias_sr if v > close]), 2,
                                       close + atr * params['atr_tp2_mult'])
            tp3_rt_buy  = _tp_desde_sr(sorted([v for v in resistencias_sr if v > close]), 3,
                                       close + atr * params['atr_tp3_mult'])
            entry_rt    = round(close, 2)
            rr1 = rr(entry_rt, sl_rt_buy, tp1_rt_buy)
            rr2 = rr(entry_rt, sl_rt_buy, tp2_rt_buy)
            rr3 = rr(entry_rt, sl_rt_buy, tp3_rt_buy)
            if rr1 >= 1.5:
                _htf_conf_buy_lines = "\n".join(filter(None, [
                    f"📈 <b>4H también roto</b> — resist ${_linea_canal_4h_res:.0f}" if canal_buy_confirmado_4h else "",
                    f"📈 <b>1D también roto</b> — resist ${_linea_canal_1d_res:.0f}" if canal_buy_confirmado_1d else "",
                    f"📈 <b>1W también roto</b> — resist ${_linea_canal_1w_res:.0f}" if canal_buy_confirmado_1w else "",
                ]))
                _conf_htf_buy = (f"\n{_htf_conf_buy_lines}  ← multi-TF confirmado"
                                 if _htf_conf_buy_lines else "")
                msg = (
                    f"🎯 <b>RETEST CANAL BUY — ORO (XAUUSD) 1H</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"🔺 Canal bajista ROTO — precio retestea línea{_conf_htf_buy}\n"
                    f"📌 <b>BUY MARKET:</b> ${entry_rt:.2f}  ← ENTRA AHORA\n"
                    f"🛑 <b>Stop Loss:</b> ${sl_rt_buy:.2f}  (bajo línea canal)\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"🎯 <b>TP1:</b> ${tp1_rt_buy:.2f}  R:R {rr1}:1  (zona S/R)\n"
                    f"🎯 <b>TP2:</b> ${tp2_rt_buy:.2f}  R:R {rr2}:1  (zona S/R)\n"
                    f"🎯 <b>TP3:</b> ${tp3_rt_buy:.2f}  R:R {rr3}:1  (zona S/R)\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"📐 <b>Línea canal:</b> ${linea_resist_canal:.2f}  📉 <b>RSI:</b> {round(rsi, 1)}\n"
                    f"📐 <b>ATR:</b> ${atr:.2f}  📊 <b>Score:</b> {score_buy}/23\n"
                    f"⏱️ 1H  📅 {fecha}  🏆 Setup retest canal"
                )
                if db:
                    try:
                        db.guardar_senal({
                            'timestamp': datetime.now(timezone.utc), 'simbolo': simbolo_db,
                            'direccion': 'COMPRA', 'precio_entrada': entry_rt,
                            'tp1': tp1_rt_buy, 'tp2': tp2_rt_buy, 'tp3': tp3_rt_buy,
                            'sl': sl_rt_buy, 'score': score_buy,
                            'indicadores': json.dumps({'rsi': round(rsi, 1), 'atr': round(atr, 2), 'adx': round(adx, 2)}),
                            'patron_velas': f"Retest canal bajista roto, línea ${linea_resist_canal:.2f}",
                            'version_detector': 'GOLD 1H-v2.0-RETEST'
                        })
                    except Exception as e:
                        print(f"  ⚠️ Error BD: {e}")
                enviar_telegram(msg)
                marcar_enviada('RETEST_BUY')
            else:
                print(f"  ⛔ Retest BUY bloqueado: R:R {rr1}:1 < 1.5 mínimo")

    # ── CANCELACIONES ───────────────────────────────────────────
    if cancelar_sell and not ya_enviada('CANCEL_SELL'):
        enviar_telegram(f"❌ <b>CANCELAR SELL — ORO (1H) INTRADÍA</b> ❌\n"
                        f"━━━━━━━━━━━━━━━━━━━━\n"
                        f"💰 Precio: ${close:.2f} | Rompió resistencia (${zrh:.2f})\n"
                        f"⏱️ 1H  📅 {fecha}")
        marcar_enviada('CANCEL_SELL')

    if cancelar_buy and not ya_enviada('CANCEL_BUY'):
        enviar_telegram(f"❌ <b>CANCELAR BUY — ORO (1H) INTRADÍA</b> ❌\n"
                        f"━━━━━━━━━━━━━━━━━━━━\n"
                        f"💰 Precio: ${close:.2f} | Perforó soporte (${zsl:.2f})\n"
                        f"⏱️ 1H  📅 {fecha}")
        marcar_enviada('CANCEL_BUY')


def main():
    print("🚀 Detector ORO 1H intradía iniciado")
    enviar_telegram("🚀 <b>Detector ORO (XAUUSD) 1H — INTRADÍA iniciado</b>\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    "📊 Monitorizando: GC=F (Gold Futures)\n"
                    "⏱️ Timeframe: 1H  |  Modo: ⏰ INTRADÍA\n"
                    "🔄 Revisión cada 60 segundos\n"
                    "📐 TPs dinámicos basados en ATR (×1.5 / ×2.5 / ×4.0)\n"
                    "🔒 Señales para abrir y cerrar en el mismo día\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    "🔴 Resistencia: $4,750 - $4,900\n"
                    "🟢 Soporte:     $4,200 - $4,400")
    ciclo = 0
    while True:
        ciclo += 1
        ahora_utc = datetime.now(timezone.utc)

        # ── Sábado: futuros Gold cerrados. Domingo 18:00 UTC abre Globex ──
        if ahora_utc.weekday() == 5:  # 5=Sábado únicamente
            from datetime import timedelta
            proximo_domingo_18 = (ahora_utc + timedelta(days=1)).replace(hour=18, minute=0, second=0, microsecond=0)
            segundos_espera = min((proximo_domingo_18 - ahora_utc).total_seconds(), 3600)
            print(f"[{ahora_utc.strftime('%Y-%m-%d %H:%M')} UTC] 💤 Sábado — mercado cerrado. Próxima apertura Domingo 18:00 UTC. Revisando en {int(segundos_espera//60)} min...")
            time.sleep(segundos_espera)
            continue

        print(f"\n[{ahora_utc.strftime('%Y-%m-%d %H:%M:%S')}] 🔄 CICLO #{ciclo} - ORO 1H")
        for simbolo, params in SIMBOLOS.items():
            analizar(simbolo, params)
        print(f"⏳ Esperando {CHECK_INTERVAL}s...")
        time.sleep(CHECK_INTERVAL)


if __name__ == '__main__':
    main()
