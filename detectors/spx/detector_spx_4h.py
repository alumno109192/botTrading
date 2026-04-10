import yfinance as yf
import pandas as pd
import numpy as np
import requests
import os
import time
import json
from datetime import datetime, timezone
from dotenv import load_dotenv

# Cargar variables de entorno
load_dotenv()

# Inicializar base de datos solo si las variables están configuradas
db = None
try:
    turso_url = os.environ.get('TURSO_DATABASE_URL')
    turso_token = os.environ.get('TURSO_AUTH_TOKEN')
    if turso_url and turso_token:
        import sys
        sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
        from db_manager import DatabaseManager
        db = DatabaseManager()
        print("✅ Sistema de tracking de BD activado")
    else:
        print("⚠️  Variables Turso no configuradas - Sistema funcionará sin tracking de BD")
except Exception as e:
    print(f"⚠️  No se pudo inicializar BD: {e}")
    print("⚠️  Sistema funcionará sin tracking de BD")
    db = None

# ══════════════════════════════════════
# CONFIGURACIÓN 4H
# ══════════════════════════════════════
TELEGRAM_TOKEN   = os.environ.get('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')

CHECK_INTERVAL = 4 * 60  # cada 4 minutos (balance óptimo para timeframe 4H)

# ══════════════════════════════════════
# PARÁMETROS — ESPECÍFICOS SPX500 4H
# ══════════════════════════════════════
SIMBOLOS = {
    'SPX500': {
        'ticker_yf':          '^GSPC',      # SP500 en Yahoo Finance
        'zona_resist_high':   7000.0,
        'zona_resist_low':    6900.0,
        'zona_soporte_high':  6600.0,
        'zona_soporte_low':   6400.0,
        'tp1_venta':          6650.0,
        'tp2_venta':          6500.0,
        'tp3_venta':          6200.0,
        'tp1_compra':         6900.0,
        'tp2_compra':         7000.0,
        'tp3_compra':         7200.0,
        'tolerancia':         75.0,
        'limit_offset_pct':   0.3,
        'anticipar_velas':    3,
        'cancelar_dist':      1.5,
        'rsi_length':         28,           # 14D × 2 para 4H
        'rsi_min_sell':       60.0,
        'rsi_max_buy':        40.0,
        'ema_fast_len':       18,           # 9D × 2 para 4H
        'ema_slow_len':       42,           # 21D × 2 para 4H
        'ema_trend_len':      400,          # 200D × 2 para 4H
        'atr_length':         28,           # 14D × 2 para 4H
        'atr_sl_mult':        1.6,          # Menos agresivo para 4H (era 2.0)
        'vol_mult':           1.3,
    }
}

# ══════════════════════════════════════
# CONTROL ANTI-SPAM
# ══════════════════════════════════════
alertas_enviadas = {}
ultimo_analisis = {}

# ══════════════════════════════════════
# TELEGRAM
# ══════════════════════════════════════
def enviar_telegram(mensaje):
    try:
        url     = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {
            "chat_id":    TELEGRAM_CHAT_ID,
            "text":       mensaje,
            "parse_mode": "HTML"
        }
        r = requests.post(url, json=payload, timeout=10)
        if r.status_code == 200:
            print(f"✅ Telegram enviado → {r.status_code}")
        else:
            print(f"❌ Error Telegram → Status {r.status_code}")
            print(f"   Respuesta: {r.text}")
            print(f"   Mensaje (primeros 200 chars): {mensaje[:200]}...")
    except Exception as e:
        print(f"❌ Error Telegram (excepción): {e}")

# ══════════════════════════════════════
# INDICADORES TÉCNICOS
# ══════════════════════════════════════
def calcular_rsi(series, length):
    delta = series.diff()
    gain  = delta.clip(lower=0)
    loss  = -delta.clip(upper=0)
    avg_g = gain.ewm(com=length - 1, min_periods=length).mean()
    avg_l = loss.ewm(com=length - 1, min_periods=length).mean()
    rs    = avg_g / avg_l
    return 100 - (100 / (1 + rs))

def calcular_ema(series, length):
    return series.ewm(span=length, adjust=False).mean()

