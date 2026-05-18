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
            # SCORING SYSTEM — solo EMA + S/R (respaldado por datos: score=4 → 88% WR)
            # ══════════════════════════════════════
            score_sell = 0
            score_buy  = 0
            vol_medio = df['Volume'].iloc[-20:].mean()

            # 1. EMA alineada en dirección (necesario)
            if ema_fast.iloc[-1] < ema_slow.iloc[-1]:
                score_sell += 2
            if ema_fast.iloc[-1] > ema_slow.iloc[-1]:
                score_buy += 2

            # 2. Tendencia general confirmada por EMA lenta vs EMA tendencia
            if close < ema_trend.iloc[-1]:
                score_sell += 1
            else:
                score_buy += 1

            # 3. Precio en zona S/R (necesario)
            if en_zona_resist or aproximando_resistencia:
                score_sell += 2
            if en_zona_soporte or aproximando_soporte:
                score_buy += 2

            # Log informativo (no afecta score)
            logger.info(f"  📊 [15M] Score SELL={score_sell} BUY={score_buy} | "
                        f"EMA {'SELL' if ema_fast.iloc[-1] < ema_slow.iloc[-1] else 'BUY'} | "
                        f"Zona {'RESIST' if en_zona_resist else 'aprox_resist' if aproximando_resistencia else '-'}"
                        f"{'SOPO' if en_zona_soporte else 'aprox_sopo' if aproximando_soporte else ''} | "
                        f"Vol={vol:.0f} (media={vol_medio:.0f})")

            # Umbral: 4 = EMA + S/R alineados → señal. 5 = + confirmación EMA tendencia
            _umbral_fue = 4
            senal_sell_fuerte = score_sell >= _umbral_fue
            senal_buy_fuerte  = score_buy  >= _umbral_fue

            # Variables que el snapshot BD espera (se mantienen para compatibilidad)
            _rsi_baj_3 = _rsi_sub_3 = False
            _micro_vol = 1.0
            _momentum_rec = 0
            dxy_bias = None
            _cot_bias = None
            _oi_bias = None
            canal_alc_roto_15m = canal_baj_roto_15m = False
            linea_sop_canal_15m = linea_res_canal_15m = 0.0
            en_resist_canal_baj_15m = en_sop_canal_alc_15m = False
            linea_res_precio_15m = linea_sop_precio_15m = 0.0
            _rup_sop_15m = _rup_res_15m = False
            _niv_sop_15m = _niv_res_15m = 0.0
            _retest_res_15m = _retest_sop_15m = False
            _niv_retest_res_15m = _niv_retest_sop_15m = 0.0
            _rec_dir_baj_15m = _rec_dir_alc_15m = False
            _precio_dir_baj_15m = _precio_dir_alc_15m = 0.0
            _cuña_desc_15m = _cuña_asc_15m = None
            _t_desc_15m = _s_desc_15m = _t_asc_15m = _s_asc_15m = 0.0
            _dt_15m = _ds_15m = False
            _dt_nivel_15m = _dt_neck_15m = _ds_nivel_15m = _ds_neck_15m = 0.0
            v_rev_alc = v_rev_baj = False
            v_min = v_precio = v_max = v_precio_baj = 0.0

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

            # ── Filtro RSI 5M/1M: evitar entrar cuando el TF inferior está contraposicionado ──
            # SELL: si RSI 5M < 35 el precio ya está sobrevendido en TF corto → no vender
            # BUY:  si RSI 5M > 65 el precio ya está sobrecomprado en TF corto → no comprar
            try:
                _rsi_5m = float(calcular_rsi(df_5m['Close'], 14).iloc[-1])
                if senal_sell_fuerte and _rsi_5m < 35:
                    logger.info(f"  🔴 [15M] SELL bloqueada — RSI 5M sobrevendido ({_rsi_5m:.1f} < 35)")
                    senal_sell_fuerte = False
                if senal_buy_fuerte and _rsi_5m > 65:
                    logger.info(f"  🟢 [15M] BUY bloqueada — RSI 5M sobrecomprado ({_rsi_5m:.1f} > 65)")
                    senal_buy_fuerte = False
                logger.info(f"  📊 [15M] RSI 5M={_rsi_5m:.1f} (filtro TF corto OK)")
            except Exception as _e_rsi5m:
                logger.debug(f"  [15M] No se pudo calcular RSI 5M: {_e_rsi5m}")

            # ── Filtro RSI 1M: bloquea entradas en momentos de impulso extremo en TF 1M ──
            # Umbrales más extremos que 5M (1M es ruidoso) — solo bloquea si hay agotamiento evidente
            # SELL: RSI 1M < 25 → impulso bajista agotado en 1M, posible rebote inmediato
            # BUY:  RSI 1M > 75 → impulso alcista agotado en 1M, posible retroceso inmediato
            try:
                df_1m, _ = get_ohlcv(params['ticker_yf'], period='1d', interval='1m')
                if df_1m is not None and len(df_1m) >= 14:
                    _rsi_1m = float(calcular_rsi(df_1m['Close'], 7).iloc[-1])
                    if senal_sell_fuerte and _rsi_1m < 25:
                        logger.info(f"  🔴 [15M] SELL bloqueada — RSI 1M agotado bajista ({_rsi_1m:.1f} < 25)")
                        senal_sell_fuerte = False
                    if senal_buy_fuerte and _rsi_1m > 75:
                        logger.info(f"  🟢 [15M] BUY bloqueada — RSI 1M agotado alcista ({_rsi_1m:.1f} > 75)")
                        senal_buy_fuerte = False
                    logger.info(f"  📊 [15M] RSI 1M={_rsi_1m:.1f} (filtro 1M OK)")
            except Exception as _e_rsi1m:
                logger.debug(f"  [15M] No se pudo calcular RSI 1M: {_e_rsi1m}")
            if not self.en_sesion_optima():
                logger.info(f"  🌙 [15M] Fuera sesión óptima — señal suprimida (08-21 UTC)")
                senal_sell_fuerte = False
                senal_buy_fuerte  = False

            # ── Filtro ADX mínimo 15M: mercado plano → bloquear señales ─────────
            _ADX_MIN_15M = 18
            if adx < _ADX_MIN_15M:
                logger.info(f"  😴 [15M] ADX {round(adx, 1)} < {_ADX_MIN_15M} — mercado plano, señales bloqueadas")
                senal_sell_fuerte = False
                senal_buy_fuerte  = False

            # ── Filtro zona estricta (score bajo): precio debe estar DENTRO de zona, no solo aproximándose ──
            # Score alto (≥9) ya tiene suficientes confirmaciones → no necesita este filtro
            # Score bajo (5-8) puede disparar solo por "aproximando" → exigir que esté dentro
            _SCORE_ZONA_ESTRICTA = 9
            if senal_sell_fuerte and score_sell < _SCORE_ZONA_ESTRICTA:
                if not en_zona_resist:
                    logger.info(f"  🚫 [15M] SELL score={score_sell} < {_SCORE_ZONA_ESTRICTA}: precio no en zona resist ({close:.2f} vs {zrl:.2f}-{zrh:.2f}) — bloqueada")
                    senal_sell_fuerte = False
            if senal_buy_fuerte and score_buy < _SCORE_ZONA_ESTRICTA:
                if not en_zona_soporte:
                    logger.info(f"  🚫 [15M] BUY score={score_buy} < {_SCORE_ZONA_ESTRICTA}: precio no en zona soporte ({close:.2f} vs {zsl:.2f}-{zsh:.2f}) — bloqueada")
                    senal_buy_fuerte = False

            # Cancelaciones (más estrictas en scalping)
            simbolo_db_15m = f"{simbolo}_15M"
            cancelar_sell = (close < zsh) or (rsi < 30)
            cancelar_buy  = (close > zrh) or (rsi > (75 if adx > 45 else 70))
            # Bloquear si ya hay señal ACTIVA en la MISMA dirección
            if self.db and self.db.existe_senal_activa_misma_dir(simbolo_db_15m, 'VENTA'):
                cancelar_sell = True
                logger.info(f"  🚫 [15M] cancelar_sell=True: VENTA ya activa en BD")
            if self.db and self.db.existe_senal_activa_misma_dir(simbolo_db_15m, 'COMPRA'):
                cancelar_buy = True
                logger.info(f"  🚫 [15M] cancelar_buy=True: COMPRA ya activa en BD")
        
            # ── SL y TP para SCALPING ──
            asm = params['atr_sl_mult']
            spread = params.get('spread', 0.35)  # leído aquí: usado en SL y TPs
            sl_venta  = close + (atr * asm) + spread
            sl_compra = close - (atr * asm) - spread
        
            # Límites de entrada
            # SELL LIMIT: espera que el precio suba hasta la resistencia antes de vender
            offset_pct = params['limit_offset_pct']
            sell_limit = close * (1 + offset_pct / 100)
            sell_entry = round(sell_limit - spread, 2)
            # BUY: entrada a precio de mercado (ask actual) — el close ya está en soporte,
            # poner el limit por debajo garantiza que nunca se ejecuta si el precio rebota
            buy_entry  = round(close + spread, 2)

            # VATR: factor de volumen — amplía TPs en mercados con impulso, los reduce en apáticos
            _vol_avg20  = float(df['Volume'].rolling(20).mean().iloc[-1])
            _vol_last   = float(df['Volume'].iloc[-1])
            _vol_factor = min(max(_vol_last / _vol_avg20, 0.75), 1.50) if _vol_avg20 > 0 else 1.0

            # TPs dinámicos basados en ATR ajustado por volumen (VATR)
            tp1_v = round(sell_entry - atr * params['atr_tp1_mult'] * _vol_factor - spread, 2)
            tp2_v = round(sell_entry - atr * params['atr_tp2_mult'] * _vol_factor - spread, 2)
            tp3_v = round(sell_entry - atr * params['atr_tp3_mult'] * _vol_factor - spread, 2)
            tp1_c = round(buy_entry  + atr * params['atr_tp1_mult'] * _vol_factor + spread, 2)
            tp2_c = round(buy_entry  + atr * params['atr_tp2_mult'] * _vol_factor + spread, 2)
            tp3_c = round(buy_entry  + atr * params['atr_tp3_mult'] * _vol_factor + spread, 2)
        
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
            logger.info(f"  📊 Score SELL: {score_sell}/5 | Score BUY: {score_buy}/5")
            logger.info(f"  🔴 SELL Fuerte:{senal_sell_fuerte}  🟢 BUY Fuerte:{senal_buy_fuerte}")
            logger.info(f"  📉 RSI: {round(rsi, 1)} | ADX: {round(adx, 1)} | ATR: {round(atr, 2)}")
        
            # ── CONTROL DE PÉRDIDAS CONSECUTIVAS (desactivado para backtesting) ──
            # perdidas_consecutivas = self.db.contar_perdidas_consecutivas(f"{simbolo}_15M") if self.db else 0
            # if perdidas_consecutivas >= params['max_perdidas_dia']:
            #     if not (senal_sell_fuerte or senal_buy_fuerte):
            #         logger.warning(f"  ⛔ Trading pausado: {perdidas_consecutivas} pérdidas consecutivas — esperando señal fuerte")
            #         return
            #     logger.info(f"  ✅ Señal fuerte detectada tras {perdidas_consecutivas} pérdidas — reanudando")
        
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
            tf_bias.publicar_scores(simbolo, '15M', score_sell, score_buy, 5)

            # ── Publicar zona activa 15M para MODO CAZA 5M ────────────────────────
            # Umbral bajo (3) = setup incipiente — el 5M confirmará con su propio score
            # TTL 45 min en tf_bias — zona se limpia si el 15M deja de ver el setup
            _UMBRAL_ZONA_15M = 3
            if score_buy >= _UMBRAL_ZONA_15M and not cancelar_buy and self.en_sesion_optima():
                tf_bias.publicar_zona_activa_15m(simbolo, tf_bias.BIAS_BULLISH, {
                    'zsl': zsl, 'zsh': zsh,
                    'buy_limit': buy_entry, 'sl': sl_compra,
                    'tp1': tp1_c, 'tp2': tp2_c, 'tp3': tp3_c,
                    'atr': atr, 'score_15m': score_buy,
                })
                logger.info(f"  🏗️ [15M→5M] Zona BUY publicada — soporte ${zsl:.2f}-${zsh:.2f}, limit ${buy_entry:.2f} (score {score_buy})")
            else:
                tf_bias.limpiar_zona_activa_15m(simbolo, tf_bias.BIAS_BULLISH)

            if score_sell >= _UMBRAL_ZONA_15M and not cancelar_sell and self.en_sesion_optima():
                tf_bias.publicar_zona_activa_15m(simbolo, tf_bias.BIAS_BEARISH, {
                    'zrl': zrl, 'zrh': zrh,
                    'sell_limit': sell_limit, 'sl': sl_venta,
                    'tp1': tp1_v, 'tp2': tp2_v, 'tp3': tp3_v,
                    'atr': atr, 'score_15m': score_sell,
                })
                logger.info(f"  🏗️ [15M→5M] Zona SELL publicada — resistencia ${zrl:.2f}-${zrh:.2f}, limit ${sell_limit:.2f} (score {score_sell})")
            else:
                tf_bias.limpiar_zona_activa_15m(simbolo, tf_bias.BIAS_BEARISH)

            _conf_sell = ""; _conf_buy = ""
            if senal_sell_fuerte:
                _ok, _desc = tf_bias.verificar_confluencia(simbolo, '15M', tf_bias.BIAS_BEARISH, score_sell)
                if not _ok:
                    logger.info(f"  🚫 SELL bloqueada por TF superior: {_desc[:80]}")
                    senal_sell_fuerte = False
                else:
                    _conf_sell = _desc
            if senal_buy_fuerte:
                _ok, _desc = tf_bias.verificar_confluencia(simbolo, '15M', tf_bias.BIAS_BULLISH, score_buy)
                if not _ok:
                    logger.info(f"  🚫 BUY bloqueada por TF superior: {_desc[:80]}")
                    senal_buy_fuerte = False
                else:
                    _conf_buy = _desc

            # ── Filtro de umbral elevado contra-tendencia 4H ─────────────────────────
            # Si la señal va CONTRA el sesgo del 4H, exigir score mínimo 13 (en lugar de 8)
            # para que solo señales muy claras (Stop Hunt + patrón + RSI extremo) pasen.
            _SCORE_CONTRA_4H = 13
            _bias_4h = tf_bias.obtener_sesgo(simbolo, '4H')
            _b4h_ct = _bias_4h['bias'] if _bias_4h else tf_bias.BIAS_NEUTRAL
            _contra_tend_sell = (_b4h_ct == tf_bias.BIAS_BULLISH)  # SELL vs 4H alcista
            _contra_tend_buy  = (_b4h_ct == tf_bias.BIAS_BEARISH)  # BUY  vs 4H bajista
            if _bias_4h:
                _b4h = _bias_4h['bias']
                if senal_sell_fuerte and _b4h == tf_bias.BIAS_BULLISH and score_sell < _SCORE_CONTRA_4H:
                    logger.info(f"  🚫 SELL contra 4H BULLISH bloqueada: score {score_sell} < {_SCORE_CONTRA_4H} (contra-tendencia)")
                    senal_sell_fuerte = False
                if senal_buy_fuerte and _b4h == tf_bias.BIAS_BEARISH and score_buy < _SCORE_CONTRA_4H:
                    logger.info(f"  🚫 BUY contra 4H BEARISH bloqueada: score {score_buy} < {_SCORE_CONTRA_4H} (contra-tendencia)")
                    senal_buy_fuerte = False

            # ── MODO CAZA: activar si hay zona activa del 1H ───────────────────────────
            # El detector 1H publicó una zona activa (score≥5). Si el precio
            # entra en esa zona y score_15M≥4, disparar señal con umbral reducido.
            _zona_1h_buy  = tf_bias.obtener_zona_activa(simbolo, tf_bias.BIAS_BULLISH)
            _zona_1h_sell = tf_bias.obtener_zona_activa(simbolo, tf_bias.BIAS_BEARISH)
            _modo_caza_buy  = False
            _modo_caza_sell = False
            _UMBRAL_CAZA    = 12  # el 1H confirma el contexto — mínimo MEDIA (antes: 8)

            if _zona_1h_buy and not senal_buy_fuerte and self.en_sesion_optima() and adx >= _ADX_MIN_15M:
                _tol_1h = _zona_1h_buy['atr'] * 0.6
                _en_zona_1h = (_zona_1h_buy['zsl'] - _tol_1h <= close <= _zona_1h_buy['zsh'] + _tol_1h)
                if _en_zona_1h and score_buy >= _UMBRAL_CAZA:
                    senal_buy_fuerte = True
                    _modo_caza_buy   = True
                    _conf_buy = (f"⚡ <b>Setup 1H / Entrada 15M</b>\n"
                                 f"📍 Zona 1H soporte: ${_zona_1h_buy['zsl']:.2f}–${_zona_1h_buy['zsh']:.2f} | Score 1H: {_zona_1h_buy['score_1h']}/21\n"
                                 f"📌 Limit 1H: ${_zona_1h_buy['buy_limit']:.2f} → ajustado 15M: ${buy_entry:.2f}")
                    logger.info(f"  🎯 [15M] MODO CAZA BUY — zona 1H activa (score {score_buy}) → señal activada")

            if _zona_1h_sell and not senal_sell_fuerte and self.en_sesion_optima() and adx >= _ADX_MIN_15M:
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

            # ── GUARD: distancia mínima SL-entrada (evita SL=entry cuando ATR≈offset) ──
            SL_MIN_DIST = 1.0   # mínimo 1$ de distancia entre SL y precio de entrada
            if abs(sl_venta  - sell_entry) < SL_MIN_DIST:
                logger.warning(
                    f'  ⛔ SELL bloqueada: SL ({sl_venta:.2f}) demasiado cerca de '
                    f'entrada ({sell_entry:.2f}) — dist={abs(sl_venta - sell_entry):.3f} < {SL_MIN_DIST}$'
                )
                cancelar_sell = True
            if abs(sl_compra - buy_entry)  < SL_MIN_DIST:
                logger.warning(
                    f'  ⛔ BUY bloqueada: SL ({sl_compra:.2f}) demasiado cerca de '
                    f'entrada ({buy_entry:.2f}) — dist={abs(sl_compra - buy_entry):.3f} < {SL_MIN_DIST}$'
                )
                cancelar_buy = True

            # ── FILTRO R:R MÍNIMO 1.6 (Scalping 15M) ──
            RR_MINIMO = 1.6
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

            # ── Re-entrada: ¿hubo un cierre reciente (TP/SL) en la misma dirección? ──
            _rentry_sell = self.db.existe_senal_cerrada_reciente(simbolo_db, 'VENTA',  horas=4) if self.db else None
            _rentry_buy  = self.db.existe_senal_cerrada_reciente(simbolo_db, 'COMPRA', horas=4) if self.db else None
            _sfx_sell = (
                f"\n━━━━━━━━━━━━━━━━━━━━\n"
                f"♻️ <b>RE-ENTRADA</b> — Trade #{_rentry_sell['id']} cerrado ({_rentry_sell['estado']})\n"
                f"   Entrada anterior: ${_rentry_sell['precio_entrada']:.2f} | TP1: ${_rentry_sell['tp1']:.2f}"
            ) if _rentry_sell else ""
            _sfx_buy = (
                f"\n━━━━━━━━━━━━━━━━━━━━\n"
                f"♻️ <b>RE-ENTRADA</b> — Trade #{_rentry_buy['id']} cerrado ({_rentry_buy['estado']})\n"
                f"   Entrada anterior: ${_rentry_buy['precio_entrada']:.2f} | TP1: ${_rentry_buy['tp1']:.2f}"
            ) if _rentry_buy else ""

            # ── ANTI-TRAMPA: Stop Hunt contralateral (gold 15M) ─────────────────────
            _sh_baj_activo = detectar_stop_hunt_bajista(df)
            _sh_alc_activo = detectar_stop_hunt_alcista(df)
            if senal_sell_fuerte and _sh_alc_activo:
                logger.warning(
                    f"  🚫 [ANTI-TRAMPA 15M] SELL bloqueada: Stop Hunt ALCISTA activo "
                    f"(el barrido bajista fue una trampa — señal real es BUY)"
                )
                senal_sell_fuerte = False
            if senal_buy_fuerte and _sh_baj_activo:
                logger.warning(
                    f"  🚫 [ANTI-TRAMPA 15M] BUY bloqueada: Stop Hunt BAJISTA activo "
                    f"(el barrido alcista fue una trampa — señal real es SELL)"
                )
                senal_buy_fuerte = False
            _warn_consenso_sell = tf_bias.detectar_consenso_trampa(simbolo, '15M', tf_bias.BIAS_BEARISH)
            _warn_consenso_buy  = tf_bias.detectar_consenso_trampa(simbolo, '15M', tf_bias.BIAS_BULLISH)

            # ── Construir diagnóstico de patrones detectados ──
            patrones_detectados = []
            if patron_envolvente_bajista(df):
                patrones_detectados.append("📉 Envolvente Bajista")
            if patron_envolvente_alcista(df):
                patrones_detectados.append("📈 Envolvente Alcista")
            if patron_doji(df):
                patrones_detectados.append("⚪ Doji (indecisión)")
            if _sh_baj_activo:
                patrones_detectados.append("🎯 Stop Hunt Bajista (trampa alcista)")
            if _sh_alc_activo:
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
                           + (f"⚠️ <i>Solo TP1 — contra tendencia 4H ({_b4h_ct})</i>\n"
                              if _contra_tend_sell else
                              f"🎯 <b>TP2:</b> ${tp2_v}  R:R {rr(sell_entry, sl_venta, tp2_v)}:1\n"
                              f"🎯 <b>TP3:</b> ${tp3_v}  R:R {rr(sell_entry, sl_venta, tp3_v)}:1\n")
                           + 
                           f"━━━━━━━━━━━━━━━━━━━━\n"
                           f"📊 <b>Score:</b> {score_sell}/5  📉 <b>RSI:</b> {round(rsi, 1)}  📐 <b>ADX:</b> {round(adx, 1)}\n"
                           f"⏱️ <b>TF:</b> 15M\n"
                           f"📅 <b>Estudio:</b> {hora_estudio}\n"
                           f"🕐 <b>Envío:</b> {hora_envio}\n"
                           f"━━━━━━━━━━━━━━━━━━━━\n"
                           f"🔍 <b>Patrones Detectados:</b>\n{diagnostico_patrones}")
                    if _conf_sell:
                        msg += f"\n━━━━━━━━━━━━━━━━━━━━\n{_conf_sell}"
                    if _ctx_htf:
                        msg += _ctx_htf
                    msg += _sfx_sell
                    if self.db:
                        try:
                            self._guardar_senal({
                                'timestamp': datetime.now(timezone.utc), 
                                'timestamp_entry': timestamp_vela.isoformat(),  # Hora de la vela
                                'simbolo': simbolo_db,
                                'asset': 'GOLD',
                                'timeframe': '15M',
                                'direccion': 'VENTA', 'precio_entrada': sell_entry,
                                'tp1': tp1_v,
                                'tp2': None if _contra_tend_sell else tp2_v,
                                'tp3': None if _contra_tend_sell else tp3_v,
                                'sl': sl_venta,
                                'score': score_sell,
                                'indicadores': json.dumps(_condiciones_bd),
                                'patron_velas': f"Envolvente:{patron_envolvente_bajista(df)}, Doji:{patron_doji(df)}, StopHunt:{_sh_baj_activo}",
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
                           + (f"⚠️ <i>Solo TP1 — contra tendencia 4H ({_b4h_ct})</i>\n"
                              if _contra_tend_buy else
                              f"🎯 <b>TP2:</b> ${tp2_c}  R:R {rr(buy_entry, sl_compra, tp2_c)}:1\n"
                              f"🎯 <b>TP3:</b> ${tp3_c}  R:R {rr(buy_entry, sl_compra, tp3_c)}:1\n")
                           + 
                           f"━━━━━━━━━━━━━━━━━━━━\n"
                           f"📊 <b>Score:</b> {score_buy}/5  📉 <b>RSI:</b> {round(rsi, 1)}  📐 <b>ADX:</b> {round(adx, 1)}\n"
                           f"⏱️ <b>TF:</b> 15M\n"
                           f"📅 <b>Estudio:</b> {hora_estudio}\n"
                           f"🕐 <b>Envío:</b> {hora_envio}\n"
                           f"━━━━━━━━━━━━━━━━━━━━\n"
                           f"🔍 <b>Patrones Detectados:</b>\n{diagnostico_patrones}")
                    if _conf_buy:
                        msg += f"\n━━━━━━━━━━━━━━━━━━━━\n{_conf_buy}"
                    if _warn_consenso_buy:
                        msg += f"\n━━━━━━━━━━━━━━━━━━━━\n⚠️ <b>ALERTA TRAMPA:</b> {_warn_consenso_buy}"
                    if _ctx_htf:
                        msg += _ctx_htf
                    msg += _sfx_buy
                    if self.db:
                        try:
                            self._guardar_senal({
                                'timestamp': datetime.now(timezone.utc), 
                                'timestamp_entry': timestamp_vela.isoformat(),  # Hora de la vela
                                'simbolo': simbolo_db,
                                'asset': 'GOLD',
                                'timeframe': '15M',
                                'direccion': 'COMPRA', 'precio_entrada': buy_entry,
                                'tp1': tp1_c,
                                'tp2': None if _contra_tend_buy else tp2_c,
                                'tp3': None if _contra_tend_buy else tp3_c,
                                'sl': sl_compra,
                                'score': score_buy,
                                'indicadores': json.dumps(_condiciones_bd),
                                'patron_velas': f"Envolvente:{patron_envolvente_alcista(df)}, Doji:{patron_doji(df)}, StopHunt:{_sh_alc_activo}",
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

