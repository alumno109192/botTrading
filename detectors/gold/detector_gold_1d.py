import os
from adapters.data_provider import get_ohlcv

import yfinance as yf
import pandas as pd
import numpy as np
import requests
import time
import json
from datetime import datetime, timezone
from dotenv import load_dotenv
from services import tf_bias
from services.dxy_bias import get_dxy_bias, ajustar_score_por_dxy
from services.cot_bias import get_cot_bias, ajustar_score_por_cot
from services.open_interest import get_oi_bias, ajustar_score_por_oi
from services.economic_calendar import obtener_aviso_macro

# Cargar variables de entorno
load_dotenv()
from adapters.telegram import enviar_telegram as _enviar_telegram_base

_aviso_macro = ""

def enviar_telegram(mensaje):
    sufijo = f"\n⚠️ <b>Evento macro próximo:</b> {_aviso_macro}" if _aviso_macro else ""
    return _enviar_telegram_base(mensaje + sufijo, TELEGRAM_THREAD_ID)

from adapters.database import get_db
from core.base_detector import BaseDetector
import logging
logger = logging.getLogger('bottrading')

db = get_db()

# ══════════════════════════════════════
# CONFIGURACIÓN
# ══════════════════════════════════════
TELEGRAM_TOKEN   = os.environ.get('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID   = os.environ.get('TELEGRAM_CHAT_ID')
TELEGRAM_THREAD_ID = int(os.environ.get('THREAD_ID_SWING') or 0) or None

CHECK_INTERVAL = 10 * 60  # cada 10 minutos (balance óptimo para timeframe 1D)
                          # mantiene el servidor activo

# ══════════════════════════════════════
# PARÁMETROS — igual que Pine Script
# ══════════════════════════════════════
SIMBOLOS = {
    'XAUUSD': {
        'ticker_yf':          'GC=F',       # Oro en Yahoo Finance
        # Zonas S/R calculadas automáticamente en analizar() — sin mantenimiento manual
        'sr_lookback':        30,           # 30 velas 1D ≈ 6 semanas de historia
        'sr_zone_mult':       0.8,          # ancho de zona = atr × 0.8
        'limit_offset_pct':   0.3,
        'anticipar_velas':    3,
        'cancelar_dist':      1.0,
        'rsi_length':         14,
        'rsi_min_sell':       55.0,
        'rsi_max_buy':        45.0,
        'ema_fast_len':       9,
        'ema_slow_len':       21,
        'ema_trend_len':      200,
        'atr_length':         14,
        'atr_sl_mult':        1.5,
        'atr_tp1_mult':       3.0,    # TP1: 3.0× ATR (~$150-240 desde entry)
        'atr_tp2_mult':       5.0,    # TP2: 5.0× ATR
        'atr_tp3_mult':       8.0,    # TP3: 8.0× ATR (objetivo swing amplio)
        'vol_mult':           1.2,
    }
}

# ══════════════════════════════════════
# CONTROL DE ALERTAS YA ENVIADAS
# (evita spam en la misma vela)
# ══════════════════════════════════════
alertas_enviadas = {}
ultimo_analisis = {}  # Guarda última fecha y scores analizados


# ══════════════════════════════════════
from core.indicators import (
    calcular_rsi, calcular_ema, calcular_atr,
    calcular_bollinger_bands, calcular_macd, calcular_obv, calcular_adx,
    detectar_evening_star, detectar_morning_star,
    detectar_rotura_alcista, detectar_rotura_bajista,
    detectar_doble_techo, detectar_doble_suelo,
    detectar_canal_roto,
    detectar_ruptura_soporte_horizontal, detectar_ruptura_resistencia_horizontal,
    detectar_cuña_descendente, detectar_cuña_ascendente,
    detectar_precio_en_fibonacci, detectar_rebote_alcista, detectar_rebote_bajista,
    calcular_fibonacci,
)

# ══════════════════════════════════════
# ANÁLISIS DE SENTIMIENTO DEL MERCADO
# (Mejora calidad - solo señales con confluencia)
# ══════════════════════════════════════
def calcular_sentimiento_bajista(row, prev, p2, df, params):
    """
    Calcula el score de sentimiento bajista (0-10)
    Returns: (score, factores_detectados)
    """
    factores = []
    score = 0
    
    close = row['Close']
    high = row['High']
    rsi = row['rsi']
    ema_fast = row['ema_fast']
    ema_slow = row['ema_slow']
    ema_trend = row['ema_trend']
    
    # 1. Estructura bajista (2 puntos)
    max_decreciente = (high < float(prev['High'])) and (float(prev['High']) < float(p2['High']))
    min_decreciente = (row['Low'] < float(prev['Low'])) and (float(prev['Low']) < float(p2['Low']))
    if max_decreciente or min_decreciente:
        factores.append("Estructura bajista")
        score += 2
    
    # 2. EMAs bajistas (1 punto cada una)
    if ema_fast < ema_slow:
        factores.append("EMAs bajistas (9<21)")
        score += 1
    if close < ema_trend:
        factores.append("Precio bajo EMA200")
        score += 1
    
    # 3. RSI en zona alta (1 punto)
    if rsi > params['rsi_min_sell']:
        factores.append(f"RSI alto ({rsi:.1f})")
        score += 1
    
    # 4. En zona de resistencia (2 puntos)
    zrh = float(df['High'].iloc[-31:-1].max())
    zrl = zrh - (df['High'] - df['Low']).iloc[-14:-1].mean()
    tol_s = (df['High'] - df['Low']).iloc[-14:-1].mean() * 0.4
    if (high >= zrl - tol_s) and (high <= zrh + tol_s):
        factores.append("En zona resistencia")
        score += 2
    
    # 5. Divergencia bajista (2 puntos)
    try:
        lookback = 5
        if len(df) >= lookback + 3:
            price_new_high = high > float(df['High'].iloc[-lookback-2:-2].max())
            rsi_lower_high = rsi < float(df['rsi'].iloc[-lookback-2:-2].max())
            if price_new_high and rsi_lower_high and rsi > 50:
                factores.append("Divergencia bajista")
                score += 2
    except:
        pass
    
    # 6. Tendencia de largo plazo bajista (1 punto)
    if ema_trend < df['Close'].iloc[-20:].mean():
        factores.append("Tendencia LT bajista")
        score += 1
    
    return score, factores

def calcular_sentimiento_alcista(row, prev, p2, df, params):
    """
    Calcula el score de sentimiento alcista (0-10)
    Returns: (score, factores_detectados)
    """
    factores = []
    score = 0
    
    close = row['Close']
    low = row['Low']
    rsi = row['rsi']
    ema_fast = row['ema_fast']
    ema_slow = row['ema_slow']
    ema_trend = row['ema_trend']
    
    # 1. Estructura alcista (2 puntos)
    max_creciente = (row['High'] > float(prev['High'])) and (float(prev['High']) > float(p2['High']))
    min_creciente = (low > float(prev['Low'])) and (float(prev['Low']) > float(p2['Low']))
    if max_creciente or min_creciente:
        factores.append("Estructura alcista")
        score += 2
    
    # 2. EMAs alcistas (1 punto cada una)
    if ema_fast > ema_slow:
        factores.append("EMAs alcistas (9>21)")
        score += 1
    if close > ema_trend:
        factores.append("Precio sobre EMA200")
        score += 1
    
    # 3. RSI en zona baja (1 punto)
    if rsi < params['rsi_max_buy']:
        factores.append(f"RSI bajo ({rsi:.1f})")
        score += 1
    
    # 4. En zona de soporte (2 puntos) — dinámica (mirrors calcular_zonas_sr logic)
    zsl = float(df['Low'].iloc[-31:-1].min())
    zsh = zsl + (df['High'] - df['Low']).iloc[-14:-1].mean()
    tol = (df['High'] - df['Low']).iloc[-14:-1].mean() * 0.4
    if (low >= zsl - tol) and (low <= zsh + tol):
        factores.append("En zona soporte")
        score += 2
    
    # 5. Divergencia alcista (2 puntos)
    try:
        lookback = 5
        if len(df) >= lookback + 3:
            price_new_low = low < float(df['Low'].iloc[-lookback-2:-2].min())
            rsi_higher_low = rsi > float(df['rsi'].iloc[-lookback-2:-2].min())
            if price_new_low and rsi_higher_low and rsi < 50:
                factores.append("Divergencia alcista")
                score += 2
    except:
        pass
    
    # 6. Tendencia de largo plazo alcista (1 punto)
    if ema_trend > df['Close'].iloc[-20:].mean():
        factores.append("Tendencia LT alcista")
        score += 1
    
    return score, factores

# ══════════════════════════════════════
# LÓGICA PRINCIPAL — replica Pine Script
# ══════════════════════════════════════



class GoldDetector1D(BaseDetector):
    def analizar(self, simbolo, params):
        logger.info(f"\n🔍 Analizando {simbolo}...")

        # ── Aviso calendario económico (no bloquea, solo advierte en el mensaje) ──
        self.aviso_macro = obtener_aviso_macro(60, '1D', simbolo)

        # ── Descargar datos ──
        try:
            df, is_delayed = get_ohlcv(params['ticker_yf'], period='2y', interval='1d')
            if is_delayed:
                logger.warning("  ⚠️  [1D] Datos con delay (yfinance). Configura TWELVE_DATA_API_KEY para tiempo real.")
            if df.empty or len(df) < 210:
                logger.warning(f"⚠️ Datos insuficientes para {simbolo}")
                return
        except Exception as e:
            logger.error(f"❌ Error descargando {simbolo}: {e}")
            return

        # Limpiar columnas multi-index si existen
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        df = df.copy()

        # ── Indicadores base ──
        df['rsi']       = calcular_rsi(df['Close'], params['rsi_length'])
        df['ema_fast']  = calcular_ema(df['Close'], params['ema_fast_len'])
        df['ema_slow']  = calcular_ema(df['Close'], params['ema_slow_len'])
        df['ema_trend'] = calcular_ema(df['Close'], params['ema_trend_len'])
        df['atr']       = calcular_atr(df, params['atr_length'])
        df['vol_avg']   = df['Volume'].rolling(20).mean()

        # ── Nuevos Indicadores (Alta Prioridad) ──
        df['bb_upper'], df['bb_mid'], df['bb_lower'], df['bb_width'] = calcular_bollinger_bands(df['Close'])
        df['macd'], df['macd_signal'], df['macd_hist'] = calcular_macd(df['Close'])
        df['obv'] = calcular_obv(df)
        df['adx'], df['di_plus'], df['di_minus'] = calcular_adx(df)
    
        # OBV promedio para divergencias
        df['obv_ema'] = calcular_ema(df['obv'], 20)

        # ── Velas ──
        df['body']        = (df['Close'] - df['Open']).abs()
        df['upper_wick']  = df['High'] - df[['Close','Open']].max(axis=1)
        df['lower_wick']  = df[['Close','Open']].min(axis=1) - df['Low']
        df['total_range'] = df['High'] - df['Low']
        df['is_bearish']  = df['Close'] < df['Open']
        df['is_bullish']  = df['Close'] > df['Open']

        # Última vela completa (la de ayer, no la actual)
        row  = df.iloc[-2]   # vela cerrada
        prev = df.iloc[-3]   # vela anterior
        p2   = df.iloc[-4]   # dos velas atrás

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

        # Nuevos indicadores
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

        # ── Parámetros de zona (calculados automáticamente) ──
        zrl, zrh, zsl, zsh = self.calcular_zonas_sr(df, atr, params['sr_lookback'], params['sr_zone_mult'])
        tol  = round(atr * 0.4, 2)   # tolerancia dinámica: 40% del ATR
        lop  = params['limit_offset_pct']
        cd   = params['cancelar_dist']
        av   = params['anticipar_velas']
        vm   = params['vol_mult']
        rsms = params['rsi_min_sell']
        rsmb = params['rsi_max_buy']
        asm  = params['atr_sl_mult']
        logger.info(f"  📍 Zonas auto — Resist: ${zrl:.1f}-${zrh:.1f} | Soporte: ${zsl:.1f}-${zsh:.1f}")

        # ── Precios límite ──
        sell_limit = zrl + (zrh - zrl) * (lop / 100 * 10)
        buy_limit  = zsh - (zsh - zsl) * (lop / 100 * 10)

        # ── Proximidad a zonas ──
        avg_candle_range = df['total_range'].iloc[-6:-1].mean()
        dist_to_resist   = zrl - close
        dist_to_support  = close - zsh

        aproximando_resistencia = (dist_to_resist > 0 and
                                   dist_to_resist < avg_candle_range * av and
                                   close > df['Close'].iloc[-5])

        aproximando_soporte     = (dist_to_support > 0 and
                                   dist_to_support < avg_candle_range * av and
                                   close < df['Close'].iloc[-5])

        # ── Zonas alcanzadas ──
        en_zona_resist  = (high >= zrl - tol) and (high <= zrh + tol)
        en_zona_soporte = (low  >= zsl - tol) and (low  <= zsh + tol)

        # ── Cancelación ──
        cancelar_sell = close > zrh * (1 + cd / 100)
        cancelar_buy  = close < zsl * (1 - cd / 100)

        # ══════════════════════════════════
        # BLOQUE VENTA
        # ══════════════════════════════════
        intento_rotura_fallido = (high >= zrl) and (close < zrl)
        shooting_star     = is_bearish and upper_wick > body*2 and lower_wick < body*0.3 and en_zona_resist
        bearish_engulfing = is_bearish and open_ >= prev['High'] and close <= prev['Low'] and en_zona_resist
        bearish_marubozu  = is_bearish and body > total_range*0.8 and en_zona_resist
        doji_resist       = body < total_range*0.1 and en_zona_resist and upper_wick > body*2
        vela_rechazo      = shooting_star or bearish_engulfing or bearish_marubozu or doji_resist

        rsi_alto_girando = (rsi >= rsms) and (rsi < rsi_prev)
        rsi_sobrecompra  = rsi >= 70

        lookback = 5
        price_new_high      = high > df['High'].iloc[-lookback-2:-2].max()
        rsi_lower_high      = rsi  < df['rsi'].iloc[-lookback-2:-2].max()
        divergencia_bajista = price_new_high and rsi_lower_high and rsi > 50

        vol_alto_rechazo = vol > vol_avg * vm
        vol_decreciente  = (vol < prev['Volume']) and (prev['Volume'] < p2['Volume']) and is_bullish

        emas_bajistas      = ema_fast < ema_slow
        bajo_ema200        = close < ema_trend
        max_decreciente    = (high < prev['High']) and (prev['High'] < p2['High'])
        min_decreciente    = (low  < prev['Low'])  and (prev['Low']  < p2['Low'])
        estructura_bajista = max_decreciente or min_decreciente

        # ── Nuevas señales VENTA (indicadores alta prioridad) ──
        # Bollinger Bands
        bb_toca_superior = close >= bb_upper or high >= bb_upper
        bb_squeeze = bb_width < 0.02  # Squeeze detectado (próxima explosión)
    
        # MACD
        macd_cruce_bajista = (macd < macd_signal) and (macd_hist < 0) and (macd_hist_prev >= 0)
        macd_divergencia_bajista = price_new_high and (macd < df['macd'].iloc[-lookback-2:-2].max())
        macd_negativo = macd < 0
    
        # ADX
        adx_tendencia_fuerte = adx > 25  # Tendencia confirmada
        adx_bajista = (di_minus > di_plus) and adx_tendencia_fuerte
        adx_lateral = adx < 20  # Evitar señales en lateral
    
        # OBV
        obv_divergencia_bajista = price_new_high and (obv < df['obv'].iloc[-lookback-2:-2].max())
        obv_decreciente = obv < obv_prev and obv < obv_ema
    
        # Evening Star
        evening_star = detectar_evening_star(df, len(df) - 2)

        # ── ROTURA DE NIVEL Y DOBLES TECHOS/SUELOS ──────────────────────────
        rotura_alcista = detectar_rotura_alcista(df, zrh, atr, params['vol_mult'])
        rotura_bajista = detectar_rotura_bajista(df, zsl, atr, params['vol_mult'])

        _dt_lookback = min(params['sr_lookback'], 40)
        dt_detectado, dt_nivel_techo, dt_neckline = detectar_doble_techo(
            df, atr, lookback=_dt_lookback, tol_mult=0.8)
        ds_detectado, ds_nivel_suelo, ds_neckline = detectar_doble_suelo(
            df, atr, lookback=_dt_lookback, tol_mult=0.8)

        if rotura_alcista:
            logger.info(f"  🚀 [1D] ROTURA ALCISTA detectada — cerró sobre resistencia ${zrh:.1f}")
        if rotura_bajista:
            logger.info(f"  📉 [1D] ROTURA BAJISTA detectada — cerró bajo soporte ${zsl:.1f}")
        if dt_detectado:
            logger.info(f"  🔻 [1D] DOBLE TECHO detectado — techo=${dt_nivel_techo:.1f} cuello=${dt_neckline:.1f}")
        if ds_detectado:
            logger.info(f"  🔺 [1D] DOBLE SUELO detectado — suelo=${ds_nivel_suelo:.1f} cuello=${ds_neckline:.1f}")

        score_sell = 0
        score_buy  = 0  # inicializar antes de ruptura/cuña para que += funcione
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
    
        # Nuevos puntos (indicadores alta prioridad)
        score_sell += 2 if bb_toca_superior        else 0  # Bollinger superior
        score_sell += 2 if evening_star            else 0  # Patrón Evening Star
        score_sell += 2 if macd_cruce_bajista      else 0  # MACD cruce
        score_sell += 2 if adx_bajista             else 0  # ADX confirma tendencia bajista
        score_sell += 1 if macd_divergencia_bajista else 0  # MACD divergencia
        score_sell += 1 if obv_divergencia_bajista else 0  # OBV divergencia
        score_sell += 1 if obv_decreciente         else 0  # OBV cayendo
        score_sell += 1 if macd_negativo           else 0  # MACD bajo cero
        score_sell += 4 if rotura_bajista          else 0  # rotura con impulso+volumen
        score_sell += 3 if dt_detectado            else 0  # doble techo confirmado

        # ── Ruptura horizontal directa (sin retest) 1D ─────────────────────
        _rup_sop_1d, _niv_sop_1d = detectar_ruptura_soporte_horizontal(
            df, atr, lookback=params.get('sr_lookback', 60), wing=3)
        _rup_res_1d, _niv_res_1d = detectar_ruptura_resistencia_horizontal(
            df, atr, lookback=params.get('sr_lookback', 60), wing=3)
        if _rup_sop_1d:
            score_sell += 4
            logger.info(f"  💥 [1D] RUPTURA SOPORTE ${_niv_sop_1d:.2f} — +4 pts SELL")
        if _rup_res_1d:
            score_buy += 4
            logger.info(f"  💥 [1D] RUPTURA RESISTENCIA ${_niv_res_1d:.2f} — +4 pts BUY")

        # ── Cuña descendente / ascendente (1D) ──────────────────────────────
        _lkb_cuña_1d = min(params.get('sr_lookback', 60), 60)
        _cuña_desc_1d, _t_desc_1d, _s_desc_1d = detectar_cuña_descendente(
            df, atr, lookback=_lkb_cuña_1d, wing=3, max_amplitud_pct=0.02)
        _cuña_asc_1d, _t_asc_1d, _s_asc_1d = detectar_cuña_ascendente(
            df, atr, lookback=_lkb_cuña_1d, wing=3, max_amplitud_pct=0.02)
        if _cuña_desc_1d == 'ruptura_alcista':
            score_buy += 7
            logger.info(f"  📐 [1D] CUÑA DESC ROTA AL ALZA (techo ${_t_desc_1d:.2f}) — +7 pts BUY")
        elif _cuña_desc_1d == 'ruptura_bajista':
            score_sell += 7
            logger.info(f"  📐 [1D] CUÑA DESC ROTA A LA BAJA (suelo ${_s_desc_1d:.2f}) — +7 pts SELL")
        elif _cuña_desc_1d == 'compresion':
            score_buy += 3
            logger.info(f"  📐 [1D] CUÑA DESC en compresión ${_s_desc_1d:.2f}-${_t_desc_1d:.2f} — +3 pts BUY")
        if _cuña_asc_1d == 'ruptura_bajista':
            score_sell += 7
            logger.info(f"  📐 [1D] CUÑA ASC ROTA A LA BAJA (suelo ${_s_asc_1d:.2f}) — +7 pts SELL")
        elif _cuña_asc_1d == 'compresion':
            score_sell += 3
            logger.info(f"  📐 [1D] CUÑA ASC en compresión ${_s_asc_1d:.2f}-${_t_asc_1d:.2f} — +3 pts SELL")

        # ADX lateral en zona S/R: no penalizar (consolidación = agotamiento, no debilidad)
        if adx_lateral and not en_zona_resist:
            score_sell = max(0, score_sell - 3)

        # ══════════════════════════════════
        # BLOQUE COMPRA
        # ══════════════════════════════════
        intento_caida_fallido = (low <= zsh) and (close > zsh)
        hammer            = is_bullish and lower_wick > body*2 and upper_wick < body*0.3 and en_zona_soporte
        bullish_engulfing = is_bullish and open_ <= prev['Low'] and close >= prev['High'] and en_zona_soporte
        bullish_marubozu  = is_bullish and body > total_range*0.8 and en_zona_soporte
        doji_soporte      = body < total_range*0.1 and en_zona_soporte and lower_wick > body*2
        vela_rebote       = hammer or bullish_engulfing or bullish_marubozu or doji_soporte

        rsi_bajo_girando = (rsi <= rsmb) and (rsi > rsi_prev)
        rsi_sobreventa   = rsi <= 30

        price_new_low       = low < df['Low'].iloc[-lookback-2:-2].min()
        rsi_higher_low      = rsi > df['rsi'].iloc[-lookback-2:-2].min()
        divergencia_alcista = price_new_low and rsi_higher_low and rsi < 50

        vol_alto_rebote      = vol > vol_avg * vm
        vol_decreciente_sell = (vol < prev['Volume']) and (prev['Volume'] < p2['Volume']) and is_bearish

        emas_alcistas      = ema_fast > ema_slow
        sobre_ema200       = close > ema_trend
        max_creciente      = (high > prev['High']) and (prev['High'] > p2['High'])
        min_creciente      = (low  > prev['Low'])  and (prev['Low']  > p2['Low'])
        estructura_alcista = max_creciente or min_creciente

        # ── Nuevas señales COMPRA (indicadores alta prioridad) ──
        # Bollinger Bands
        bb_toca_inferior = close <= bb_lower or low <= bb_lower
    
        # MACD
        macd_cruce_alcista = (macd > macd_signal) and (macd_hist > 0) and (macd_hist_prev <= 0)
        macd_divergencia_alcista = price_new_low and (macd > df['macd'].iloc[-lookback-2:-2].min())
        macd_positivo = macd > 0
    
        # ADX
        adx_alcista = (di_plus > di_minus) and adx_tendencia_fuerte
    
        # OBV
        obv_divergencia_alcista = price_new_low and (obv > df['obv'].iloc[-lookback-2:-2].min())
        obv_creciente = obv > obv_prev and obv > obv_ema
    
        # Morning Star
        morning_star = detectar_morning_star(df, len(df) - 2)

        # score_buy ya inicializado antes del bloque SELL
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
        score_buy += 1 if (hammer and vol_alto_rebote)              else 0
        score_buy += 1 if (divergencia_alcista and rsi_sobreventa)  else 0
        score_buy += 1 if sobre_ema200             else 0
    
        # Nuevos puntos (indicadores alta prioridad)
        score_buy += 2 if bb_toca_inferior        else 0  # Bollinger inferior
        score_buy += 2 if morning_star            else 0  # Patrón Morning Star
        score_buy += 2 if macd_cruce_alcista      else 0  # MACD cruce
        score_buy += 2 if adx_alcista             else 0  # ADX confirma tendencia alcista
        score_buy += 1 if macd_divergencia_alcista else 0  # MACD divergencia
        score_buy += 1 if obv_divergencia_alcista else 0  # OBV divergencia
        score_buy += 1 if obv_creciente           else 0  # OBV subiendo
        score_buy += 1 if macd_positivo           else 0  # MACD sobre cero
        score_buy += 4 if rotura_alcista          else 0  # rotura con impulso+volumen
        score_buy += 3 if ds_detectado            else 0  # doble suelo confirmado
        # (ruptura resistencia _rup_res_1d ya sumada antes del bloque COMPRA)

        # ADX lateral en zona S/R: consolidación = agotamiento, no penalizar
        if adx_lateral and not en_zona_soporte:
            score_buy = max(0, score_buy - 3)

        # ── FIBONACCI RETRACEMENT DINÁMICO (1D) ────────────────────────────────
        _fib_nivel_1d, _fib_precio_1d, _fib_tend_1d = detectar_precio_en_fibonacci(
            df, atr, lookback=60, tol_mult=0.6)
        if _fib_nivel_1d is not None:
            if _fib_tend_1d == 'bajista':
                _pts_fib_1d = 5 if _fib_nivel_1d in (0.382, 0.5) else 3
                score_sell += _pts_fib_1d
                logger.info(f"  🔢 [1D] Fib {_fib_nivel_1d} BAJISTA en ${_fib_precio_1d:.2f} — +{_pts_fib_1d} pts SELL")
            else:
                _pts_fib_1d = 5 if _fib_nivel_1d in (0.5, 0.618) else 3
                score_buy += _pts_fib_1d
                logger.info(f"  🔢 [1D] Fib {_fib_nivel_1d} ALCISTA en ${_fib_precio_1d:.2f} — +{_pts_fib_1d} pts BUY")

        _fib_data_1d = calcular_fibonacci(df, lookback=60)
        if _fib_data_1d:
            _n1d = _fib_data_1d['niveles']
            logger.info(
                f"  📐 [1D] Fib ({_fib_data_1d['tendencia']}) "
                f"0.382=${_n1d[0.382]:.0f} 0.5=${_n1d[0.5]:.0f} "
                f"0.618=${_n1d[0.618]:.0f} 0.786=${_n1d[0.786]:.0f}"
            )

        # ── REBOTE EN S/R O FIBONACCI (1D) ──────────────────────────────────────
        _niveles_fib_1d = _fib_data_1d.get('niveles', {}) if _fib_data_1d else {}
        _sop_fib_1d = [v for k, v in _niveles_fib_1d.items() if k in (0.5, 0.618, 0.786)]
        _res_fib_1d = [v for k, v in _niveles_fib_1d.items() if k in (0.236, 0.382, 0.5)]
        _zona_sop_1d = [zsl, zsh]
        _zona_res_1d = [zrl, zrh]
        _rsi_serie_1d = df['rsi']

        _rebote_baj_1d, _desc_baj_1d = detectar_rebote_bajista(
            df, atr, _rsi_serie_1d, _res_fib_1d + _zona_res_1d, tol_mult=0.7)
        _rebote_alc_1d, _desc_alc_1d = detectar_rebote_alcista(
            df, atr, _rsi_serie_1d, _sop_fib_1d + _zona_sop_1d, tol_mult=0.7)

        if _rebote_baj_1d:
            score_sell += 4
            logger.info(f"  🔄 [1D] REBOTE BAJISTA detectado ({_desc_baj_1d}) — +4 pts SELL")
        if _rebote_alc_1d:
            score_buy += 4
            logger.info(f"  🔄 [1D] REBOTE ALCISTA detectado ({_desc_alc_1d}) — +4 pts BUY")

        # ── Ajuste por sesgo DXY (correlación inversa Gold/USD) ──
        dxy_bias = get_dxy_bias()
        score_buy, score_sell = ajustar_score_por_dxy(score_buy, score_sell, dxy_bias)

        # ── Ajuste por COT Report (posiciones institucionales semanales) ──
        _cot_bias, _cot_ratio = get_cot_bias()
        score_buy, score_sell = ajustar_score_por_cot(score_buy, score_sell, _cot_bias)

        # ── Ajuste por Open Interest / Volumen (fuerza de tendencia) ──
        _oi_bias = get_oi_bias()
        score_buy, score_sell = ajustar_score_por_oi(score_buy, score_sell, _oi_bias)

        # ── Filtro de volumen: penalizar señales en velas de bajo volumen ──
        score_sell, score_buy, _vol_bajo = self.ajustar_scores_por_volumen(
            score_sell, score_buy, vol, vol_avg, params['vol_mult'])
        if _vol_bajo:
            logger.info(f"  ⚠️ [1D] Volumen bajo ({vol:.0f} < {vol_avg * params['vol_mult']:.0f}) — scores penalizados -3")

        # ── Umbral adaptativo: elevar umbrales si ATR está en alta volatilidad ──
        atr_media = float(df['atr'].rolling(20).mean().iloc[-2])

        # ══════════════════════════════════
        # ANÁLISIS DE SENTIMIENTO DEL MERCADO
        # (Validación cruzada para señales de alta calidad)
        # ══════════════════════════════════
        sentimiento_bajista_score, factores_bajistas = calcular_sentimiento_bajista(row, prev, p2, df, params)
        sentimiento_alcista_score, factores_alcistas = calcular_sentimiento_alcista(row, prev, p2, df, params)
    
        # Determinar sentimiento dominante
        if sentimiento_bajista_score >= 6:
            sentimiento_general = "🔴 BAJISTA FUERTE"
            emoji_sentimiento = "🔴"
        elif sentimiento_alcista_score >= 6:
            sentimiento_general = "🟢 ALCISTA FUERTE"
            emoji_sentimiento = "🟢"
        elif sentimiento_bajista_score >= 4:
            sentimiento_general = "⚠️ BAJISTA MODERADO"
            emoji_sentimiento = "🟠"
        elif sentimiento_alcista_score >= 4:
            sentimiento_general = "⚠️ ALCISTA MODERADO"
            emoji_sentimiento = "🟡"
        else:
            sentimiento_general = "⚪ NEUTRAL/MIXTO"
            emoji_sentimiento = "⚪"
    
        # ══════════════════════════════════
        # SISTEMA DE CONFLUENCIA
        # Solo señales con confirmación del sentimiento
        # ══════════════════════════════════
    
        # Validar confluencia (señal más fiable si sentimiento la apoya)
        confluencia_sell = sentimiento_bajista_score >= 4 and score_sell >= 6
        confluencia_buy = sentimiento_alcista_score >= 4 and score_buy >= 6
    
        # Señal contradictoria (advertencia - calidad reducida)
        senal_contradictoria_sell = score_sell >= 6 and sentimiento_alcista_score > sentimiento_bajista_score
        senal_contradictoria_buy = score_buy >= 6 and sentimiento_bajista_score > sentimiento_alcista_score
    
        # ══════════════════════════════════
        # NIVELES DE SEÑAL CON FILTRO DE CALIDAD
        # ══════════════════════════════════
    
        # SELL - Solo con confluencia o muy alto score técnico
        _umbral_max = self.umbral_adaptativo(10, atr, atr_media)
        _umbral_fue = self.umbral_adaptativo(8,  atr, atr_media)
        _umbral_med = self.umbral_adaptativo(6,  atr, atr_media)
        _umbral_ale = self.umbral_adaptativo(6,  atr, atr_media)
        senal_sell_maxima = score_sell >= _umbral_max and sentimiento_bajista_score >= 6
        senal_sell_fuerte = score_sell >= _umbral_fue and sentimiento_bajista_score >= 4
        senal_sell_media  = score_sell >= _umbral_med and sentimiento_bajista_score >= 3
        # ALERTA: Score técnico ≥umbral normal, O sentimiento FUERTE (≥6) con score mínimo ≥3
        senal_sell_alerta = (score_sell >= _umbral_ale and not senal_contradictoria_sell) or (sentimiento_bajista_score >= 6 and score_sell >= 3)
    
        # BUY - Solo con confluencia o muy alto score técnico
        senal_buy_maxima  = score_buy >= _umbral_max and sentimiento_alcista_score >= 6
        senal_buy_fuerte  = score_buy >= _umbral_fue and sentimiento_alcista_score >= 4
        senal_buy_media   = score_buy >= _umbral_med and sentimiento_alcista_score >= 3
        # ALERTA: Score técnico ≥umbral normal, O sentimiento FUERTE (≥6) con score mínimo ≥3
        senal_buy_alerta  = (score_buy >= _umbral_ale and not senal_contradictoria_buy) or (sentimiento_alcista_score >= 6 and score_buy >= 3)

        # ── Filtro de sesión 1D: fuera de 08-21 UTC bloquear ALERTA (MEDIA+ pasa) ──
        if not self.en_sesion_optima():
            if senal_sell_alerta and not senal_sell_media:
                logger.info(f"  🌙 [1D] Fuera sesión óptima — SELL ALERTA suprimida")
            if senal_buy_alerta and not senal_buy_media:
                logger.info(f"  🌙 [1D] Fuera sesión óptima — BUY ALERTA suprimida")
            senal_sell_alerta, senal_sell_media, senal_sell_fuerte, senal_sell_maxima = \
                self.umbral_activo_por_sesion(senal_sell_alerta, senal_sell_media, senal_sell_fuerte, senal_sell_maxima, tf_corto=False)
            senal_buy_alerta, senal_buy_media, senal_buy_fuerte, senal_buy_maxima = \
                self.umbral_activo_por_sesion(senal_buy_alerta, senal_buy_media, senal_buy_fuerte, senal_buy_maxima, tf_corto=False)

        # ══════════════════════════════════
        # SL Y TP
        # ══════════════════════════════════
        sl_venta  = round(sell_limit + atr * asm, 2)
        sl_compra = round(buy_limit  - atr * asm, 2)

        # TPs dinámicos basados en ATR (se adaptan automáticamente al rango de precio)
        tp1_v = round(sell_limit - atr * params['atr_tp1_mult'], 2)
        tp2_v = round(sell_limit - atr * params['atr_tp2_mult'], 2)
        tp3_v = round(sell_limit - atr * params['atr_tp3_mult'], 2)
        tp1_c = round(buy_limit  + atr * params['atr_tp1_mult'], 2)
        tp2_c = round(buy_limit  + atr * params['atr_tp2_mult'], 2)
        tp3_c = round(buy_limit  + atr * params['atr_tp3_mult'], 2)

        def rr(limit, sl, tp):
            return round(abs(tp - limit) / abs(sl - limit), 1) if abs(sl - limit) > 0 else 0

        # ── FILTRO R:R MÍNIMO 1.2 ──
        rr_sell_tp1 = rr(sell_limit, sl_venta, tp1_v)
        rr_buy_tp1  = rr(buy_limit,  sl_compra, tp1_c)
        if rr_sell_tp1 < 1.2:
            logger.warning(f"  ⛔ SELL bloqueada: R:R TP1={rr_sell_tp1} < 1.2")
        if rr_buy_tp1 < 1.2:
            logger.warning(f"  ⛔ BUY bloqueada: R:R TP1={rr_buy_tp1} < 1.2")

        # ══════════════════════════════════
        # LOG EN CONSOLA
        # ══════════════════════════════════
        fecha = df.index[-2].strftime('%Y-%m-%d')    
        # ══════════════════════════════════════
        # VERIFICAR SI YA SE ANALIZÓ ESTA VELA
        # ══════════════════════════════════════
        clave_simbolo = simbolo
        ya_analizado = False
    
        if clave_simbolo in self.ultimo_analisis:
            ultima_fecha = self.ultimo_analisis[clave_simbolo]['fecha']
            ultimo_score_sell = self.ultimo_analisis[clave_simbolo]['score_sell']
            ultimo_score_buy = self.ultimo_analisis[clave_simbolo]['score_buy']
        
            # Si es la misma fecha y scores similares, ya fue analizado
            if (ultima_fecha == fecha and 
                abs(ultimo_score_sell - score_sell) <= 1 and 
                abs(ultimo_score_buy - score_buy) <= 1):
                ya_analizado = True
                logger.info(f"  ℹ️  Vela {fecha} ya analizada - Sin cambios significativos")
                return  # No enviar alertas repetidas
    
        # Actualizar último análisis
        self.ultimo_analisis[clave_simbolo] = {
            'fecha': fecha,
            'score_sell': score_sell,
            'score_buy': score_buy
        }
    
        logger.info(f"  📅 Vela: {fecha}")
        logger.info(f"  💰 Precio cierre: {round(close, 2)}")
        logger.info(f"  📊 Score SELL: {score_sell}/21 | Score BUY: {score_buy}/21")
        logger.info(f"  {emoji_sentimiento} Sentimiento: {sentimiento_general} (Bajista:{sentimiento_bajista_score}/10 | Alcista:{sentimiento_alcista_score}/10)")
        logger.info(f"  🔴 SELL → Alerta:{senal_sell_alerta} Media:{senal_sell_media} Fuerte:{senal_sell_fuerte} Máxima:{senal_sell_maxima}")
        logger.info(f"  🟢 BUY  → Alerta:{senal_buy_alerta}  Media:{senal_buy_media}  Fuerte:{senal_buy_fuerte}  Máxima:{senal_buy_maxima}")
        if senal_contradictoria_sell:
            logger.warning(f"  ⚠️  ADVERTENCIA: Señal SELL contradice sentimiento alcista")
        if senal_contradictoria_buy:
            logger.warning(f"  ⚠️  ADVERTENCIA: Señal BUY contradice sentimiento bajista")
        # Informar cuando se activa por sentimiento dominante
        if senal_sell_alerta and score_sell < 6 and sentimiento_bajista_score >= 6:
            logger.info(f"  📣 ALERTA SELL activada por SENTIMIENTO BAJISTA FUERTE ({sentimiento_bajista_score}/10) - Score técnico bajo ({score_sell}/21)")
        if senal_buy_alerta and score_buy < 6 and sentimiento_alcista_score >= 6:
            logger.info(f"  📣 ALERTA BUY activada por SENTIMIENTO ALCISTA FUERTE ({sentimiento_alcista_score}/10) - Score técnico bajo ({score_buy}/21)")

        # ══════════════════════════════════
        # CONTROL ANTI-SPAM
        # No enviar la misma señal dos veces
        # en la misma vela
        # ══════════════════════════════════
        clave_vela = f"{simbolo}_{fecha}"



        # ── FILTRO PROXIMIDAD: solo operar cerca de zona ──
        cerca_resistencia = en_zona_resist or aproximando_resistencia
        cerca_soporte     = en_zona_soporte or aproximando_soporte
        if not cerca_resistencia:
            if senal_sell_alerta: logger.info(f"  ⏳ SELL ignorada: precio lejos de resistencia")
            senal_sell_maxima = senal_sell_fuerte = senal_sell_media = senal_sell_alerta = False
        if not cerca_soporte:
            if senal_buy_alerta: logger.info(f"  ⏳ BUY ignorada: precio lejos de soporte")
            senal_buy_maxima = senal_buy_fuerte = senal_buy_media = senal_buy_alerta = False

        # ── EXCLUSIÓN MUTUA: una sola dirección por vela ──
        if senal_sell_alerta and senal_buy_alerta:
            if score_sell >= score_buy:
                senal_buy_alerta = False
                logger.info(f"  ⚖️ Exclusión mutua: BUY suprimida (SELL {score_sell} >= BUY {score_buy})")
            else:
                senal_sell_alerta = False
                logger.info(f"  ⚖️ Exclusión mutua: SELL suprimida (BUY {score_buy} > SELL {score_sell})")

        # ── PUBLICAR SESGO MULTI-TF (GOLD 1D) ──
        _sesgo_dir = tf_bias.BIAS_BEARISH if score_sell > score_buy else tf_bias.BIAS_BULLISH if score_buy > score_sell else tf_bias.BIAS_NEUTRAL
        tf_bias.publicar_sesgo(simbolo, '1D', _sesgo_dir, max(score_sell, score_buy))
        logger.info(f"  📡 Sesgo GOLD 1D publicado: {_sesgo_dir} (sell={score_sell} buy={score_buy})")

        # ── CANAL ROTO 1D — publicar para uso en detectores inferiores ───────────
        _cr_1d_alc, _cr_1d_baj, _ls_1d, _lr_1d = detectar_canal_roto(df, atr, lookback=60)
        tf_bias.publicar_canal_1d(simbolo, _cr_1d_alc, _cr_1d_baj, _ls_1d, _lr_1d)
        if _cr_1d_alc:
            logger.info(f"  🔻 [1D] Canal alcista ROTO — soporte ${_ls_1d:.2f}")
        if _cr_1d_baj:
            logger.info(f"  🔺 [1D] Canal bajista ROTO — resist ${_lr_1d:.2f}")

        # ── CANAL ROTO 1W — descargar datos semanales y publicar ─────────────────
        try:
            df_1w, _ = get_ohlcv(params['ticker_yf'], period='5y', interval='1wk')
            if not df_1w.empty and len(df_1w) >= 20:
                if isinstance(df_1w.columns, __import__('pandas').MultiIndex):
                    df_1w.columns = df_1w.columns.get_level_values(0)
                df_1w = df_1w.copy()
                from core.indicators import calcular_atr as _catr
                _atr_1w = float(_catr(df_1w, 14).iloc[-2])
                _cr_1w_alc, _cr_1w_baj, _ls_1w, _lr_1w = detectar_canal_roto(
                    df_1w, _atr_1w, lookback=52)  # ~1 año de velas semanales
                tf_bias.publicar_canal_1w(simbolo, _cr_1w_alc, _cr_1w_baj, _ls_1w, _lr_1w)
                if _cr_1w_alc:
                    logger.info(f"  🔻 [1W] Canal alcista ROTO — soporte ${_ls_1w:.2f}")
                if _cr_1w_baj:
                    logger.info(f"  🔺 [1W] Canal bajista ROTO — resist ${_lr_1w:.2f}")
        except Exception as _e:
            logger.warning(f"  ⚠️ [1D] No se pudo detectar canal 1W: {_e}")

        # ── APROXIMACIÓN RESISTENCIA — SEÑAL ACCIONABLE (pon la orden limit ahora) ──
        if aproximando_resistencia and not en_zona_resist and not cancelar_sell and not senal_buy_alerta:
            if not self.ya_enviada(f"{clave_vela}_PREP_SELL"):
                nv = ("🔥 SELL MÁXIMA" if senal_sell_maxima else
                      "🔴 SELL FUERTE" if senal_sell_fuerte else
                      "⚡ SELL MEDIA"  if senal_sell_media  else
                      "👀 SELL ALERTA")
                msg = (f"⚡ <b>SEÑAL SELL — ORO 1D</b> | PON ORDEN LIMIT AHORA\n"
                       f"━━━━━━━━━━━━━━━━━━━━\n"
                       f"📊 <b>Nivel:</b> {nv}\n"
                       f"💰 <b>Precio actual:</b> {round(close, 2)}\n"
                       f"📌 <b>SELL LIMIT:</b>   {round(sell_limit, 2)}  ← pon la orden aquí\n"
                       f"🛑 <b>Stop Loss:</b>   {round(sl_venta, 2)}\n"
                       f"━━━━━━━━━━━━━━━━━━━━\n"
                       f"🎯 <b>TP1:</b> {tp1_v}  R:R {rr(sell_limit, sl_venta, tp1_v)}:1\n"
                       f"🎯 <b>TP2:</b> {tp2_v}  R:R {rr(sell_limit, sl_venta, tp2_v)}:1\n"
                       f"🎯 <b>TP3:</b> {tp3_v}  R:R {rr(sell_limit, sl_venta, tp3_v)}:1\n"
                       f"━━━━━━━━━━━━━━━━━━━━\n"
                       f"📊 <b>Score:</b> {score_sell}/21  📉 <b>RSI:</b> {round(rsi, 1)}  📐 <b>ADX:</b> {round(adx, 1)}\n"
                       f"⏱️ <b>TF:</b> 1D  📅 {fecha}")
                simbolo_db = f"{simbolo}_1D"
                if self.db and not self.db.existe_senal_reciente(simbolo_db, "VENTA", horas=4):
                    try:
                        self._guardar_senal({
                            'timestamp': datetime.now(timezone.utc), 'simbolo': simbolo_db,
                            'direccion': 'VENTA', 'precio_entrada': sell_limit,
                            'tp1': tp1_v, 'tp2': tp2_v, 'tp3': tp3_v, 'sl': sl_venta,
                            'score': score_sell,
                            'indicadores': json.dumps({'rsi': round(rsi, 1), 'adx': round(adx, 1), 'atr': round(atr, 2)}),
                            'patron_velas': f"Evening Star:{evening_star}, Shooting Star:{shooting_star}",
                            'version_detector': 'GOLD 1D-v2.0'
                        })
                    except Exception as e:
                        logger.error(f"  ⚠️ Error BD: {e}")
                self.enviar(msg)
                self.marcar_enviada(f"{clave_vela}_PREP_SELL")

        # ── APROXIMACIÓN SOPORTE — SEÑAL ACCIONABLE (pon la orden limit ahora) ──
        if aproximando_soporte and not en_zona_soporte and not cancelar_buy and not senal_sell_alerta:
            if not self.ya_enviada(f"{clave_vela}_PREP_BUY"):
                nv = ("🔥 BUY MÁXIMA" if senal_buy_maxima else
                      "🟢 BUY FUERTE" if senal_buy_fuerte else
                      "⚡ BUY MEDIA"  if senal_buy_media  else
                      "👀 BUY ALERTA")
                msg = (f"⚡ <b>SEÑAL BUY — ORO 1D</b> | PON ORDEN LIMIT AHORA\n"
                       f"━━━━━━━━━━━━━━━━━━━━\n"
                       f"📊 <b>Nivel:</b> {nv}\n"
                       f"💰 <b>Precio actual:</b> {round(close, 2)}\n"
                       f"📌 <b>BUY LIMIT:</b>    {round(buy_limit, 2)}  ← pon la orden aquí\n"
                       f"🛑 <b>Stop Loss:</b>   {round(sl_compra, 2)}\n"
                       f"━━━━━━━━━━━━━━━━━━━━\n"
                       f"🎯 <b>TP1:</b> {tp1_c}  R:R {rr(buy_limit, sl_compra, tp1_c)}:1\n"
                       f"🎯 <b>TP2:</b> {tp2_c}  R:R {rr(buy_limit, sl_compra, tp2_c)}:1\n"
                       f"🎯 <b>TP3:</b> {tp3_c}  R:R {rr(buy_limit, sl_compra, tp3_c)}:1\n"
                       f"━━━━━━━━━━━━━━━━━━━━\n"
                       f"📊 <b>Score:</b> {score_buy}/21  📉 <b>RSI:</b> {round(rsi, 1)}  📐 <b>ADX:</b> {round(adx, 1)}\n"
                       f"⏱️ <b>TF:</b> 1D  📅 {fecha}")
                simbolo_db = f"{simbolo}_1D"
                if self.db and not self.db.existe_senal_reciente(simbolo_db, "COMPRA", horas=4):
                    try:
                        self._guardar_senal({
                            'timestamp': datetime.now(timezone.utc), 'simbolo': simbolo_db,
                            'direccion': 'COMPRA', 'precio_entrada': buy_limit,
                            'tp1': tp1_c, 'tp2': tp2_c, 'tp3': tp3_c, 'sl': sl_compra,
                            'score': score_buy,
                            'indicadores': json.dumps({'rsi': round(rsi, 1), 'adx': round(adx, 1), 'atr': round(atr, 2)}),
                            'patron_velas': f"Morning Star:{morning_star}, Hammer:{hammer}",
                            'version_detector': 'GOLD 1D-v2.0'
                        })
                    except Exception as e:
                        logger.error(f"  ⚠️ Error BD: {e}")
                self.enviar(msg)
                self.marcar_enviada(f"{clave_vela}_PREP_BUY")

        # ── SEÑALES VENTA (en zona) — confirmación si ya se mandó pre-alerta ──
        if senal_sell_alerta and not cancelar_sell and rr_sell_tp1 >= 1.2:
            # Si ya avisamos antes de zona (PREP_SELL), solo confirmar si es FUERTE o MÁXIMA
            if self.ya_enviada(f"{clave_vela}_PREP_SELL") and not (senal_sell_fuerte or senal_sell_maxima):
                logger.info(f"  ℹ️  SELL ALERTA/MEDIA ignorada: pre-alerta ya enviada")
            else:
                if senal_sell_maxima:
                    nivel = "🔥 SELL MÁXIMA - CONFLUENCIA CONFIRMADA 🔥"
                    calidad = "✅ ALTA CALIDAD"
                elif senal_sell_fuerte:
                    nivel = "🔴 SELL FUERTE"
                    calidad = "✅ BUENA CALIDAD" if confluencia_sell else "⚠️ CALIDAD MEDIA"
                elif senal_sell_media:
                    nivel = "⚠️ SELL MEDIA"
                    calidad = "⚠️ PRECAUCIÓN" if not senal_contradictoria_sell else "❌ SEÑAL DUDOSA"
                else:
                    nivel = "👀 SELL ALERTA"
                    calidad = "ℹ️ MONITOREAR"

                tipo_clave = ("SELL_MAX" if senal_sell_maxima else
                              "SELL_FUE" if senal_sell_fuerte else
                              "SELL_MED" if senal_sell_media  else
                              "SELL_ALE")

                simbolo_db = f"{simbolo}_1D"
                if not self.ya_enviada(f"{clave_vela}_{tipo_clave}"):
                    if self.db and self.db.existe_senal_reciente(simbolo_db, "VENTA", horas=4):
                        logger.info(f"  ℹ️  Señal VENTA duplicada - No se guarda")
                        return
                    # Si ya hay pre-alerta → confirmar brevemente sin guardar otra vez en BD
                    if self.ya_enviada(f"{clave_vela}_PREP_SELL"):
                        msg = (f"✅ <b>CONFIRMACIÓN SELL — ORO 1D</b>\n"
                               f"━━━━━━━━━━━━━━━━━━━━\n"
                               f"{nivel} — precio ahora en zona\n"
                               f"💰 <b>Precio:</b> {round(close, 2)}\n"
                               f"📊 <b>Score:</b> {score_sell}/21  {calidad}\n"
                               f"📉 <b>RSI:</b> {round(rsi, 1)}  📐 <b>ADX:</b> {round(adx, 1)}\n"
                               f"⏱️ <b>TF:</b> 1D  📅 {fecha}")
                    else:
                        msg = (f"{nivel}\n"
                               f"━━━━━━━━━━━━━━━━━━━━\n"
                               f"📈 <b>Símbolo:</b>    {simbolo}\n"
                               f"💰 <b>Precio:</b>     {round(close, 2)}\n"
                               f"📌 <b>SELL LIMIT:</b> {round(sell_limit, 2)}\n"
                               f"🛑 <b>Stop Loss:</b>  {round(sl_venta, 2)}\n"
                               f"━━━━━━━━━━━━━━━━━━━━\n"
                               f"🎯 <b>TP1:</b> {tp1_v}  R:R {rr(sell_limit, sl_venta, tp1_v)}:1\n"
                               f"🎯 <b>TP2:</b> {tp2_v}  R:R {rr(sell_limit, sl_venta, tp2_v)}:1\n"
                               f"🎯 <b>TP3:</b> {tp3_v}  R:R {rr(sell_limit, sl_venta, tp3_v)}:1\n"
                               f"━━━━━━━━━━━━━━━━━━━━\n"
                               f"📊 <b>Score técnico:</b> {score_sell}/21\n"
                               f"{emoji_sentimiento} <b>Sentimiento:</b> {sentimiento_general} ({sentimiento_bajista_score}/10)\n"
                               f"🎯 <b>Calidad:</b> {calidad}\n"
                               f"📉 <b>RSI:</b> {round(rsi, 1)}  📐 <b>ADX:</b> {round(adx, 1)}\n"
                               f"⏱️ <b>TF:</b> 1D  📅 {fecha}")
                        if self.db:
                            try:
                                self._guardar_senal({
                                    'timestamp': datetime.now(timezone.utc),
                                    'simbolo': simbolo_db, 'direccion': 'VENTA',
                                    'precio_entrada': sell_limit,
                                    'tp1': tp1_v, 'tp2': tp2_v, 'tp3': tp3_v, 'sl': sl_venta,
                                    'score': score_sell,
                                    'indicadores': json.dumps({'rsi': round(rsi, 1), 'adx': round(adx, 1),
                                                               'ema_fast': round(ema_fast, 2), 'atr': round(atr, 2)}),
                                    'patron_velas': f"Evening Star:{evening_star}, Shooting Star:{shooting_star}",
                                    'version_detector': 'GOLD 1D-v2.0'
                                })
                            except Exception as e:
                                logger.error(f"  ⚠️ Error guardando en BD: {e}")
                    self.enviar(msg)
                    self.marcar_enviada(f"{clave_vela}_{tipo_clave}")

        # ── SEÑALES COMPRA (en zona) — confirmación si ya se mandó pre-alerta ──
        if senal_buy_alerta and not cancelar_buy and rr_buy_tp1 >= 1.2:
            if self.ya_enviada(f"{clave_vela}_PREP_BUY") and not (senal_buy_fuerte or senal_buy_maxima):
                logger.info(f"  ℹ️  BUY ALERTA/MEDIA ignorada: pre-alerta ya enviada")
            else:
                if senal_buy_maxima:
                    nivel = "🔥 BUY MÁXIMA - CONFLUENCIA CONFIRMADA 🔥"
                    calidad = "✅ ALTA CALIDAD"
                elif senal_buy_fuerte:
                    nivel = "🟢 BUY FUERTE"
                    calidad = "✅ BUENA CALIDAD" if confluencia_buy else "⚠️ CALIDAD MEDIA"
                elif senal_buy_media:
                    nivel = "⚠️ BUY MEDIA"
                    calidad = "⚠️ PRECAUCIÓN" if not senal_contradictoria_buy else "❌ SEÑAL DUDOSA"
                else:
                    nivel = "👀 BUY ALERTA"
                    calidad = "ℹ️ MONITOREAR"

                tipo_clave = ("BUY_MAX" if senal_buy_maxima else
                              "BUY_FUE" if senal_buy_fuerte else
                              "BUY_MED" if senal_buy_media  else
                              "BUY_ALE")

                simbolo_db = f"{simbolo}_1D"
                if not self.ya_enviada(f"{clave_vela}_{tipo_clave}"):
                    if self.db and self.db.existe_senal_reciente(simbolo_db, "COMPRA", horas=4):
                        logger.info(f"  ℹ️  Señal COMPRA duplicada - No se guarda")
                        return
                    if self.ya_enviada(f"{clave_vela}_PREP_BUY"):
                        msg = (f"✅ <b>CONFIRMACIÓN BUY — ORO 1D</b>\n"
                               f"━━━━━━━━━━━━━━━━━━━━\n"
                               f"{nivel} — precio ahora en zona\n"
                               f"💰 <b>Precio:</b> {round(close, 2)}\n"
                               f"📊 <b>Score:</b> {score_buy}/21  {calidad}\n"
                               f"📉 <b>RSI:</b> {round(rsi, 1)}  📐 <b>ADX:</b> {round(adx, 1)}\n"
                               f"⏱️ <b>TF:</b> 1D  📅 {fecha}")
                    else:
                        msg = (f"{nivel}\n"
                               f"━━━━━━━━━━━━━━━━━━━━\n"
                               f"📈 <b>Símbolo:</b>   {simbolo}\n"
                               f"💰 <b>Precio:</b>    {round(close, 2)}\n"
                               f"📌 <b>BUY LIMIT:</b> {round(buy_limit, 2)}\n"
                               f"🛑 <b>Stop Loss:</b> {round(sl_compra, 2)}\n"
                               f"━━━━━━━━━━━━━━━━━━━━\n"
                               f"🎯 <b>TP1:</b> {tp1_c}  R:R {rr(buy_limit, sl_compra, tp1_c)}:1\n"
                               f"🎯 <b>TP2:</b> {tp2_c}  R:R {rr(buy_limit, sl_compra, tp2_c)}:1\n"
                               f"🎯 <b>TP3:</b> {tp3_c}  R:R {rr(buy_limit, sl_compra, tp3_c)}:1\n"
                               f"━━━━━━━━━━━━━━━━━━━━\n"
                               f"📊 <b>Score técnico:</b> {score_buy}/21\n"
                               f"{emoji_sentimiento} <b>Sentimiento:</b> {sentimiento_general} ({sentimiento_alcista_score}/10)\n"
                               f"🎯 <b>Calidad:</b> {calidad}\n"
                               f"📉 <b>RSI:</b> {round(rsi, 1)}  📐 <b>ADX:</b> {round(adx, 1)}\n"
                               f"⏱️ <b>TF:</b> 1D  📅 {fecha}")
                        if self.db:
                            try:
                                self._guardar_senal({
                                    'timestamp': datetime.now(timezone.utc),
                                    'simbolo': simbolo_db, 'direccion': 'COMPRA',
                                    'precio_entrada': buy_limit,
                                    'tp1': tp1_c, 'tp2': tp2_c, 'tp3': tp3_c, 'sl': sl_compra,
                                    'score': score_buy,
                                    'indicadores': json.dumps({'rsi': round(rsi, 1), 'adx': round(adx, 1),
                                                               'ema_fast': round(ema_fast, 2), 'atr': round(atr, 2)}),
                                    'patron_velas': f"Morning Star:{morning_star}, Hammer:{hammer}",
                                    'version_detector': 'GOLD 1D-v2.0'
                                })
                            except Exception as e:
                                logger.error(f"  ⚠️ Error guardando en BD: {e}")
                    self.enviar(msg)
                    self.marcar_enviada(f"{clave_vela}_{tipo_clave}")

        # ── CANCELACIONES ──
        if cancelar_sell and not self.ya_enviada(f"{clave_vela}_CANCEL_SELL"):
            msg = (f"❌ <b>CANCELAR SELL LIMIT — ORO (XAUUSD) 1D</b> ❌\n"
                   f"━━━━━━━━━━━━━━━━━━━━\n"
                   f"📌 <b>Orden prevista:</b> SELL LIMIT ${sell_limit:.2f}\n"
                   f"💰 <b>Precio actual:</b>  ${close:.2f}\n"
                   f"⚠️ <b>Motivo:</b> Precio rompió la resistencia (${zrh:.2f}) hacia arriba\n"
                   f"🚫 La entrada ya no es válida — cancela la orden limit\n"
                   f"━━━━━━━━━━━━━━━━━━━━\n"
                   f"📅 {fecha}")
            if self.db:
                try:
                    self.db.cancelar_senales_pendientes(simbolo_db, "VENTA")
                except Exception as e:
                    logger.error(f"  ⚠️ Error BD al cancelar VENTA: {e}")
            self.enviar(msg)
            self.marcar_enviada(f"{clave_vela}_CANCEL_SELL")

        if cancelar_buy and not self.ya_enviada(f"{clave_vela}_CANCEL_BUY"):
            msg = (f"❌ <b>CANCELAR BUY LIMIT — ORO (XAUUSD) 1D</b> ❌\n"
                   f"━━━━━━━━━━━━━━━━━━━━\n"
                   f"📌 <b>Orden prevista:</b> BUY LIMIT ${buy_limit:.2f}\n"
                   f"💰 <b>Precio actual:</b>  ${close:.2f}\n"
                   f"⚠️ <b>Motivo:</b> Precio perforó el soporte (${zsl:.2f}) hacia abajo\n"
                   f"🚫 La entrada ya no es válida — cancela la orden limit\n"
                   f"━━━━━━━━━━━━━━━━━━━━\n"
                   f"📅 {fecha}")
            if self.db:
                try:
                    self.db.cancelar_senales_pendientes(simbolo_db, "COMPRA")
                except Exception as e:
                    logger.error(f"  ⚠️ Error BD al cancelar COMPRA: {e}")
            self.enviar(msg)
            self.marcar_enviada(f"{clave_vela}_CANCEL_BUY")

        # ══════════════════════════════════════════════════════════
        # SEÑALES DE ROTURA DE NIVEL (BREAKOUT) — entrada a mercado
        # ══════════════════════════════════════════════════════════

        # ── Rotura alcista ──
        if rotura_alcista and not self.ya_enviada(f"{clave_vela}_BREAK_BUY"):
            sl_break_buy  = round(zrh - atr * 0.5, 2)
            tp1_break_buy = round(close + atr * params['atr_tp1_mult'], 2)
            tp2_break_buy = round(close + atr * params['atr_tp2_mult'], 2)
            tp3_break_buy = round(close + atr * params['atr_tp3_mult'], 2)
            rr_b1 = rr(close, sl_break_buy, tp1_break_buy)
            if rr_b1 >= 1.2:
                nivel_break = ("🔥 ROTURA MÁXIMA" if score_buy >= 10 else
                               "🟢 ROTURA FUERTE" if score_buy >= 7  else
                               "⚡ ROTURA MEDIA")
                msg = (f"🚀 <b>ROTURA ALCISTA — ORO (XAUUSD) 1D</b>\n"
                       f"━━━━━━━━━━━━━━━━━━━━\n"
                       f"📊 <b>Nivel:</b> {nivel_break}\n"
                       f"💰 <b>Precio actual:</b>  ${round(close, 2)}\n"
                       f"📌 <b>COMPRA a MERCADO</b>  ← entrada inmediata\n"
                       f"🔓 <b>Resistencia rota:</b> ${round(zrh, 2)} (ahora soporte)\n"
                       f"🛑 <b>Stop Loss:</b>        ${sl_break_buy}  (-${round(close - sl_break_buy, 2)})\n"
                       f"━━━━━━━━━━━━━━━━━━━━\n"
                       f"🎯 <b>TP1:</b> ${tp1_break_buy}  R:R {rr_b1}:1\n"
                       f"🎯 <b>TP2:</b> ${tp2_break_buy}  R:R {rr(close, sl_break_buy, tp2_break_buy)}:1\n"
                       f"🎯 <b>TP3:</b> ${tp3_break_buy}  R:R {rr(close, sl_break_buy, tp3_break_buy)}:1\n"
                       f"━━━━━━━━━━━━━━━━━━━━\n"
                       f"📊 <b>Score:</b> {score_buy}/21  📉 <b>RSI:</b> {round(rsi, 1)}  📐 <b>ADX:</b> {round(adx, 1)}\n"
                       f"⏱️ <b>TF:</b> 1D  📅 {fecha}  🔒 SWING")
                if self.db and not self.db.existe_senal_reciente(f"{simbolo}_1D", "COMPRA", horas=4):
                    try:
                        self._guardar_senal({
                            'timestamp': datetime.now(timezone.utc), 'simbolo': f"{simbolo}_1D",
                            'direccion': 'COMPRA', 'precio_entrada': close,
                            'tp1': tp1_break_buy, 'tp2': tp2_break_buy, 'tp3': tp3_break_buy,
                            'sl': sl_break_buy, 'score': score_buy,
                            'indicadores': json.dumps({'rsi': round(rsi, 1), 'adx': round(adx, 2),
                                                       'macd': round(macd, 2), 'atr': round(atr, 2)}),
                            'patron_velas': f"Rotura Alcista: resistencia_rota={round(zrh, 2)}",
                            'version_detector': 'GOLD 1D-v2.0'
                        })
                    except Exception as e:
                        logger.error(f"  ⚠️ Error BD: {e}")
                self.enviar(msg); self.marcar_enviada(f"{clave_vela}_BREAK_BUY")

        # ── Rotura bajista ──
        if rotura_bajista and not self.ya_enviada(f"{clave_vela}_BREAK_SELL"):
            sl_break_sell  = round(zsl + atr * 0.5, 2)
            tp1_break_sell = round(close - atr * params['atr_tp1_mult'], 2)
            tp2_break_sell = round(close - atr * params['atr_tp2_mult'], 2)
            tp3_break_sell = round(close - atr * params['atr_tp3_mult'], 2)
            rr_bs1 = rr(close, sl_break_sell, tp1_break_sell)
            if rr_bs1 >= 1.2:
                nivel_break = ("🔥 ROTURA MÁXIMA" if score_sell >= 10 else
                               "🔴 ROTURA FUERTE" if score_sell >= 7  else
                               "⚡ ROTURA MEDIA")
                msg = (f"📉 <b>ROTURA BAJISTA — ORO (XAUUSD) 1D</b>\n"
                       f"━━━━━━━━━━━━━━━━━━━━\n"
                       f"📊 <b>Nivel:</b> {nivel_break}\n"
                       f"💰 <b>Precio actual:</b>  ${round(close, 2)}\n"
                       f"📌 <b>VENTA a MERCADO</b>  ← entrada inmediata\n"
                       f"🔓 <b>Soporte roto:</b>    ${round(zsl, 2)} (ahora resistencia)\n"
                       f"🛑 <b>Stop Loss:</b>       ${sl_break_sell}  (+${round(sl_break_sell - close, 2)})\n"
                       f"━━━━━━━━━━━━━━━━━━━━\n"
                       f"🎯 <b>TP1:</b> ${tp1_break_sell}  R:R {rr_bs1}:1\n"
                       f"🎯 <b>TP2:</b> ${tp2_break_sell}  R:R {rr(close, sl_break_sell, tp2_break_sell)}:1\n"
                       f"🎯 <b>TP3:</b> ${tp3_break_sell}  R:R {rr(close, sl_break_sell, tp3_break_sell)}:1\n"
                       f"━━━━━━━━━━━━━━━━━━━━\n"
                       f"📊 <b>Score:</b> {score_sell}/21  📉 <b>RSI:</b> {round(rsi, 1)}  📐 <b>ADX:</b> {round(adx, 1)}\n"
                       f"⏱️ <b>TF:</b> 1D  📅 {fecha}  🔒 SWING")
                if self.db and not self.db.existe_senal_reciente(f"{simbolo}_1D", "VENTA", horas=4):
                    try:
                        self._guardar_senal({
                            'timestamp': datetime.now(timezone.utc), 'simbolo': f"{simbolo}_1D",
                            'direccion': 'VENTA', 'precio_entrada': close,
                            'tp1': tp1_break_sell, 'tp2': tp2_break_sell, 'tp3': tp3_break_sell,
                            'sl': sl_break_sell, 'score': score_sell,
                            'indicadores': json.dumps({'rsi': round(rsi, 1), 'adx': round(adx, 2),
                                                       'macd': round(macd, 2), 'atr': round(atr, 2)}),
                            'patron_velas': f"Rotura Bajista: soporte_roto={round(zsl, 2)}",
                            'version_detector': 'GOLD 1D-v2.0'
                        })
                    except Exception as e:
                        logger.error(f"  ⚠️ Error BD: {e}")
                self.enviar(msg); self.marcar_enviada(f"{clave_vela}_BREAK_SELL")

        # ══════════════════════════════════════════════════════════
        # SEÑALES DE DOBLE TECHO / DOBLE SUELO
        # ══════════════════════════════════════════════════════════

        # ── Doble Techo ──
        if dt_detectado and not self.ya_enviada(f"{clave_vela}_DTECHO"):
            altura_dt  = dt_nivel_techo - dt_neckline
            entrada_dt = close
            sl_dt      = round(dt_nivel_techo + atr * 0.5, 2)
            tp1_dt     = round(dt_neckline - altura_dt * 0.5, 2)
            tp2_dt     = round(dt_neckline - altura_dt * 1.0, 2)
            tp3_dt     = round(dt_neckline - altura_dt * 1.5, 2)
            rr_dt1     = rr(entrada_dt, sl_dt, tp1_dt)
            if rr_dt1 >= 1.2:
                msg = (f"🔻 <b>DOBLE TECHO (M) — ORO (XAUUSD) 1D</b>\n"
                       f"━━━━━━━━━━━━━━━━━━━━\n"
                       f"📐 <b>Patrón:</b> Doble Techo confirmado\n"
                       f"💰 <b>Precio actual:</b>  ${round(close, 2)}\n"
                       f"📌 <b>VENTA a MERCADO</b>  ← neckline rota\n"
                       f"📏 <b>Doble techo en:</b>  ${dt_nivel_techo}\n"
                       f"📏 <b>Cuello (neckline):</b> ${dt_neckline}\n"
                       f"🛑 <b>Stop Loss:</b>       ${sl_dt}  (sobre el techo)\n"
                       f"━━━━━━━━━━━━━━━━━━━━\n"
                       f"🎯 <b>TP1:</b> ${tp1_dt}  R:R {rr_dt1}:1  (50% medida)\n"
                       f"🎯 <b>TP2:</b> ${tp2_dt}  R:R {rr(entrada_dt, sl_dt, tp2_dt)}:1  (100% medida)\n"
                       f"🎯 <b>TP3:</b> ${tp3_dt}  R:R {rr(entrada_dt, sl_dt, tp3_dt)}:1  (150% ext)\n"
                       f"━━━━━━━━━━━━━━━━━━━━\n"
                       f"📊 <b>Score:</b> {score_sell}/21  📉 <b>RSI:</b> {round(rsi, 1)}  📐 <b>ADX:</b> {round(adx, 1)}\n"
                       f"⏱️ <b>TF:</b> 1D  📅 {fecha}  🔒 SWING")
                if self.db and not self.db.existe_senal_reciente(f"{simbolo}_1D", "VENTA", horas=4):
                    try:
                        self._guardar_senal({
                            'timestamp': datetime.now(timezone.utc), 'simbolo': f"{simbolo}_1D",
                            'direccion': 'VENTA', 'precio_entrada': entrada_dt,
                            'tp1': tp1_dt, 'tp2': tp2_dt, 'tp3': tp3_dt, 'sl': sl_dt,
                            'score': score_sell,
                            'indicadores': json.dumps({'rsi': round(rsi, 1), 'adx': round(adx, 2),
                                                       'macd': round(macd, 2), 'atr': round(atr, 2)}),
                            'patron_velas': f"Doble Techo: techo={dt_nivel_techo} neckline={dt_neckline}",
                            'version_detector': 'GOLD 1D-v2.0'
                        })
                    except Exception as e:
                        logger.error(f"  ⚠️ Error BD: {e}")
                self.enviar(msg); self.marcar_enviada(f"{clave_vela}_DTECHO")

        # ── Doble Suelo ──
        if ds_detectado and not self.ya_enviada(f"{clave_vela}_DSUELO"):
            altura_ds  = ds_neckline - ds_nivel_suelo
            entrada_ds = close
            sl_ds      = round(ds_nivel_suelo - atr * 0.5, 2)
            tp1_ds     = round(ds_neckline + altura_ds * 0.5, 2)
            tp2_ds     = round(ds_neckline + altura_ds * 1.0, 2)
            tp3_ds     = round(ds_neckline + altura_ds * 1.5, 2)
            rr_ds1     = rr(entrada_ds, sl_ds, tp1_ds)
            if rr_ds1 >= 1.2:
                msg = (f"🔺 <b>DOBLE SUELO (W) — ORO (XAUUSD) 1D</b>\n"
                       f"━━━━━━━━━━━━━━━━━━━━\n"
                       f"📐 <b>Patrón:</b> Doble Suelo confirmado\n"
                       f"💰 <b>Precio actual:</b>  ${round(close, 2)}\n"
                       f"📌 <b>COMPRA a MERCADO</b>  ← neckline superada\n"
                       f"📏 <b>Doble suelo en:</b>  ${ds_nivel_suelo}\n"
                       f"📏 <b>Cuello (neckline):</b> ${ds_neckline}\n"
                       f"🛑 <b>Stop Loss:</b>       ${sl_ds}  (bajo el suelo)\n"
                       f"━━━━━━━━━━━━━━━━━━━━\n"
                       f"🎯 <b>TP1:</b> ${tp1_ds}  R:R {rr_ds1}:1  (50% medida)\n"
                       f"🎯 <b>TP2:</b> ${tp2_ds}  R:R {rr(entrada_ds, sl_ds, tp2_ds)}:1  (100% medida)\n"
                       f"🎯 <b>TP3:</b> ${tp3_ds}  R:R {rr(entrada_ds, sl_ds, tp3_ds)}:1  (150% ext)\n"
                       f"━━━━━━━━━━━━━━━━━━━━\n"
                       f"📊 <b>Score:</b> {score_buy}/21  📉 <b>RSI:</b> {round(rsi, 1)}  📐 <b>ADX:</b> {round(adx, 1)}\n"
                       f"⏱️ <b>TF:</b> 1D  📅 {fecha}  🔒 SWING")
                if self.db and not self.db.existe_senal_reciente(f"{simbolo}_1D", "COMPRA", horas=4):
                    try:
                        self._guardar_senal({
                            'timestamp': datetime.now(timezone.utc), 'simbolo': f"{simbolo}_1D",
                            'direccion': 'COMPRA', 'precio_entrada': entrada_ds,
                            'tp1': tp1_ds, 'tp2': tp2_ds, 'tp3': tp3_ds, 'sl': sl_ds,
                            'score': score_buy,
                            'indicadores': json.dumps({'rsi': round(rsi, 1), 'adx': round(adx, 2),
                                                       'macd': round(macd, 2), 'atr': round(atr, 2)}),
                            'patron_velas': f"Doble Suelo: suelo={ds_nivel_suelo} neckline={ds_neckline}",
                            'version_detector': 'GOLD 1D-v2.0'
                        })
                    except Exception as e:
                        logger.error(f"  ⚠️ Error BD: {e}")
                self.enviar(msg); self.marcar_enviada(f"{clave_vela}_DSUELO")


def analizar(simbolo, params):
    return GoldDetector1D(simbolo=simbolo, tf_label='1D', params=params, telegram_thread_id=TELEGRAM_THREAD_ID).analizar(simbolo, params)


def main():
    logger.info("🚀 Detector de señales GOLD 1D iniciado")
    logger.info(f"⏱️  Revisando cada {CHECK_INTERVAL // 60} minutos")
    logger.info(f"📊 Símbolos: {list(SIMBOLOS.keys())}")
    enviar_telegram("🚀 <b>Detector de señales GOLD 1D iniciado</b>\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    "📊 Monitorizando: XAUUSD\n"
                    "⏱️ Timeframe: 1D\n"
                    "🔄 Revisión cada 10 minutos\n"
                    "💎 Calidad > Cantidad")
    while True:
        ahora_utc = datetime.now(timezone.utc)
        if ahora_utc.weekday() == 5:
            from datetime import timedelta
            proximo_domingo_18 = (ahora_utc + timedelta(days=1)).replace(
                hour=18, minute=0, second=0, microsecond=0)
            segundos_espera = min((proximo_domingo_18 - ahora_utc).total_seconds(), 3600)
            logger.info(f"[{ahora_utc.strftime('%Y-%m-%d %H:%M')} UTC] 💤 Sábado — mercado cerrado.")
            time.sleep(segundos_espera)
            continue
        for simbolo, params in SIMBOLOS.items():
            analizar(simbolo, params)
        logger.info(f"\n⏳ Esperando {CHECK_INTERVAL // 60} minutos...\n")
        time.sleep(CHECK_INTERVAL)


if __name__ == '__main__':
    main()