def calcular_atr(df, length):
    high       = df['High']
    low        = df['Low']
    close_prev = df['Close'].shift(1)
    tr = pd.concat([
        high - low,
        (high - close_prev).abs(),
        (low  - close_prev).abs()
    ], axis=1).max(axis=1)
    return tr.ewm(com=length - 1, min_periods=length).mean()

def calcular_bollinger_bands(series, length=40, std_dev=2):
    """Bandas de Bollinger para 4H"""
    bb_mid = series.rolling(window=length).mean()
    std = series.rolling(window=length).std()
    bb_upper = bb_mid + (std * std_dev)
    bb_lower = bb_mid - (std * std_dev)
    bb_width = (bb_upper - bb_lower) / bb_mid
    return bb_upper, bb_mid, bb_lower, bb_width

def calcular_macd(series, fast=24, slow=52, signal=18):
    """MACD para 4H (periodos x2)"""
    ema_fast = series.ewm(span=fast, adjust=False).mean()
    ema_slow = series.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram

def calcular_obv(df):
    """On-Balance Volume"""
    obv = pd.Series(index=df.index, dtype=float)
    obv.iloc[0] = df['Volume'].iloc[0]
    
    for i in range(1, len(df)):
        if df['Close'].iloc[i] > df['Close'].iloc[i-1]:
            obv.iloc[i] = obv.iloc[i-1] + df['Volume'].iloc[i]
        elif df['Close'].iloc[i] < df['Close'].iloc[i-1]:
            obv.iloc[i] = obv.iloc[i-1] - df['Volume'].iloc[i]
        else:
            obv.iloc[i] = obv.iloc[i-1]
    
    return obv

def calcular_adx(df, length=28):
    """ADX para 4H (periodo x2)"""
    high = df['High']
    low = df['Low']
    close = df['Close']
    
    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low - close.shift(1)).abs()
    ], axis=1).max(axis=1)
    
    up_move = high - high.shift(1)
    down_move = low.shift(1) - low
    
    plus_dm = pd.Series(0.0, index=df.index)
    minus_dm = pd.Series(0.0, index=df.index)
    
    plus_dm[(up_move > down_move) & (up_move > 0)] = up_move
    minus_dm[(down_move > up_move) & (down_move > 0)] = down_move
    
    atr = tr.ewm(com=length - 1, min_periods=length).mean()
    plus_di = 100 * (plus_dm.ewm(com=length - 1, min_periods=length).mean() / atr)
    minus_di = 100 * (minus_dm.ewm(com=length - 1, min_periods=length).mean() / atr)
    
    dx = 100 * ((plus_di - minus_di).abs() / (plus_di + minus_di))
    adx = dx.ewm(com=length - 1, min_periods=length).mean()
    
    return adx, plus_di, minus_di

def detectar_evening_star(df, idx):
    """Evening Star: Patrón de reversión bajista"""
    if idx < 2:
        return False
    
    v1 = df.iloc[idx - 2]
    v2 = df.iloc[idx - 1]
    v3 = df.iloc[idx]
    
    v1_bullish = v1['Close'] > v1['Open']
    v1_body = abs(v1['Close'] - v1['Open'])
    v1_range = v1['High'] - v1['Low']
    v1_large_body = v1_body > v1_range * 0.6
    
    v2_body = abs(v2['Close'] - v2['Open'])
    v2_range = v2['High'] - v2['Low']
    v2_small = v2_body < v2_range * 0.3
    v2_gap_up = v2['Open'] > v1['Close']
    
    v3_bearish = v3['Close'] < v3['Open']
    v3_body = abs(v3['Close'] - v3['Open'])
    v3_range = v3['High'] - v3['Low']
    v3_large_body = v3_body > v3_range * 0.6
    v3_closes_in_v1 = v3['Close'] < (v1['Open'] + v1['Close']) / 2
    
    return v1_bullish and v1_large_body and v2_small and v2_gap_up and v3_bearish and v3_large_body and v3_closes_in_v1

