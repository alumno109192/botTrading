import os
from services import tf_bias
from services.dxy_bias import get_dxy_bias, ajustar_score_por_dxy
from services.economic_calendar import hay_evento_impacto
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

def enviar_telegram(mensaje):
    return _enviar_telegram_base(mensaje, TELEGRAM_THREAD_ID)

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
)

def en_sesion_activa():
    """Solo operar en sesión London/NY: 07:00-17:00 UTC."""
    hora_utc = datetime.now(timezone.utc).hour
    return 7 <= hora_utc < 17


def calcular_zonas_sr(df, atr, lookback, zone_mult):
    """
    Detecta automáticamente zonas S/R desde swing highs/lows históricos.
    Returns: (zrl, zrh, zsl, zsh)
    """
    highs = df['High'].iloc[-lookback-1:-1]
    lows  = df['Low'].iloc[-lookback-1:-1]
    
    resist_pivot  = float(highs.max())
    support_pivot = float(lows.min())
    zone_width = atr * zone_mult
    
    zrh = round(resist_pivot + zone_width * 0.25, 2)
    zrl = round(resist_pivot - zone_width * 0.75, 2)
    zsh = round(support_pivot + zone_width * 0.75, 2)
    zsl = round(support_pivot - zone_width * 0.25, 2)
    return zrl, zrh, zsl, zsh


