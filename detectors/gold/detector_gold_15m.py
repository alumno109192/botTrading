"""
DETECTOR GOLD 15M - SCALPING
Análisis de XAUUSD en timeframe 15 minutos para operaciones de corto plazo
Optimizado para capturar movimientos rápidos con alta frecuencia
"""
import os
from services import tf_bias
from services.dxy_bias import get_dxy_bias, ajustar_score_por_dxy
from services.cot_bias import get_cot_bias, ajustar_score_por_cot
from services.open_interest import get_oi_bias, ajustar_score_por_oi
from services.economic_calendar import obtener_aviso_macro, debe_bloquear_trading, enviar_alerta_bloqueo, verificar_y_notificar_reanudacion
from adapters.data_provider import get_ohlcv
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
from adapters.telegram import enviar_telegram as _enviar_telegram_base

_aviso_macro = ""

def enviar_telegram(mensaje):
    sufijo = f"\n⚠️ <b>Evento macro próximo:</b> {_aviso_macro}" if _aviso_macro else ""
    return _enviar_telegram_base(mensaje + sufijo, TELEGRAM_THREAD_ID)

# Inicializar base de datos solo si las variables están configuradas
from adapters.database import get_db
from core.base_detector_gold import GoldBaseDetector as BaseDetector
import logging
logger = logging.getLogger('bottrading')

db = get_db()