def detectar_morning_star(df, idx):
    """Morning Star: Patrón de reversión alcista"""
    if idx < 2:
        return False
    
    v1 = df.iloc[idx - 2]
    v2 = df.iloc[idx - 1]
    v3 = df.iloc[idx]
    
    v1_bearish = v1['Close'] < v1['Open']
    v1_body = abs(v1['Close'] - v1['Open'])
    v1_range = v1['High'] - v1['Low']
    v1_large_body = v1_body > v1_range * 0.6
    
    v2_body = abs(v2['Close'] - v2['Open'])
    v2_range = v2['High'] - v2['Low']
    v2_small = v2_body < v2_range * 0.3
    v2_gap_down = v2['Open'] < v1['Close']
    
    v3_bullish = v3['Close'] > v3['Open']
    v3_body = abs(v3['Close'] - v3['Open'])
    v3_range = v3['High'] - v3['Low']
    v3_large_body = v3_body > v3_range * 0.6
    v3_closes_in_v1 = v3['Close'] > (v1['Open'] + v1['Close']) / 2
    
    return v1_bearish and v1_large_body and v2_small and v2_gap_down and v3_bullish and v3_large_body and v3_closes_in_v1

# ══════════════════════════════════════
# LÓGICA PRINCIPAL
# ══════════════════════════════════════
def analizar(simbolo, params):
    print(f"\n🔍 Analizando {simbolo} [4H]...")

    # Descargar datos 4H
    # SPX500 solo cotiza en horario de mercado (~6 velas/día vs 6 de cripto 24h)
    # con 60d retorna ~120 velas, umbral ajustado a 80
    try:
        df = yf.download(params['ticker_yf'], period='60d', interval='4h', progress=False)
        if df.empty or len(df) < 80:
            print(f"⚠️ Datos insuficientes para {simbolo}")
            return
    except Exception as e:
        print(f"❌ Error descargando {simbolo}: {e}")
        return

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    df = df.copy()

    # Indicadores base (4H ajustados)
    df['rsi']       = calcular_rsi(df['Close'], params['rsi_length'])
    df['ema_fast']  = calcular_ema(df['Close'], params['ema_fast_len'])
    df['ema_slow']  = calcular_ema(df['Close'], params['ema_slow_len'])
    df['ema_trend'] = calcular_ema(df['Close'], params['ema_trend_len'])
    df['atr']       = calcular_atr(df, params['atr_length'])
    df['vol_avg']   = df['Volume'].rolling(20).mean()

    # Nuevos Indicadores (4H ajustados)
    df['bb_upper'], df['bb_mid'], df['bb_lower'], df['bb_width'] = calcular_bollinger_bands(df['Close'])
    df['macd'], df['macd_signal'], df['macd_hist'] = calcular_macd(df['Close'])
    df['obv'] = calcular_obv(df)
    df['adx'], df['di_plus'], df['di_minus'] = calcular_adx(df)
    df['obv_ema'] = calcular_ema(df['obv'], 20)

    # Velas
    df['body']        = (df['Close'] - df['Open']).abs()
    df['upper_wick']  = df['High'] - df[['Close','Open']].max(axis=1)
    df['lower_wick']  = df[['Close','Open']].min(axis=1) - df['Low']
    df['total_range'] = df['High'] - df['Low']
    df['is_bearish']  = df['Close'] < df['Open']
    df['is_bullish']  = df['Close'] > df['Open']

    # Última vela completa
    row  = df.iloc[-2]
    prev = df.iloc[-3]
    p2   = df.iloc[-4]

    close = row['Close']
    high  = row['High']
    low   = row['Low']
    open_ = row['Open']
    vol   = row['Volume']

    rsi       = row['rsi']
    rsi_prev  = prev['rsi']
    ema_fast  = row['ema_fast']
    ema_slow  = row['ema_slow']
    ema_trend = row['ema_trend']
    atr       = row['atr']
    vol_avg   = row['vol_avg']

    bb_upper  = row['bb_upper']
    bb_lower  = row['bb_lower']
    bb_mid    = row['bb_mid']
    bb_width  = row['bb_width']
    bb_width_prev = prev['bb_width']
    
    macd      = row['macd']
    macd_signal = row['macd_signal']
    macd_hist = row['macd_hist']
    macd_hist_prev = prev['macd_hist']
    
    obv       = row['obv']
    obv_prev  = prev['obv']
    obv_ema   = row['obv_ema']
    
    adx       = row['adx']
    di_plus   = row['di_plus']
    di_minus  = row['di_minus']

    body        = row['body']
    upper_wick  = row['upper_wick']
    lower_wick  = row['lower_wick']
    total_range = row['total_range']
    is_bearish  = row['is_bearish']
    is_bullish  = row['is_bullish']

    # Parámetros de zona
    zrh  = params['zona_resist_high']
    zrl  = params['zona_resist_low']
    zsh  = params['zona_soporte_high']
    zsl  = params['zona_soporte_low']
    tol  = params['tolerancia']
    lop  = params['limit_offset_pct']
    cd   = params['cancelar_dist']
    av   = params['anticipar_velas']
    vm   = params['vol_mult']
    rsms = params['rsi_min_sell']
    rsmb = params['rsi_max_buy']
    asm  = params['atr_sl_mult']

    sell_limit = zrl + (zrh - zrl) * (lop / 100 * 10)
    buy_limit  = zsh - (zsh - zsl) * (lop / 100 * 10)

    avg_candle_range = df['total_range'].iloc[-6:-1].mean()
    dist_to_resist   = zrl - close
    dist_to_support  = close - zsh

    aproximando_resistencia = (dist_to_resist > 0 and
                               dist_to_resist < avg_candle_range * av and
                               close > df['Close'].iloc[-5])

    aproximando_soporte     = (dist_to_support > 0 and
                               dist_to_support < avg_candle_range * av and
                               close < df['Close'].iloc[-5])

    en_zona_resist  = (high >= zrl - tol) and (high <= zrh + tol)
    en_zona_soporte = (low  >= zsl - tol) and (low  <= zsh + tol)

    cancelar_sell = close > zrh * (1 + cd / 100)
    cancelar_buy  = close < zsl * (1 - cd / 100)

    # BLOQUE VENTA
    intento_rotura_fallido = (high >= zrl) and (close < zrl)
    shooting_star     = is_bearish and upper_wick > body*2 and lower_wick < body*0.3 and en_zona_resist
    bearish_engulfing = is_bearish and open_ >= float(prev['High']) and close <= float(prev['Low']) and en_zona_resist
    bearish_marubozu  = is_bearish and body > total_range*0.8 and en_zona_resist
    doji_resist       = body < total_range*0.1 and en_zona_resist and upper_wick > body*2
    vela_rechazo      = shooting_star or bearish_engulfing or bearish_marubozu or doji_resist

    rsi_alto_girando = (rsi >= rsms) and (rsi < rsi_prev)
    rsi_sobrecompra  = rsi >= 70

    lookback = 5
    price_new_high      = high > float(df['High'].iloc[-lookback-2:-2].max())
    rsi_lower_high      = rsi  < float(df['rsi'].iloc[-lookback-2:-2].max())
    divergencia_bajista = price_new_high and rsi_lower_high and rsi > 50

    vol_alto_rechazo = vol > vol_avg * vm
    vol_decreciente  = (vol < float(prev['Volume'])) and (float(prev['Volume']) < float(p2['Volume'])) and is_bullish

    emas_bajistas      = ema_fast < ema_slow
    bajo_ema200        = close < ema_trend
    max_decreciente    = (high < float(prev['High'])) and (float(prev['High']) < float(p2['High']))
    min_decreciente    = (low  < float(prev['Low']))  and (float(prev['Low'])  < float(p2['Low']))
    estructura_bajista = max_decreciente or min_decreciente

    bb_toca_superior = close >= bb_upper or high >= bb_upper
    bb_squeeze = bb_width < 0.02
    
    macd_cruce_bajista = (macd < macd_signal) and (macd_hist < 0) and (macd_hist_prev >= 0)
    macd_divergencia_bajista = price_new_high and (macd < float(df['macd'].iloc[-lookback-2:-2].max()))
    macd_negativo = macd < 0
    
    adx_tendencia_fuerte = adx > 25
    adx_bajista = (di_minus > di_plus) and adx_tendencia_fuerte
    adx_lateral = adx < 20
    
    obv_divergencia_bajista = price_new_high and (obv < float(df['obv'].iloc[-lookback-2:-2].max()))
    obv_decreciente = obv < obv_prev and obv < obv_ema
    
    evening_star = detectar_evening_star(df, len(df) - 2)

    score_sell = 0
    score_sell += 2 if en_zona_resist          else 0
    score_sell += 2 if vela_rechazo            else 0
    score_sell += 2 if vol_alto_rechazo        else 0
    score_sell += 1 if rsi_alto_girando        else 0
    score_sell += 1 if rsi_sobrecompra         else 0
    score_sell += 1 if divergencia_bajista     else 0
    score_sell += 1 if emas_bajistas           else 0
    score_sell += 1 if estructura_bajista      else 0
    score_sell += 1 if intento_rotura_fallido  else 0
    score_sell += 1 if vol_decreciente         else 0
    score_sell += 1 if (shooting_star and vol_alto_rechazo)       else 0
    score_sell += 1 if (divergencia_bajista and rsi_sobrecompra)  else 0
    score_sell += 1 if bajo_ema200             else 0
    score_sell += 2 if bb_toca_superior        else 0
    score_sell += 2 if evening_star            else 0
    score_sell += 2 if macd_cruce_bajista      else 0
    score_sell += 2 if adx_bajista             else 0
    score_sell += 1 if macd_divergencia_bajista else 0
    score_sell += 1 if obv_divergencia_bajista else 0
    score_sell += 1 if obv_decreciente         else 0
    score_sell += 1 if macd_negativo           else 0
    
    if adx_lateral:
        score_sell = max(0, score_sell - 3)

    # BLOQUE COMPRA
    intento_caida_fallido = (low <= zsh) and (close > zsh)
    hammer            = is_bullish and lower_wick > body*2 and upper_wick < body*0.3 and en_zona_soporte
    bullish_engulfing = is_bullish and open_ <= float(prev['Low']) and close >= float(prev['High']) and en_zona_soporte
    bullish_marubozu  = is_bullish and body > total_range*0.8 and en_zona_soporte
    doji_soporte      = body < total_range*0.1 and en_zona_soporte and lower_wick > body*2
    vela_rebote       = hammer or bullish_engulfing or bullish_marubozu or doji_soporte

    rsi_bajo_girando = (rsi <= rsmb) and (rsi > rsi_prev)
    rsi_sobreventa   = rsi <= 30

    price_new_low       = low < float(df['Low'].iloc[-lookback-2:-2].min())
    rsi_higher_low      = rsi > float(df['rsi'].iloc[-lookback-2:-2].min())
    divergencia_alcista = price_new_low and rsi_higher_low and rsi < 50

    vol_alto_rebote      = vol > vol_avg * vm
    vol_decreciente_sell = (vol < float(prev['Volume'])) and (float(prev['Volume']) < float(p2['Volume'])) and is_bearish

    emas_alcistas      = ema_fast > ema_slow
    sobre_ema200       = close > ema_trend
    max_creciente      = (high > float(prev['High'])) and (float(prev['High']) > float(p2['High']))
    min_creciente      = (low  > float(prev['Low']))  and (float(prev['Low'])  > float(p2['Low']))
    estructura_alcista = max_creciente or min_creciente

    bb_toca_inferior = close <= bb_lower or low <= bb_lower
    
    macd_cruce_alcista = (macd > macd_signal) and (macd_hist > 0) and (macd_hist_prev <= 0)
    macd_divergencia_alcista = price_new_low and (macd > float(df['macd'].iloc[-lookback-2:-2].min()))
    macd_positivo = macd > 0
    
    adx_alcista = (di_plus > di_minus) and adx_tendencia_fuerte
    
    obv_divergencia_alcista = price_new_low and (obv > float(df['obv'].iloc[-lookback-2:-2].min()))
    obv_creciente = obv > obv_prev and obv > obv_ema
    
    morning_star = detectar_morning_star(df, len(df) - 2)

    score_buy = 0
    score_buy += 2 if en_zona_soporte          else 0
    score_buy += 2 if vela_rebote              else 0
    score_buy += 2 if vol_alto_rebote          else 0
    score_buy += 1 if rsi_bajo_girando         else 0
    score_buy += 1 if rsi_sobreventa           else 0
    score_buy += 1 if divergencia_alcista      else 0
    score_buy += 1 if emas_alcistas            else 0
    score_buy += 1 if estructura_alcista       else 0
    score_buy += 1 if intento_caida_fallido    else 0
    score_buy += 1 if vol_decreciente_sell     else 0
    score_buy += 1 if (hammer and vol_alto_rebote)             else 0
    score_buy += 1 if (divergencia_alcista and rsi_sobreventa) else 0
    score_buy += 1 if sobre_ema200             else 0
    score_buy += 2 if bb_toca_inferior        else 0
    score_buy += 2 if morning_star            else 0
    score_buy += 2 if macd_cruce_alcista      else 0
    score_buy += 2 if adx_alcista             else 0
    score_buy += 1 if macd_divergencia_alcista else 0
    score_buy += 1 if obv_divergencia_alcista else 0
    score_buy += 1 if obv_creciente           else 0
    score_buy += 1 if macd_positivo           else 0
    
    if adx_lateral:
        score_buy = max(0, score_buy - 3)

    # NIVELES DE SEÑAL 4H (MÁS ESTRICTOS)
    senal_sell_maxima = score_sell >= 14
    senal_sell_fuerte = score_sell >= 12
    senal_sell_media  = score_sell >= 9
    senal_sell_alerta = score_sell >= 5
    senal_buy_maxima  = score_buy  >= 14
    senal_buy_fuerte  = score_buy  >= 12
    senal_buy_media   = score_buy  >= 9
    senal_buy_alerta  = score_buy  >= 5

    sl_venta  = max(zrh, close + atr * asm)
    sl_compra = min(zsl, close - atr * asm)

    tp1_v = params['tp1_venta']
    tp2_v = params['tp2_venta']
    tp3_v = params['tp3_venta']
    tp1_c = params['tp1_compra']
    tp2_c = params['tp2_compra']
    tp3_c = params['tp3_compra']

    def rr(limit, sl, tp):
        return round(abs(tp - limit) / abs(sl - limit), 1) if abs(sl - limit) > 0 else 0

    fecha = df.index[-2].strftime('%Y-%m-%d %H:%M')
    
    # VERIFICAR SI YA SE ANALIZÓ ESTA VELA
    clave_simbolo = f"{simbolo}_4H"
    
    if clave_simbolo in ultimo_analisis:
        ultima_fecha = ultimo_analisis[clave_simbolo]['fecha']
        ultimo_score_sell = ultimo_analisis[clave_simbolo]['score_sell']
        ultimo_score_buy = ultimo_analisis[clave_simbolo]['score_buy']
        
        if (ultima_fecha == fecha and 
            abs(ultimo_score_sell - score_sell) <= 1 and 
            abs(ultimo_score_buy - score_buy) <= 1):
            print(f"  ℹ️  Vela {fecha} ya analizada - Sin cambios significativos")
            return
    
    ultimo_analisis[clave_simbolo] = {
        'fecha': fecha,
        'score_sell': score_sell,
        'score_buy': score_buy
    }
    
    print(f"  📅 Vela:  {fecha}")
    print(f"  💰 Close: {round(close, 2)}")
    print(f"  📊 Score SELL: {score_sell}/15 | Score BUY: {score_buy}/15")
    print(f"  🔴 SELL → Alerta:{senal_sell_alerta} Media:{senal_sell_media} Fuerte:{senal_sell_fuerte} Máxima:{senal_sell_maxima}")
    print(f"  🟢 BUY  → Alerta:{senal_buy_alerta}  Media:{senal_buy_media}  Fuerte:{senal_buy_fuerte}  Máxima:{senal_buy_maxima}")

    clave_vela = f"{simbolo}_4H_{fecha}"

    def ya_enviada(tipo):
        return alertas_enviadas.get(f"{clave_vela}_{tipo}", False)

    def marcar_enviada(tipo):
        alertas_enviadas[f"{clave_vela}_{tipo}"] = True

    # ENVIAR SEÑALES
    if senal_sell_alerta and not cancelar_sell:
        nivel = ("⚡ SELL MÁXIMA" if senal_sell_maxima else
                 "🔴 SELL FUERTE" if senal_sell_fuerte else
                 "⚠️ SELL MEDIA"  if senal_sell_media  else
                 "👀 SELL ALERTA")
        tipo_clave = ("SELL_MAX" if senal_sell_maxima else
                      "SELL_FUE" if senal_sell_fuerte else
                      "SELL_MED" if senal_sell_media  else
                      "SELL_ALE")
        if not ya_enviada(tipo_clave):
            if db and db.existe_senal_reciente(f"{simbolo}_4H", 'VENTA', horas=2):
                print(f"  ℹ️  Señal VENTA duplicada - No se guarda")
                return
            
            msg = (f"{nivel} — <b>SPX500 4H</b> {nivel.split()[0]}\n"
                   f"━━━━━━━━━━━━━━━━━━━━\n"
                   f"💰 <b>Precio:</b>     ${round(close, 2)}\n"
                   f"📌 <b>SELL LIMIT:</b> ${round(sell_limit, 2)}\n"
                   f"🛑 <b>Stop Loss:</b>  ${round(sl_venta, 2)}\n"
                   f"🎯 <b>TP1:</b> ${tp1_v}  R:R {rr(sell_limit, sl_venta, tp1_v)}:1\n"
                   f"🎯 <b>TP2:</b> ${tp2_v}  R:R {rr(sell_limit, sl_venta, tp2_v)}:1\n"
                   f"🎯 <b>TP3:</b> ${tp3_v}  R:R {rr(sell_limit, sl_venta, tp3_v)}:1\n"
                   f"━━━━━━━━━━━━━━━━━━━━\n"
                   f"📊 <b>Score:</b> {score_sell}/15  📉 <b>RSI:</b> {round(rsi, 1)}\n"
                   f"⏱️ <b>TF:</b> 4H  📅 {fecha}")
            
            if db:
                senal_data = {
                    'timestamp': datetime.now(timezone.utc),
                    'simbolo': f"{simbolo}_4H",
                    'direccion': 'VENTA',
                    'precio_entrada': sell_limit,
                    'tp1': tp1_v,
                    'tp2': tp2_v,
                    'tp3': tp3_v,
                    'sl': sl_venta,
                    'score': score_sell,
                    'timeframe': '4H',
                    'indicadores': json.dumps({
                        'rsi': round(rsi, 1),
                        'ema_fast': round(ema_fast, 2),
                        'ema_slow': round(ema_slow, 2),
                        'atr': round(atr, 2),
                        'bb_upper': round(bb_upper, 2),
                        'bb_lower': round(bb_lower, 2),
                        'macd': round(macd, 2),
                        'adx': round(adx, 2)
                    }),
                    'patron_velas': f"Evening Star: {evening_star}, Shooting Star: {shooting_star}",
                    'version_detector': 'SPX 4H-v1.0'
                }
                
                try:
                    senal_id = db.guardar_senal(senal_data)
                    print(f"  💾 Señal VENTA 4H guardada en DB con ID: {senal_id}")
                except Exception as e:
                    print(f"  ⚠️ Error guardando señal en DB: {e}")
            
            enviar_telegram(msg)
            marcar_enviada(tipo_clave)

    if senal_buy_alerta and not cancelar_buy:
        nivel = ("⚡ BUY MÁXIMA"  if senal_buy_maxima else
                 "🟢 BUY FUERTE"  if senal_buy_fuerte else
                 "⚠️ BUY MEDIA"   if senal_buy_media  else
                 "👀 BUY ALERTA")
        tipo_clave = ("BUY_MAX" if senal_buy_maxima else
                      "BUY_FUE" if senal_buy_fuerte else
                      "BUY_MED" if senal_buy_media  else
                      "BUY_ALE")
        if not ya_enviada(tipo_clave):
            if db and db.existe_senal_reciente(f"{simbolo}_4H", 'COMPRA', horas=2):
                print(f"  ℹ️  Señal COMPRA duplicada - No se guarda")
                return
            
            msg = (f"{nivel} — <b>SPX500 4H</b> {nivel.split()[0]}\n"
                   f"━━━━━━━━━━━━━━━━━━━━\n"
                   f"💰 <b>Precio:</b>    ${round(close, 2)}\n"
                   f"📌 <b>BUY LIMIT:</b> ${round(buy_limit, 2)}\n"
                   f"🛑 <b>Stop Loss:</b> ${round(sl_compra, 2)}\n"
                   f"🎯 <b>TP1:</b> ${tp1_c}  R:R {rr(buy_limit, sl_compra, tp1_c)}:1\n"
                   f"🎯 <b>TP2:</b> ${tp2_c}  R:R {rr(buy_limit, sl_compra, tp2_c)}:1\n"
                   f"🎯 <b>TP3:</b> ${tp3_c}  R:R {rr(buy_limit, sl_compra, tp3_c)}:1\n"
                   f"━━━━━━━━━━━━━━━━━━━━\n"
                   f"📊 <b>Score:</b> {score_buy}/15  📉 <b>RSI:</b> {round(rsi, 1)}\n"
                   f"⏱️ <b>TF:</b> 4H  📅 {fecha}")
            
            if db:
                senal_data = {
                    'timestamp': datetime.now(timezone.utc),
                    'simbolo': f"{simbolo}_4H",
                    'direccion': 'COMPRA',
                    'precio_entrada': buy_limit,
                    'tp1': tp1_c,
                    'tp2': tp2_c,
                    'tp3': tp3_c,
                    'sl': sl_compra,
                    'score': score_buy,
                    'timeframe': '4H',
                    'indicadores': json.dumps({
                        'rsi': round(rsi, 1),
                        'ema_fast': round(ema_fast, 2),
                        'ema_slow': round(ema_slow, 2),
                        'atr': round(atr, 2),
                        'bb_upper': round(bb_upper, 2),
                        'bb_lower': round(bb_lower, 2),
                        'macd': round(macd, 2),
                        'adx': round(adx, 2)
                    }),
                    'patron_velas': f"Morning Star: {morning_star}, Hammer: {hammer}",
                    'version_detector': 'SPX 4H-v1.0'
                }
                
                try:
                    senal_id = db.guardar_senal(senal_data)
                    print(f"  💾 Señal COMPRA 4H guardada en DB con ID: {senal_id}")
                except Exception as e:
                    print(f"  ⚠️ Error guardando señal en DB: {e}")
            
            enviar_telegram(msg)
            marcar_enviada(tipo_clave)