def analizar(simbolo, params):
    simbolo_db = f"{simbolo}_1H"

    if not en_sesion_activa():
        print(f"  ⏸️  [1H] Fuera de sesión (07-17 UTC) — análisis saltado")
        return

    # ── Filtro calendario económico ──
    bloqueado, descripcion = hay_evento_impacto(ventana_minutos=60)
    if bloqueado:
        print(f"  🚫 [1H] Señal bloqueada por evento macro: {descripcion}")
        return

    print(f"\n🔍 Analizando {simbolo} [1H intradía]...")

    try:
        df, is_delayed = get_ohlcv(params['ticker_yf'], period='7d', interval='1h')
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

    zrl, zrh, zsl, zsh = calcular_zonas_sr(df, atr, params['sr_lookback'], params['sr_zone_mult'])
    tol  = round(atr * 0.4, 2)   # tolerancia dinámica: 40% del ATR
    lop = params['limit_offset_pct']; cd = params['cancelar_dist']
    av   = params['anticipar_velas']; vm = params['vol_mult']
    rsms = params['rsi_min_sell']; rsmb = params['rsi_max_buy']; asm = params['atr_sl_mult']
    print(f"  📍 Zonas auto — Resist: ${zrl:.1f}-${zrh:.1f} | Soporte: ${zsl:.1f}-${zsh:.1f}")

    sell_limit = zrl + (zrh - zrl) * (lop / 100 * 10)
    buy_limit  = zsh - (zsh - zsl) * (lop / 100 * 10)

    # TPs dinámicos basados en ATR (intradía alcanzables en el mismo día)
    tp1_v = round(sell_limit - atr * params['atr_tp1_mult'], 2)
    tp2_v = round(sell_limit - atr * params['atr_tp2_mult'], 2)
    tp3_v = round(sell_limit - atr * params['atr_tp3_mult'], 2)
    tp1_c = round(buy_limit  + atr * params['atr_tp1_mult'], 2)
    tp2_c = round(buy_limit  + atr * params['atr_tp2_mult'], 2)
    tp3_c = round(buy_limit  + atr * params['atr_tp3_mult'], 2)

    # SL estrictamente ATR-based desde precio de entrada (intradía, no zona)
    sl_venta  = round(sell_limit + atr * asm, 2)
    sl_compra = round(buy_limit  - atr * asm, 2)

    avg_candle_range    = df['total_range'].iloc[-6:-1].mean()
    aproximando_resist  = (zrl - close > 0 and zrl - close < avg_candle_range * av and close > float(df['Close'].iloc[-5]))
    aproximando_soporte = (close - zsh > 0 and close - zsh < avg_candle_range * av and close < float(df['Close'].iloc[-5]))
    en_zona_resist      = (high >= zrl - tol) and (high <= zrh + tol)
    en_zona_soporte     = (low  >= zsl - tol) and (low  <= zsh + tol)
    cancelar_sell       = close > zrh * (1 + cd / 100)
    cancelar_buy        = close < zsl * (1 - cd / 100)

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
        (1 if macd_negativo            else 0)
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
        (1 if macd_positivo              else 0)
    )
    if adx_lateral: score_buy = max(0, score_buy - 3)

    # ── Ajuste por sesgo DXY (correlación inversa Gold/USD) ──
    dxy_bias = get_dxy_bias()
    score_buy, score_sell = ajustar_score_por_dxy(score_buy, score_sell, dxy_bias)

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
        msg = (f"⚡ <b>SEÑAL SELL — ORO (XAUUSD) 1H</b> | PON ORDEN LIMIT AHORA\n"
               f"━━━━━━━━━━━━━━━━━━━━\n"
               f"📊 <b>Nivel:</b> {nv}\n"
               f"💰 <b>Precio actual:</b> ${close:.2f}\n"
               f"📌 <b>SELL LIMIT:</b>   ${sell_limit:.2f}  ← pon la orden aquí\n"
               f"🛑 <b>Stop Loss:</b>   ${sl_venta:.2f}  (-${round(sl_venta - sell_limit, 2)})\n"
               f"━━━━━━━━━━━━━━━━━━━━\n"
               f"🎯 <b>TP1:</b> ${tp1_v:.2f}  R:R {rr(sell_limit, sl_venta, tp1_v)}:1  (+${round(sell_limit - tp1_v, 2)})\n"
               f"🎯 <b>TP2:</b> ${tp2_v:.2f}  R:R {rr(sell_limit, sl_venta, tp2_v)}:1  (+${round(sell_limit - tp2_v, 2)})\n"
               f"🎯 <b>TP3:</b> ${tp3_v:.2f}  R:R {rr(sell_limit, sl_venta, tp3_v)}:1  (+${round(sell_limit - tp3_v, 2)})\n"
               f"━━━━━━━━━━━━━━━━━━━━\n"
               f"📊 <b>Score:</b> {score_sell}/21  📉 <b>RSI:</b> {round(rsi, 1)}  📐 <b>ATR:</b> ${atr:.2f}\n"
               f"⏱️ <b>TF:</b> 1H  📅 {fecha}  🔒 INTRADÍA")
        if db and not db.existe_senal_reciente(simbolo_db, "VENTA", horas=1):
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
        enviar_telegram(msg); marcar_enviada('PREP_SELL')

    if aproximando_soporte and not en_zona_soporte and not cancelar_buy and not cancelar_buy_rr and senal_buy_alerta and not ya_enviada('PREP_BUY'):
        nv = ("🔥 BUY MÁXIMA" if senal_buy_maxima else
              "🟢 BUY FUERTE" if senal_buy_fuerte else
              "⚡ BUY MEDIA"  if senal_buy_media  else
              "👀 BUY ALERTA")
        msg = (f"⚡ <b>SEÑAL BUY — ORO (XAUUSD) 1H</b> | PON ORDEN LIMIT AHORA\n"
               f"━━━━━━━━━━━━━━━━━━━━\n"
               f"📊 <b>Nivel:</b> {nv}\n"
               f"💰 <b>Precio actual:</b> ${close:.2f}\n"
               f"📌 <b>BUY LIMIT:</b>    ${buy_limit:.2f}  ← pon la orden aquí\n"
               f"🛑 <b>Stop Loss:</b>   ${sl_compra:.2f}  (-${round(buy_limit - sl_compra, 2)})\n"
               f"━━━━━━━━━━━━━━━━━━━━\n"
               f"🎯 <b>TP1:</b> ${tp1_c:.2f}  R:R {rr(buy_limit, sl_compra, tp1_c)}:1  (+${round(tp1_c - buy_limit, 2)})\n"
               f"🎯 <b>TP2:</b> ${tp2_c:.2f}  R:R {rr(buy_limit, sl_compra, tp2_c)}:1  (+${round(tp2_c - buy_limit, 2)})\n"
               f"🎯 <b>TP3:</b> ${tp3_c:.2f}  R:R {rr(buy_limit, sl_compra, tp3_c)}:1  (+${round(tp3_c - buy_limit, 2)})\n"
               f"━━━━━━━━━━━━━━━━━━━━\n"
               f"📊 <b>Score:</b> {score_buy}/21  📉 <b>RSI:</b> {round(rsi, 1)}  📐 <b>ATR:</b> ${atr:.2f}\n"
               f"⏱️ <b>TF:</b> 1H  📅 {fecha}  🔒 INTRADÍA")
        if db and not db.existe_senal_reciente(simbolo_db, "COMPRA", horas=1):
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
        print(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 🔄 CICLO #{ciclo} - ORO 1H")
        for simbolo, params in SIMBOLOS.items():
            analizar(simbolo, params)
        print(f"⏳ Esperando {CHECK_INTERVAL}s...")
        time.sleep(CHECK_INTERVAL)


if __name__ == '__main__':
    main()