# ══════════════════════════════════════
# CONFIGURACIÓN
# ══════════════════════════════════════
TELEGRAM_TOKEN   = os.environ.get('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID   = os.environ.get('TELEGRAM_CHAT_ID')
TELEGRAM_THREAD_ID = int(os.environ.get('THREAD_ID_SCALPING') or 0) or None

CHECK_INTERVAL = 2 * 60  # cada 2 minutos (scalping requiere alta frecuencia)

# ══════════════════════════════════════
# PARÁMETROS — SCALPING GOLD 15M
# ══════════════════════════════════════
SIMBOLOS = {
    'XAUUSD': {
        'ticker_yf':          'GC=F',       # Gold Futures
        # Zonas S/R calculadas automáticamente en analizar_simbolo() — sin mantenimiento manual
        'sr_lookback':        200,          # 200 velas 15M ≈ 2 días de historia
        'sr_zone_mult':       1.0,          # ancho de zona = atr × 1.0 (scalping, más amplio)
        # TPs calculados automáticamente como múltiplo de ATR — sin mantenimiento manual
        'atr_tp1_mult':       1.5,          # TP1: 1.5× ATR 15M (~$30-50 desde entry)
        'atr_tp2_mult':       2.5,          # TP2: 2.5× ATR
        'atr_tp3_mult':       4.0,          # TP3: 4.0× ATR (objetivo scalping amplio)
        'limit_offset_pct':   0.15,         # Offset muy pequeño (scalping)
        'anticipar_velas':    2,            # Menos anticipación
        'cancelar_dist':      1.2,          # Distancia de cancelación ajustada
        # Indicadores optimizados para SCALPING
        'rsi_length':         9,            # RSI más sensible (periodo corto)
        'rsi_min_sell':       65.0,         # Umbrales más sensibles
        'rsi_max_buy':        35.0,         
        'ema_fast_len':       5,            # EMAs muy rápidas
        'ema_slow_len':       13,           
        'ema_trend_len':      50,           # Tendencia de corto plazo
        'atr_length':         10,           # ATR más sensible
        'atr_sl_mult':        1.5,          # SL más ajustado (scalping)
        'vol_mult':           1.2,          # Volumen importante pero no crítico
        'spread':             0.35,          # Spread típico broker CFD (XAUUSD)
        # Parámetros específicos de scalping
        'min_score_scalping': 3,            # Score mínimo más bajo (más señales)
        'max_perdidas_dia':   3,            # Máximo 3 pérdidas consecutivas
    }
}

# ══════════════════════════════════════
# CONTROL ANTI-SPAM Y GESTIÓN DE RIESGO
# ══════════════════════════════════════
alertas_enviadas = {}
ultimo_analisis = {}
perdidas_consecutivas = 0
ultima_senal_timestamp = None


# ══════════════════════════════════════
# INDICADORES TÉCNICOS
# ══════════════════════════════════════
from core.indicators import (calcular_rsi, calcular_atr, calcular_adx,
    patron_envolvente_alcista, patron_envolvente_bajista, patron_doji,
    detectar_stop_hunt_alcista, detectar_stop_hunt_bajista,
    detectar_canal_roto, detectar_precio_en_canal,
    detectar_ruptura_soporte_horizontal, detectar_ruptura_resistencia_horizontal,
    detectar_retest_resistencia, detectar_retest_soporte,
    detectar_rechazo_en_directriz,
    detectar_cuña_descendente, detectar_cuña_ascendente,
    detectar_doble_techo, detectar_doble_suelo,
    detectar_v_reversal_alcista, detectar_v_reversal_bajista,
    detectar_doble_techo, detectar_doble_suelo,
    calcular_aceleracion_rsi, calcular_micro_volatilidad, calcular_momentum_reciente,
)

# ══════════════════════════════════════
# ANÁLISIS PRICE ACTION SCALPING
# ══════════════════════════════════════
def analizar_price_action_scalping(df):
    """Análisis de price action específico para scalping"""
    score = 0
    
    # 1. Momentum de vela actual (más peso en scalping)
    vela_actual = df.iloc[-1]
    vela_anterior = df.iloc[-2]
    
    body_actual = abs(vela_actual['Close'] - vela_actual['Open'])
    body_anterior = abs(vela_anterior['Close'] - vela_anterior['Open'])
    
    # Vela con cuerpo fuerte = momentum claro
    if body_actual > body_anterior * 1.3:
        score += 1
    
    # 2. Precio rompe máximos/mínimos recientes (5 velas)
    max_reciente = df['High'].iloc[-6:-1].max()
    min_reciente = df['Low'].iloc[-6:-1].min()
    
    if vela_actual['Close'] > max_reciente:
        score += 1  # Ruptura alcista
    elif vela_actual['Close'] < min_reciente:
        score += 1  # Ruptura bajista
    
    # 3. Secuencia de velas (3 velas consecutivas en misma dirección)
    ultimas_3 = df.iloc[-3:]
    todas_alcistas = all(ultimas_3['Close'] > ultimas_3['Open'])
    todas_bajistas = all(ultimas_3['Close'] < ultimas_3['Open'])
    
    if todas_alcistas or todas_bajistas:
        score += 1
    
    return score

# ══════════════════════════════════════
# FUNCIÓN PRINCIPAL DE ANÁLISIS
# ══════════════════════════════════════
def en_sesion_activa():
    """Solo operar en sesión London/NY: 07:00-17:00 UTC."""
    from datetime import timezone as tz
    hora_utc = datetime.now(tz.utc).hour
    return 7 <= hora_utc < 17





class GoldDetector15M(BaseDetector):
    def analizar(self, simbolo, params):
        global perdidas_consecutivas, ultima_senal_timestamp

        # ── Bloqueo por eventos críticos (FOMC, Powell, NFP, CPI) ──
        bloqueado, desc_evento, minutos = debe_bloquear_trading(90)
        if bloqueado:
            enviar_alerta_bloqueo(desc_evento, minutos, ['15M'])
            logger.warning(f"🚫 [15M] Trading bloqueado por evento: {desc_evento}")
            return
        
        # ── Aviso calendario económico (eventos menores) ──
        self.aviso_macro = obtener_aviso_macro(45, '15M', simbolo)

        try:
            # Descargar datos 5M y resamplear a 15M (comparte caché con detectores 5M y 1H)
            df_5m, _ = get_ohlcv(params['ticker_yf'], period='7d', interval='5m')
            df = df_5m.resample('15min').agg({'Open': 'first', 'High': 'max', 'Low': 'min', 'Close': 'last', 'Volume': 'sum'}).dropna()
        
            if df.empty or len(df) < 100:
                logger.warning(f"⚠️ Datos insuficientes para {simbolo}")
                return
        
            # Limpiar columnas
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.droplevel(1)
        
            # Renombrar columnas solo si es necesario (data_provider ya las normaliza)
            if 'Open' not in df.columns:
                if len(df.columns) == 6:
                    df.columns = ['Open', 'High', 'Low', 'Close', 'Adj Close', 'Volume']
                elif len(df.columns) == 5:
                    df.columns = ['Open', 'High', 'Low', 'Close', 'Volume']
                else:
                    logger.warning(f"⚠️ Columnas inesperadas ({len(df.columns)}): {df.columns.tolist()}")
                    return
        
            # Calcular indicadores
            close = df['Close'].iloc[-1]
            vol   = df['Volume'].iloc[-6:].mean()
        
            rsi_len = params['rsi_length']
            rsi_series = calcular_rsi(df['Close'], rsi_len)
            rsi = rsi_series.iloc[-1]
        
            ema_fast = df['Close'].ewm(span=params['ema_fast_len']).mean()
            ema_slow = df['Close'].ewm(span=params['ema_slow_len']).mean()
            ema_trend = df['Close'].ewm(span=params['ema_trend_len']).mean()
        
            atr_len = params['atr_length']
            _atr_series = calcular_atr(df, atr_len)
            atr = float(_atr_series.iloc[-1])
            atr_media = float(_atr_series.rolling(20).mean().iloc[-1])
        
            adx, _, _ = calcular_adx(df)
            adx = adx.iloc[-1]
        
            # Parámetros de zonas (calculados automáticamente)
            zrl, zrh, zsl, zsh = self.calcular_zonas_sr(df, atr, params['sr_lookback'], params['sr_zone_mult'])
            tol = round(atr * 0.4, 2)   # tolerancia dinámica: 40% del ATR
            logger.info(f"  📍 Zonas auto — Resist: ${zrl:.1f}-${zrh:.1f} | Soporte: ${zsl:.1f}-${zsh:.1f}")
        
            # Detectar zonas
            en_zona_resist = (zrl <= close <= zrh)
            en_zona_soporte = (zsl <= close <= zsh)
            aproximando_resistencia = (zrl - tol <= close < zrl)
            aproximando_soporte = (zsh < close <= zsh + tol)
        
            # ══════════════════════════════════════
            # SCORING SYSTEM - SCALPING (más sensible)
            # ══════════════════════════════════════
            score_sell = 0
            score_buy  = 0
        
            # 1. PRICE ACTION SCALPING (peso importante)
            pa_score = analizar_price_action_scalping(df)
            if df['Close'].iloc[-1] < df['Open'].iloc[-1]:  # Vela bajista
                score_sell += pa_score
            else:
                score_buy += pa_score
        
            # 2. RSI (más sensible en scalping)
            _rsi_baj_3, _rsi_sub_3 = calcular_aceleracion_rsi(rsi_series)
            _micro_vol    = calcular_micro_volatilidad(df)
            _momentum_rec = calcular_momentum_reciente(df)
            if rsi >= params['rsi_min_sell']:
                score_sell += 2
                if _rsi_baj_3: score_sell += 1   # RSI bajando 3 velas consecutivas
            elif rsi >= 60:
                score_sell += 1
        
            if rsi <= params['rsi_max_buy']:
                score_buy += 2
                if _rsi_sub_3: score_buy += 1    # RSI subiendo 3 velas consecutivas
            elif rsi <= 40:
                score_buy += 1
        
            # 3. EMAs (cruce rápido = señal)
            if ema_fast.iloc[-1] < ema_slow.iloc[-1]:
                score_sell += 2
                if ema_fast.iloc[-2] >= ema_slow.iloc[-2]:  # Cruce reciente
                    score_sell += 1
        
            if ema_fast.iloc[-1] > ema_slow.iloc[-1]:
                score_buy += 2
                if ema_fast.iloc[-2] <= ema_slow.iloc[-2]:  # Cruce reciente
                    score_buy += 1
        
            # 4. Tendencia general (EMA 50)
            if close < ema_trend.iloc[-1]:
                score_sell += 1
            else:
                score_buy += 1
        
            # 5. Zonas de soporte/resistencia
            if en_zona_resist or aproximando_resistencia:
                score_sell += 2
            if en_zona_soporte or aproximando_soporte:
                score_buy += 2
        
            # 6. ADX (fuerza de tendencia)
            if adx > 25:  # Tendencia fuerte
                if score_sell > score_buy:
                    score_sell += 1
                else:
                    score_buy += 1
        
            # 7. Volumen (confirma movimiento)
            vol_medio = df['Volume'].iloc[-20:].mean()
            if vol > vol_medio * params['vol_mult']:
                if score_sell > score_buy:
                    score_sell += 1
                else:
                    score_buy += 1
        
            # 8. Patrones de velas
            if patron_envolvente_bajista(df):
                score_sell += 2
            if patron_envolvente_alcista(df):
                score_buy += 2

            # 9. Stop Hunt / Falsa Ruptura (patrón de alta fiabilidad en Gold)
            if detectar_stop_hunt_bajista(df):
                score_sell += 3
                logger.info(f"  🎯 [15M] Stop Hunt BAJISTA detectado — +3 pts SELL")
            if detectar_stop_hunt_alcista(df):
                score_buy += 3
                logger.info(f"  🎯 [15M] Stop Hunt ALCISTA detectado — +3 pts BUY")

            # 9.5. Doble Techo / Doble Suelo (patrones de inversión)
            if detectar_doble_techo(df, atr):
                score_sell += 2
                logger.info(f"  🎯 [15M] DOBLE TECHO detectado — +2 pts SELL")
            if detectar_doble_suelo(df, atr):
                score_buy += 2
                logger.info(f"  🎯 [15M] DOBLE SUELO detectado — +2 pts BUY")

            # 9.6. V-Reversal (reversión vertical — patrón de alta velocidad)
            # Parámetros 15M: lookback=16 velas (~4h), mínimo 4.0 ATR caída, 3.0 ATR rebote
            v_rev_alc, v_min, v_precio = detectar_v_reversal_alcista(df, atr, lookback=16, 
                                                                       min_caida_atr=4.0, 
                                                                       min_rebote_atr=3.0)
            v_rev_baj, v_max, v_precio_baj = detectar_v_reversal_bajista(df, atr, lookback=16,
                                                                           min_subida_atr=4.0,
                                                                           min_caida_atr=3.0)
            if v_rev_alc:
                score_buy += 4
                logger.info(f"  ⚡ [15M] V-REVERSAL ALCISTA detectado — mín ${v_min:.2f} → ${v_precio:.2f} — +4 pts BUY")
            if v_rev_baj:
                score_sell += 4
                logger.info(f"  ⚡ [15M] V-REVERSAL BAJISTA detectado — máx ${v_max:.2f} → ${v_precio_baj:.2f} — +4 pts SELL")

            # 10. Canal roto / directriz (patrón de rotura 15M) ─────────────
            _lkb15 = params.get('sr_lookback', 200)
            _zm15  = params.get('sr_zone_mult', 1.0)
            canal_alc_roto_15m, canal_baj_roto_15m, \
                linea_sop_canal_15m, linea_res_canal_15m = detectar_canal_roto(
                    df, atr, lookback=_lkb15, wing=3)
            en_resist_canal_baj_15m, en_sop_canal_alc_15m, \
                linea_res_precio_15m, linea_sop_precio_15m = detectar_precio_en_canal(
                    df, atr, lookback=_lkb15, wing=3)

            if canal_alc_roto_15m:
                score_sell += 2
                logger.info(f"  🔻 [15M] CANAL ALCISTA ROTO — línea soporte ${linea_sop_canal_15m:.2f}")
            if canal_baj_roto_15m:
                score_buy += 2
                logger.info(f"  🔺 [15M] CANAL BAJISTA ROTO — línea resist ${linea_res_canal_15m:.2f}")
            if en_resist_canal_baj_15m:
                score_sell += 3
                logger.info(f"  📐 [15M] PRECIO EN DIRECTRIZ BAJISTA — ${linea_res_precio_15m:.2f}")
            if en_sop_canal_alc_15m:
                score_buy += 3
                logger.info(f"  📐 [15M] PRECIO EN DIRECTRIZ ALCISTA — ${linea_sop_precio_15m:.2f}")

            # ── Ruptura horizontal directa (sin retest) 15M ────────────────
            _lkb15_h = params.get('sr_lookback', 200)
            _rup_sop_15m, _niv_sop_15m = detectar_ruptura_soporte_horizontal(
                df, atr, lookback=_lkb15_h, wing=3)
            _rup_res_15m, _niv_res_15m = detectar_ruptura_resistencia_horizontal(
                df, atr, lookback=_lkb15_h, wing=3)
            if _rup_sop_15m:
                score_sell += 4
                logger.info(f"  💥 [15M] RUPTURA SOPORTE ${_niv_sop_15m:.2f} — +4 pts SELL")
            if _rup_res_15m:
                score_buy += 4
                logger.info(f"  💥 [15M] RUPTURA RESISTENCIA ${_niv_res_15m:.2f} — +4 pts BUY")

            # ── Retest soporte→resistencia / resistencia→soporte (15M) ──────────
            _retest_res_15m, _niv_retest_res_15m = detectar_retest_resistencia(
                df, atr, lookback=_lkb15_h, wing=3)
            _retest_sop_15m, _niv_retest_sop_15m = detectar_retest_soporte(
                df, atr, lookback=_lkb15_h, wing=3)
            if _retest_res_15m:
                score_sell += 5
                logger.info(f"  🔁 [15M] RETEST RESISTENCIA ${_niv_retest_res_15m:.2f} — +5 pts SELL")
            if _retest_sop_15m:
                score_buy += 5
                logger.info(f"  🔁 [15M] RETEST SOPORTE ${_niv_retest_sop_15m:.2f} — +5 pts BUY")

            # ── Rechazo en directriz (15M) ───────────────────────────────────────
            _rec_dir_baj_15m, _precio_dir_baj_15m = detectar_rechazo_en_directriz(
                df, atr, lookback=_lkb15_h, wing=3, direccion='bajista')
            _rec_dir_alc_15m, _precio_dir_alc_15m = detectar_rechazo_en_directriz(
                df, atr, lookback=_lkb15_h, wing=3, direccion='alcista')
            if _rec_dir_baj_15m:
                score_sell += 4
                logger.info(f"  📐 [15M] RECHAZO EN DIRECTRIZ BAJISTA ${_precio_dir_baj_15m:.2f} — +4 pts SELL")
            if _rec_dir_alc_15m:
                score_buy += 4
                logger.info(f"  📐 [15M] RECHAZO EN DIRECTRIZ ALCISTA ${_precio_dir_alc_15m:.2f} — +4 pts BUY")

            # ── Cuña descendente / ascendente (15M) ─────────────────────────────
            _cuña_desc_15m, _t_desc_15m, _s_desc_15m = detectar_cuña_descendente(
                df, atr, lookback=_lkb15_h, wing=2, max_amplitud_pct=0.035)
            _cuña_asc_15m, _t_asc_15m, _s_asc_15m = detectar_cuña_ascendente(
                df, atr, lookback=_lkb15_h, wing=2, max_amplitud_pct=0.035)
            if _cuña_desc_15m == 'ruptura_alcista':
                score_buy += 5
                logger.info(f"  📐 [15M] CUÑA DESC ROTA AL ALZA (techo ${_t_desc_15m:.2f}) — +5 pts BUY")
            elif _cuña_desc_15m == 'ruptura_bajista':
                score_sell += 5
                logger.info(f"  📐 [15M] CUÑA DESC ROTA A LA BAJA (suelo ${_s_desc_15m:.2f}) — +5 pts SELL")
            elif _cuña_desc_15m == 'compresion':
                score_buy += 2
                logger.info(f"  📐 [15M] CUÑA DESC en compresión ${_s_desc_15m:.2f}-${_t_desc_15m:.2f} — +2 pts BUY")
            if _cuña_asc_15m == 'ruptura_bajista':
                score_sell += 5
                logger.info(f"  📐 [15M] CUÑA ASC ROTA A LA BAJA (suelo ${_s_asc_15m:.2f}) — +5 pts SELL")
            elif _cuña_asc_15m == 'compresion':
                score_sell += 2
                logger.info(f"  📐 [15M] CUÑA ASC en compresión ${_s_asc_15m:.2f}-${_t_asc_15m:.2f} — +2 pts SELL")

            # ── Doble Techo / Doble Suelo (15M) ─────────────────────────────────
            _dt_15m, _dt_nivel_15m, _dt_neck_15m = detectar_doble_techo(
                df, atr, lookback=_lkb15_h, tol_mult=0.6)
            _ds_15m, _ds_nivel_15m, _ds_neck_15m = detectar_doble_suelo(
                df, atr, lookback=_lkb15_h, tol_mult=0.6)
            if _dt_15m:
                score_sell += 4
                logger.info(f"  🔻 [15M] DOBLE TECHO (M) detectado — techo=${_dt_nivel_15m:.1f} cuello=${_dt_neck_15m:.1f} — +4 pts SELL")
            if _ds_15m:
                score_buy += 4
                logger.info(f"  🔺 [15M] DOBLE SUELO (W) detectado — suelo=${_ds_nivel_15m:.1f} cuello=${_ds_neck_15m:.1f} — +4 pts BUY")

            # ── Confirmación 1M — "la puntilla" ─────────────────────────────────
            # Solo si score está en zona de desempate (4–7), evita llamadas innecesarias
            _umbral_conf_15 = 8
            _necesita_conf_sell = 4 <= score_sell < _umbral_conf_15
            _necesita_conf_buy  = 4 <= score_buy  < _umbral_conf_15
            if _necesita_conf_sell or _necesita_conf_buy:
                try:
                    df_1m, _ = get_ohlcv(params['ticker_yf'], period='1d', interval='1m')
                    if df_1m is not None and len(df_1m) >= 10:
                        atr_1m = float(calcular_atr(df_1m, 7).iloc[-1])
                        if _necesita_conf_sell:
                            _env_baj_1m   = patron_envolvente_bajista(df_1m)
                            _sh_baj_1m    = detectar_stop_hunt_bajista(df_1m, atr_1m)
                            _rej_dir_1m_s = detectar_rechazo_en_directriz(
                                df_1m, atr_1m, lookback=60, wing=2, direccion='bajista')[0]
                            if _env_baj_1m or _sh_baj_1m or _rej_dir_1m_s:
                                score_sell += 2
                                motivo = ('envolvente' if _env_baj_1m
                                          else 'stop hunt' if _sh_baj_1m else 'directriz')
                                logger.info(f"  🎯 [1M] Confirmación SELL ({motivo}) — +2 pts SELL")
                        if _necesita_conf_buy:
                            _env_alc_1m   = patron_envolvente_alcista(df_1m)
                            _sh_alc_1m    = detectar_stop_hunt_alcista(df_1m, atr_1m)
                            _rej_dir_1m_b = detectar_rechazo_en_directriz(
                                df_1m, atr_1m, lookback=60, wing=2, direccion='alcista')[0]
                            if _env_alc_1m or _sh_alc_1m or _rej_dir_1m_b:
                                score_buy += 2
                                motivo = ('envolvente' if _env_alc_1m
                                          else 'stop hunt' if _sh_alc_1m else 'directriz')
                                logger.info(f"  🎯 [1M] Confirmación BUY ({motivo}) — +2 pts BUY")
                except Exception as _e_1m:
                    logger.debug(f"  [1M] No se pudo obtener confirmación: {_e_1m}")

            # Score máximo: ~30 puntos (+2 del confirmador 1M)
            max_score = 30
        
            # ══════════════════════════════════════
            # NIVELES DE SEÑAL 15M — solo FUERTE llega a Telegram
            # ══════════════════════════════════════
            senal_sell_fuerte = score_sell >= 8
            senal_buy_fuerte  = score_buy  >= 8

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
                score_sell, score_buy, vol, vol_medio, params['vol_mult'])
            if _vol_bajo:
                logger.info(f"  ⚠️ [15M] Volumen bajo ({vol:.0f} < {vol_medio * params['vol_mult']:.0f}) — scores penalizados -3")

            # Recalcular umbrales tras ajuste DXY y filtro de volumen (con umbral adaptativo)
            # ── Micro-volatilidad y momentum reciente ─────────────────────────────
            if _micro_vol > 1.5:
                if score_sell > score_buy:
                    score_sell = min(score_sell + 1, 23)
                    logger.info(f"  📈 [15M] Micro-vol {_micro_vol:.2f} (expansión) — +1 SELL")
                elif score_buy > score_sell:
                    score_buy = min(score_buy + 1, 23)
                    logger.info(f"  📈 [15M] Micro-vol {_micro_vol:.2f} (expansión) — +1 BUY")
            elif _micro_vol < 0.8:
                score_sell = max(0, score_sell - 1)
                score_buy  = max(0, score_buy  - 1)
                logger.info(f"  😴 [15M] Micro-vol {_micro_vol:.2f} (dormido) — -1 ambos scores")
            if _momentum_rec == -1 and (en_zona_resist or aproximando_resistencia):
                score_sell = min(score_sell + 1, 23)
                logger.info(f"  🔻 [15M] Momentum bajista en resistencia — +1 SELL")
            elif _momentum_rec == 1 and (en_zona_soporte or aproximando_soporte):
                score_buy = min(score_buy + 1, 23)
                logger.info(f"  🔺 [15M] Momentum alcista en soporte — +1 BUY")
            _umbral_fue = self.umbral_adaptativo(8, atr, atr_media)
            senal_sell_fuerte = score_sell >= _umbral_fue
            senal_buy_fuerte  = score_buy  >= _umbral_fue

            # ── Snapshot completo de condiciones para backtesting/estudio ─────
            _condiciones_bd = {
                # Indicadores numéricos
                'rsi': round(float(rsi), 1), 'atr': round(float(atr), 2), 'atr_media': round(float(atr_media), 2),
                'adx': round(float(adx), 1),
                'ema_fast': round(float(ema_fast.iloc[-1]), 2), 'ema_slow': round(float(ema_slow.iloc[-1]), 2),
                'ema_trend': round(float(ema_trend.iloc[-1]), 2),
                'vol': round(float(vol), 0), 'vol_medio': round(float(vol_medio), 0),
                'score_sell': score_sell, 'score_buy': score_buy,
                # Zonas S/R
                'zrl': round(zrl, 2), 'zrh': round(zrh, 2), 'zsl': round(zsl, 2), 'zsh': round(zsh, 2),
                # Condiciones de zona
                'en_zona_resist': bool(en_zona_resist), 'en_zona_soporte': bool(en_zona_soporte),
                'aproximando_resist': bool(aproximando_resistencia), 'aproximando_soporte': bool(aproximando_soporte),
                # Patrones de velas
                'envolvente_bajista': bool(patron_envolvente_bajista(df)),
                'envolvente_alcista': bool(patron_envolvente_alcista(df)),
                'stop_hunt_bajista': bool(detectar_stop_hunt_bajista(df)),
                'stop_hunt_alcista': bool(detectar_stop_hunt_alcista(df)),
                'v_rev_alcista': bool(v_rev_alc), 'v_rev_bajista': bool(v_rev_baj),
                # Canal / estructura
                'canal_alc_roto': bool(canal_alc_roto_15m), 'canal_baj_roto': bool(canal_baj_roto_15m),
                'en_resist_canal_baj': bool(en_resist_canal_baj_15m), 'en_sop_canal_alc': bool(en_sop_canal_alc_15m),
                'rup_sop': bool(_rup_sop_15m), 'rup_res': bool(_rup_res_15m),
                'retest_resist': bool(_retest_res_15m), 'retest_sop': bool(_retest_sop_15m),
                'rec_dir_baj': bool(_rec_dir_baj_15m), 'rec_dir_alc': bool(_rec_dir_alc_15m),
                'cuña_desc': str(_cuña_desc_15m) if _cuña_desc_15m else None,
                'cuña_asc': str(_cuña_asc_15m) if _cuña_asc_15m else None,
                'dt_detectado': bool(_dt_15m), 'ds_detectado': bool(_ds_15m),
                # Contexto macro
                'dxy_bias': str(dxy_bias) if dxy_bias else None,
                'cot_bias': str(_cot_bias) if _cot_bias else None,
                'oi_bias': str(_oi_bias) if _oi_bias else None,
            }

            # ── Filtro de sesión: fuera de 08-21 UTC solo FUERTE (TF corto = ruido nocturno) ──
            if not self.en_sesion_optima():
                logger.info(f"  🌙 [15M] Fuera sesión óptima — señal suprimida (08-21 UTC)")
                senal_sell_fuerte = False
                senal_buy_fuerte  = False

            # Cancelaciones (más estrictas en scalping)
            simbolo_db_15m = f"{simbolo}_15M"
            cancelar_sell = (close < zsh) or (rsi < 30)
            cancelar_buy  = (close > zrh) or (rsi > 70)
            # Bloquear si ya hay señal ACTIVA en la MISMA dirección
            if self.db and self.db.existe_senal_activa_misma_dir(simbolo_db_15m, 'VENTA'):
                cancelar_sell = True
                logger.info(f"  🚫 [15M] cancelar_sell=True: VENTA ya activa en BD")
            if self.db and self.db.existe_senal_activa_misma_dir(simbolo_db_15m, 'COMPRA'):
                cancelar_buy = True
                logger.info(f"  🚫 [15M] cancelar_buy=True: COMPRA ya activa en BD")
        
            # ── SL y TP para SCALPING ──
            asm = params['atr_sl_mult']
            sl_venta  = close + (atr * asm)
            sl_compra = close - (atr * asm)
        
            # Límites de entrada
            offset_pct = params['limit_offset_pct']
            sell_limit = close * (1 + offset_pct / 100)
            buy_limit  = close * (1 - offset_pct / 100)

            # Ajuste de spread del broker: BUY paga ask (bid+spread), SELL cobra bid (bid-spread)
            spread = params.get('spread', 0.35)
            sell_entry = round(sell_limit - spread, 2)
            buy_entry  = round(buy_limit  + spread, 2)

            tp1_v = round(sell_entry - atr * params['atr_tp1_mult'], 2)
            tp2_v = round(sell_entry - atr * params['atr_tp2_mult'], 2)
            tp3_v = round(sell_entry - atr * params['atr_tp3_mult'], 2)
            tp1_c = round(buy_entry  + atr * params['atr_tp1_mult'], 2)
            tp2_c = round(buy_entry  + atr * params['atr_tp2_mult'], 2)
            tp3_c = round(buy_entry  + atr * params['atr_tp3_mult'], 2)
        
            def rr(limit, sl, tp):
                return round(abs(tp - limit) / abs(sl - limit), 1) if abs(sl - limit) > 0 else 0
        
            # ── Log consola ──
            fecha = df.index[-1].strftime('%Y-%m-%d %H:%M')
        
            # ══════════════════════════════════════
            # VERIFICAR SI YA SE ANALIZÓ
            # ══════════════════════════════════════
            clave_simbolo = simbolo
        
            if clave_simbolo in self.ultimo_analisis:
                ultima_fecha = self.ultimo_analisis[clave_simbolo]['fecha']
                ultimo_score_sell = self.ultimo_analisis[clave_simbolo]['score_sell']
                ultimo_score_buy = self.ultimo_analisis[clave_simbolo]['score_buy']
            
                if (ultima_fecha == fecha and 
                    abs(ultimo_score_sell - score_sell) <= 1 and 
                    abs(ultimo_score_buy - score_buy) <= 1):
                    logger.info(f"  ℹ️  Vela {fecha} ya analizada - Sin cambios")
                    return
        
            self.ultimo_analisis[clave_simbolo] = {
                'fecha': fecha,
                'score_sell': score_sell,
                'score_buy': score_buy
            }
        
            logger.info(f"  📅 Vela:  {fecha}")
            logger.info(f"  💰 Close: {round(close, 2)}")
            logger.info(f"  📊 Score SELL: {score_sell}/{max_score} | Score BUY: {score_buy}/{max_score}")
            logger.info(f"  🔴 SELL Fuerte:{senal_sell_fuerte}  🟢 BUY Fuerte:{senal_buy_fuerte}")
            logger.info(f"  📉 RSI: {round(rsi, 1)} | ADX: {round(adx, 1)} | ATR: {round(atr, 2)}")
        
            # ── CONTROL DE PÉRDIDAS CONSECUTIVAS (consultado desde BD) ──
            perdidas_consecutivas = self.db.contar_perdidas_consecutivas(f"{simbolo}_15M") if self.db else 0
            if perdidas_consecutivas >= params['max_perdidas_dia']:
                if not (senal_sell_fuerte or senal_buy_fuerte):
                    logger.warning(f"  ⛔ Trading pausado: {perdidas_consecutivas} pérdidas consecutivas — esperando señal fuerte")
                    return
                logger.info(f"  ✅ Señal fuerte detectada tras {perdidas_consecutivas} pérdidas — reanudando")
        
            # ══════════════════════════════════════
            # ANTI-SPAM
            # ══════════════════════════════════════
            clave_vela = f"{simbolo}_{fecha}"
        
            def ya_enviada(tipo):
                clave = f"{clave_vela}_{tipo}"
                ts_mem = self.alertas_enviadas.get(clave, 0)
                if ts_mem > time.time() - 172800:
                    return True
                if self.db:
                    ts_db = self.db.get_antispam(clave)
                    if ts_db > time.time() - 172800:
                        self.alertas_enviadas[clave] = ts_db
                        return True
                return False
        
            def marcar_enviada(tipo):
                clave = f"{clave_vela}_{tipo}"
                self.alertas_enviadas[clave] = time.time()
                if self.db:
                    self.db.set_antispam(clave, self.alertas_enviadas[clave])
            
            # ── EXCLUSIÓN MUTUA ──
            if senal_sell_fuerte and senal_buy_fuerte:
                if score_sell >= score_buy:
                    senal_buy_fuerte = False
                    logger.info(f"  ⚖️ Exclusión mutua: BUY suprimida (SELL {score_sell} >= BUY {score_buy})")
                else:
                    senal_sell_fuerte = False
                    logger.info(f"  ⚖️ Exclusión mutua: SELL suprimida (BUY {score_buy} > SELL {score_sell})")

            _sesgo_dir = tf_bias.BIAS_BEARISH if score_sell > score_buy else tf_bias.BIAS_BULLISH if score_buy > score_sell else tf_bias.BIAS_NEUTRAL
            tf_bias.publicar_sesgo(simbolo, '15M', _sesgo_dir, max(score_sell, score_buy))
            _conf_sell = ""; _conf_buy = ""
            if senal_sell_fuerte:
                _ok, _desc = tf_bias.verificar_confluencia(simbolo, '15M', tf_bias.BIAS_BEARISH)
                if not _ok:
                    logger.info(f"  🚫 SELL bloqueada por TF superior: {_desc[:80]}")
                    senal_sell_fuerte = False
                else:
                    _conf_sell = _desc
            if senal_buy_fuerte:
                _ok, _desc = tf_bias.verificar_confluencia(simbolo, '15M', tf_bias.BIAS_BULLISH)
                if not _ok:
                    logger.info(f"  🚫 BUY bloqueada por TF superior: {_desc[:80]}")
                    senal_buy_fuerte = False
                else:
                    _conf_buy = _desc

            # ── MODO CAZA: activar si hay zona activa del 1H ───────────────────────────
            # El detector 1H publicó una zona activa (score≥5). Si el precio
            # entra en esa zona y score_15M≥4, disparar señal con umbral reducido.
            _zona_1h_buy  = tf_bias.obtener_zona_activa(simbolo, tf_bias.BIAS_BULLISH)
            _zona_1h_sell = tf_bias.obtener_zona_activa(simbolo, tf_bias.BIAS_BEARISH)
            _modo_caza_buy  = False
            _modo_caza_sell = False
            _UMBRAL_CAZA    = 4   # mitad del umbral normal — el 1H ya confirmó

            if _zona_1h_buy and not senal_buy_fuerte and self.en_sesion_optima():
                _tol_1h = _zona_1h_buy['atr'] * 0.6
                _en_zona_1h = (_zona_1h_buy['zsl'] - _tol_1h <= close <= _zona_1h_buy['zsh'] + _tol_1h)
                if _en_zona_1h and score_buy >= _UMBRAL_CAZA:
                    senal_buy_fuerte = True
                    _modo_caza_buy   = True
                    _conf_buy = (f"⚡ <b>Setup 1H / Entrada 15M</b>\n"
                                 f"📍 Zona 1H soporte: ${_zona_1h_buy['zsl']:.2f}–${_zona_1h_buy['zsh']:.2f} | Score 1H: {_zona_1h_buy['score_1h']}/21\n"
                                 f"📌 Limit 1H: ${_zona_1h_buy['buy_limit']:.2f} → ajustado 15M: ${buy_limit:.2f}")
                    logger.info(f"  🎯 [15M] MODO CAZA BUY — zona 1H activa (score {score_buy}) → señal activada")

            if _zona_1h_sell and not senal_sell_fuerte and self.en_sesion_optima():
                _tol_1h = _zona_1h_sell['atr'] * 0.6
                _en_zona_1h = (_zona_1h_sell['zrl'] - _tol_1h <= close <= _zona_1h_sell['zrh'] + _tol_1h)
                if _en_zona_1h and score_sell >= _UMBRAL_CAZA:
                    senal_sell_fuerte = True
                    _modo_caza_sell   = True
                    _conf_sell = (f"⚡ <b>Setup 1H / Entrada 15M</b>\n"
                                  f"📍 Zona 1H resistencia: ${_zona_1h_sell['zrl']:.2f}–${_zona_1h_sell['zrh']:.2f} | Score 1H: {_zona_1h_sell['score_1h']}/21\n"
                                  f"📌 Limit 1H: ${_zona_1h_sell['sell_limit']:.2f} → ajustado 15M: ${sell_limit:.2f}")
                    logger.info(f"  🎯 [15M] MODO CAZA SELL — zona 1H activa (score {score_sell}) → señal activada")

            # Títulos dinámicos según modo
            _titulo_sell = ("⚡ ENTRADA PRECISA — <b>Setup 1H / Entrada 15M</b>"
                            if _modo_caza_sell else "🔥 SELL FUERTE — <b>GOLD 15M SCALPING</b>")
            _titulo_buy  = ("⚡ ENTRADA PRECISA — <b>Setup 1H / Entrada 15M</b>"
                            if _modo_caza_buy  else "🔥 BUY FUERTE — <b>GOLD 15M SCALPING</b>")

            # ════════════════════════════════
            # ENVIAR SEÑALES SCALPING
            # ════════════════════════════════

            # ── FILTRO R:R MÍNIMO 1.5 (Scalping 15M) ──
            RR_MINIMO = 1.5
            rr_sell_tp1 = rr(sell_entry, sl_venta,  tp1_v)
            rr_buy_tp1  = rr(buy_entry,  sl_compra, tp1_c)
            if rr_sell_tp1 < RR_MINIMO:
                logger.warning(f'  ⛔ SELL bloqueada: R:R TP1={rr_sell_tp1} < {RR_MINIMO}')
                cancelar_sell = True
            if rr_buy_tp1 < RR_MINIMO:
                logger.warning(f'  ⛔ BUY bloqueada: R:R TP1={rr_buy_tp1} < {RR_MINIMO}')
                cancelar_buy = True

            simbolo_db = f"{simbolo}_15M"

            # ── Contexto HTF para mensajes ──────────────────────────────────────────────
            _ctx_htf = ""
            try:
                _htf_lineas_15m = []
                for _tf_key, _label in [('1W', '1W'), ('1D', '1D'), ('4H', '4H'), ('1H', '1H')]:
                    _bd = tf_bias.obtener_sesgo(simbolo, _tf_key)
                    if _bd:
                        _b   = _bd['bias']
                        _ico = "📈" if _b == tf_bias.BIAS_BULLISH else "📉" if _b == tf_bias.BIAS_BEARISH else "➖"
                        _htf_lineas_15m.append(f"  {_ico} <b>{_label}:</b> {_b}")
                    else:
                        _htf_lineas_15m.append(f"  ⏳ <b>{_tf_key}:</b> sin datos")
                _ctx_htf = "\n━━━━━━━━━━━━━━━━━━━━\n📊 <b>Contexto HTF:</b>\n" + "\n".join(_htf_lineas_15m)
            except Exception as _ctx_e:
                logger.debug(f"  [15M] Error _ctx_htf: {_ctx_e}")

            # ── Construir diagnóstico de patrones detectados ──
            patrones_detectados = []
            if patron_envolvente_bajista(df):
                patrones_detectados.append("📉 Envolvente Bajista")
            if patron_envolvente_alcista(df):
                patrones_detectados.append("📈 Envolvente Alcista")
            if patron_doji(df):
                patrones_detectados.append("⚪ Doji (indecisión)")
            if detectar_stop_hunt_bajista(df):
                patrones_detectados.append("🎯 Stop Hunt Bajista (trampa alcista)")
            if detectar_stop_hunt_alcista(df):
                patrones_detectados.append("🎯 Stop Hunt Alcista (trampa bajista)")
            if detectar_doble_techo(df, atr):
                patrones_detectados.append("🔻 Doble Techo (M)")
            if detectar_doble_suelo(df, atr):
                patrones_detectados.append("🔺 Doble Suelo (W)")
            if v_rev_alc:
                patrones_detectados.append(f"⚡ V-Reversal Alcista (${v_min:.2f}→${v_precio:.2f})")
            if v_rev_baj:
                patrones_detectados.append(f"⚡ V-Reversal Bajista (${v_max:.2f}→${v_precio_baj:.2f})")
            
            diagnostico_patrones = "\n".join(patrones_detectados) if patrones_detectados else "Sin patrones destacados"

            # ── SEÑALES VENTA — solo FUERTE ──
            if senal_sell_fuerte and not cancelar_sell:
                if self.db and self.db.existe_senal_activa_misma_dir(simbolo_db, 'VENTA'):
                    logger.info(f"  ℹ️  SELL 15M bloqueada: ya existe señal ACTIVA en {simbolo_db}")
                else:
                    # Timestamp de la vela analizada
                    timestamp_vela = df.index[-1]
                    hora_estudio = timestamp_vela.strftime('%Y-%m-%d %H:%M UTC')
                    hora_envio = datetime.now(timezone.utc).strftime('%H:%M:%S UTC')
                    
                    msg = (f"{_titulo_sell}\n"
                           f"━━━━━━━━━━━━━━━━━━━━\n"
                           f"💰 <b>Precio:</b>     ${round(close, 2)}\n"
                           f"📌 <b>SELL LIMIT:</b> ${sell_entry:.2f}  (spread ${spread:.2f} incluido)\n"
                           f"🛑 <b>Stop Loss:</b>  ${round(sl_venta, 2)}\n"
                           f"━━━━━━━━━━━━━━━━━━━━\n"
                           f"🎯 <b>TP1:</b> ${tp1_v}  R:R {rr(sell_entry, sl_venta, tp1_v)}:1\n"
                           f"🎯 <b>TP2:</b> ${tp2_v}  R:R {rr(sell_entry, sl_venta, tp2_v)}:1\n"
                           f"🎯 <b>TP3:</b> ${tp3_v}  R:R {rr(sell_entry, sl_venta, tp3_v)}:1\n"
                           f"━━━━━━━━━━━━━━━━━━━━\n"
                           f"📊 <b>Score:</b> {score_sell}/{max_score}  📉 <b>RSI:</b> {round(rsi, 1)}  📐 <b>ADX:</b> {round(adx, 1)}\n"
                           f"⏱️ <b>TF:</b> 15M\n"
                           f"📅 <b>Estudio:</b> {hora_estudio}\n"
                           f"🕐 <b>Envío:</b> {hora_envio}\n"
                           f"━━━━━━━━━━━━━━━━━━━━\n"
                           f"🔍 <b>Patrones Detectados:</b>\n{diagnostico_patrones}")
                    if _conf_sell:
                        msg += f"\n━━━━━━━━━━━━━━━━━━━━\n{_conf_sell}"
                    if _ctx_htf:
                        msg += _ctx_htf
                    if self.db:
                        try:
                            self._guardar_senal({
                                'timestamp': datetime.now(timezone.utc), 
                                'timestamp_entry': timestamp_vela.isoformat(),  # Hora de la vela
                                'simbolo': simbolo_db,
                                'asset': 'GOLD',
                                'timeframe': '15M',
                                'direccion': 'VENTA', 'precio_entrada': sell_entry,
                                'tp1': tp1_v, 'tp2': tp2_v, 'tp3': tp3_v, 'sl': sl_venta,
                                'score': score_sell,
                                'indicadores': json.dumps(_condiciones_bd),
                                'patron_velas': f"Envolvente:{patron_envolvente_bajista(df)}, Doji:{patron_doji(df)}, StopHunt:{detectar_stop_hunt_bajista(df)}",
                                'version_detector': '15M-SCALP-v2.1'
                            })
                        except Exception as e:
                            logger.error(f"  ⚠️ Error guardando señal: {e}")
                    self.enviar(msg)

            # ── SEÑALES COMPRA — solo FUERTE ──
            if senal_buy_fuerte and not cancelar_buy:
                if self.db and self.db.existe_senal_activa_misma_dir(simbolo_db, 'COMPRA'):
                    logger.info(f"  ℹ️  BUY 15M bloqueada: ya existe señal ACTIVA en {simbolo_db}")
                else:
                    # Timestamp de la vela analizada
                    timestamp_vela = df.index[-1]
                    hora_estudio = timestamp_vela.strftime('%Y-%m-%d %H:%M UTC')
                    hora_envio = datetime.now(timezone.utc).strftime('%H:%M:%S UTC')
                    
                    msg = (f"{_titulo_buy}\n"
                           f"━━━━━━━━━━━━━━━━━━━━\n"
                           f"💰 <b>Precio:</b>    ${round(close, 2)}\n"
                           f"📌 <b>BUY LIMIT:</b> ${buy_entry:.2f}  (spread ${spread:.2f} incluido)\n"
                           f"🛑 <b>Stop Loss:</b> ${round(sl_compra, 2)}\n"
                           f"━━━━━━━━━━━━━━━━━━━━\n"
                           f"🎯 <b>TP1:</b> ${tp1_c}  R:R {rr(buy_entry, sl_compra, tp1_c)}:1\n"
                           f"🎯 <b>TP2:</b> ${tp2_c}  R:R {rr(buy_entry, sl_compra, tp2_c)}:1\n"
                           f"🎯 <b>TP3:</b> ${tp3_c}  R:R {rr(buy_entry, sl_compra, tp3_c)}:1\n"
                           f"━━━━━━━━━━━━━━━━━━━━\n"
                           f"📊 <b>Score:</b> {score_buy}/{max_score}  📉 <b>RSI:</b> {round(rsi, 1)}  📐 <b>ADX:</b> {round(adx, 1)}\n"
                           f"⏱️ <b>TF:</b> 15M\n"
                           f"📅 <b>Estudio:</b> {hora_estudio}\n"
                           f"🕐 <b>Envío:</b> {hora_envio}\n"
                           f"━━━━━━━━━━━━━━━━━━━━\n"
                           f"🔍 <b>Patrones Detectados:</b>\n{diagnostico_patrones}")
                    if _conf_buy:
                        msg += f"\n━━━━━━━━━━━━━━━━━━━━\n{_conf_buy}"
                    if _ctx_htf:
                        msg += _ctx_htf
                    if self.db:
                        try:
                            self._guardar_senal({
                                'timestamp': datetime.now(timezone.utc), 
                                'timestamp_entry': timestamp_vela.isoformat(),  # Hora de la vela
                                'simbolo': simbolo_db,
                                'asset': 'GOLD',
                                'timeframe': '15M',
                                'direccion': 'COMPRA', 'precio_entrada': buy_entry,
                                'tp1': tp1_c, 'tp2': tp2_c, 'tp3': tp3_c, 'sl': sl_compra,
                                'score': score_buy,
                                'indicadores': json.dumps(_condiciones_bd),
                                'patron_velas': f"Envolvente:{patron_envolvente_alcista(df)}, Doji:{patron_doji(df)}, StopHunt:{detectar_stop_hunt_alcista(df)}",
                                'version_detector': '15M-SCALP-v2.1'
                            })
                        except Exception as e:
                            logger.error(f"  ⚠️ Error guardando señal: {e}")
                    self.enviar(msg)
    
        except Exception as e:
            logger.error(f"❌ Error analizando {simbolo}: {e}")