# ══════════════════════════════════════
# BUCLE PRINCIPAL
# ══════════════════════════════════════
def main():
    print("🚀 Detector SPX500 4H iniciado")
    print(f"⏱️  Revisando cada {CHECK_INTERVAL//60} minutos")

    enviar_telegram("🚀 <b>Detector SPX500 4H iniciado</b>\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    "📊 Monitorizando: SPX500\n"
                    "⏱️ Timeframe: 4H\n"
                    "🔄 Revisión cada 7 minutos\n"
                    "💚 Filtros más estrictos que 1D\n"
                    "✅ Score mínimo: 5 (alerta), 9 (media), 12 (fuerte), 14 (máxima)\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    f"🔴 Resistencia: $5,800 - $6,100\n"
                    f"🟢 Soporte:     $4,800 - $5,200")

    ciclo = 0
    while True:
        ciclo += 1
        ahora = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        print(f"\n{'='*60}")
        print(f"[{ahora}] 🔄 CICLO #{ciclo} - Iniciando análisis SPX500 4H")
        print(f"{'='*60}")
        
        for simbolo, params in SIMBOLOS.items():
            print(f"\n📊 Analizando {simbolo} [4H]...")
            analizar(simbolo, params)
        
        print(f"\n{'='*60}")
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] ✅ Ciclo #{ciclo} completado")
        print(f"⏳ Esperando {CHECK_INTERVAL//60} minutos hasta el próximo análisis...")
        print(f"{'='*60}\n")
        time.sleep(CHECK_INTERVAL)

if __name__ == '__main__':
    main()