def analizar_simbolo(simbolo, params):
    return GoldDetector15M(simbolo=simbolo, tf_label='15M', params=params, telegram_thread_id=TELEGRAM_THREAD_ID).analizar(simbolo, params)


def main():
    """Función principal para ejecutar el detector."""
    enviar_telegram("🚀 <b>Detector GOLD 15M SCALPING iniciado</b>\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    "⏱️  Análisis cada 2 minutos\n"
                    "⚡ Optimizado para operaciones rápidas\n"
                    "📊 Score mínimo: 3/15")
    ciclo = 0
    while True:
        ciclo += 1        
        # Verificar si hay eventos finalizados para notificar reanudación
        verificar_y_notificar_reanudacion()
        ahora_utc = datetime.now(timezone.utc)
        if ahora_utc.weekday() == 5:
            from datetime import timedelta
            proximo_domingo_18 = (ahora_utc + timedelta(days=1)).replace(
                hour=18, minute=0, second=0, microsecond=0)
            segundos_espera = min((proximo_domingo_18 - ahora_utc).total_seconds(), 3600)
            logger.info(f"[{ahora_utc.strftime('%Y-%m-%d %H:%M')} UTC] 💤 Sábado — mercado cerrado.")
            time.sleep(segundos_espera)
            continue
        logger.info(f"[{ahora_utc.strftime('%Y-%m-%d %H:%M:%S')}] 🔄 CICLO #{ciclo} - GOLD 15M SCALPING")
        for simbolo, params in SIMBOLOS.items():
            analizar_simbolo(simbolo, params)
        logger.info(f"⏳ Esperando {CHECK_INTERVAL // 60} minutos...")
        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()

