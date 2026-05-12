import os
from services import tf_bias
from services.dxy_bias import get_dxy_bias, ajustar_score_por_dxy
from services.cot_bias import get_cot_bias, ajustar_score_por_cot
from services.yield_bias import get_yield_bias, ajustar_score_por_yield
from services.open_interest import get_oi_bias, ajustar_score_por_oi
from services.economic_calendar import obtener_aviso_macro, debe_bloquear_trading, enviar_alerta_bloqueo, verificar_y_notificar_reanudacion
from services.news_monitor import obtener_sesgo_actual
from adapters.data_provider import get_ohlcv

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

from adapters.database import get_db
from core.base_detector_gold import GoldBaseDetector as BaseDetector
from core.base_detector import simbolo_a_nombre
from core.predictor import GoldPredictor
import logging
logger = logging.getLogger('bottrading')

db = get_db()

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
        'atr_sl_mult':        1.5,    # evitar SL prematuro por ruido 1H (datos: SL medio wins=4.3 vs losses=13.4)
        'atr_tp1_mult':       1.5,    # TP1: 1.5× ATR (intradía alcanzable)
        'atr_tp2_mult':       2.5,    # TP2: 2.5× ATR
        'atr_tp3_mult':       4.0,    # TP3: 4.0× ATR (objetivo ambicioso)
        'vol_mult':           1.2,
        'spread':             0.35,         # Spread típico broker CFD (XAUUSD)
    }
}
ultimo_analisis  = {}
# Estado previo de pullback por símbolo — permite edge-trigger (solo dispara al
# activarse, no mientras persiste). Se resetea al resolverse el pullback.
_estado_pullback: dict = {}  # clave → bool (True = pullback activo en ciclo anterior)

# Instancia singleton — persiste alertas_enviadas entre ciclos
_detector_instance: 'GoldDetector1H | None' = None
_predictor_1h_buy = GoldPredictor(tf='1H', direccion='COMPRA')
_predictor_1h_sell = GoldPredictor(tf='1H', direccion='VENTA')
_last_ml_retrain_1h = 0.0


from core.indicators import (
    calcular_rsi, calcular_ema, calcular_atr,
    calcular_bollinger_bands, calcular_macd, calcular_obv, calcular_adx,
    detectar_evening_star, detectar_morning_star,
    detectar_canal_roto, calcular_sr_multiples,
    detectar_ruptura_soporte_horizontal, detectar_ruptura_resistencia_horizontal,
    detectar_cuña_descendente, detectar_cuña_ascendente,
    detectar_precio_en_fibonacci, detectar_rebote_alcista, detectar_rebote_bajista,
    calcular_fibonacci,
    detectar_doble_techo, detectar_doble_suelo,
    detectar_v_reversal_alcista, detectar_v_reversal_bajista,
    detectar_hch, detectar_hch_invertido,
    detectar_triangulo,
    detectar_bandera_banderin,
    calcular_pivots_diarios, evaluar_precio_vs_pivots,
    calcular_aceleracion_rsi, calcular_micro_volatilidad, calcular_momentum_reciente,
)



def analizar(simbolo, params):
    global _detector_instance
    if _detector_instance is None:
        _detector_instance = GoldDetector1H(
            simbolo=simbolo, tf_label='1H', params=params,
            telegram_thread_id=TELEGRAM_THREAD_ID
        )
    return _detector_instance.analizar(simbolo, params)



class GoldDetector1H(BaseDetector):
    def analizar(self, simbolo, params):
        simbolo_db = f"{simbolo}_1H"

        # ── Bloqueo por eventos críticos (FOMC, Powell, NFP, CPI) ──
        bloqueado, desc_evento, minutos = debe_bloquear_trading(90)
        if bloqueado:
            enviar_alerta_bloqueo(desc_evento, minutos, ['1H'])
            logger.warning(f"🚫 [1H] Trading bloqueado por evento: {desc_evento}")
            return
        
        # ── Aviso calendario económico (eventos menores) ──
        self.aviso_macro = obtener_aviso_macro(60, '1H', simbolo)

        logger.info(f"\n🔍 Analizando {simbolo} [1H intradía]...")

        try:
            # NOTA: TwelveData tiene problemas con el endpoint 1H directo (ATR ~$110 vs $20 real)
            # Solución: descargar 5M (confiable) y resamplear a 1H
            df_5m, is_delayed = get_ohlcv(params['ticker_yf'], period='7d', interval='5m')
            if df_5m.empty or len(df_5m) < 100:
                logger.warning(f"⚠️ Datos insuficientes para {simbolo} 1H")
                return
            
            # Resample 5M → 1H (más confiable que endpoint 1H directo de TD)
            df = df_5m.resample('1h').agg({
                'Open': 'first', 'High': 'max', 'Low': 'min', 'Close': 'last', 'Volume': 'sum'
            }).dropna()
            
            if df.empty or len(df) < 80:
                logger.warning(f"⚠️ Datos insuficientes para {simbolo} 1H")
                return
        except Exception as e:
            logger.error(f"❌ Error descargando {simbolo}: {e}")
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

        # ── Pivots diarios (sesión NY anterior) ──────────────────────────────
        try:
            _df_1d, _ = get_ohlcv(params['ticker_yf'], period='5d', interval='1d')
            _pivots_1h = calcular_pivots_diarios(_df_1d)
        except Exception:
            _pivots_1h = {}

        row  = df.iloc[-2]; prev = df.iloc[-3]; p2 = df.iloc[-4]
        close = row['Close']; high = row['High']; low = row['Low']; open_ = row['Open']; vol = row['Volume']
        rsi = row['rsi']; rsi_prev = prev['rsi']

        # ── Guardia: ATR anómalo indica datos sucios (p.ej. 1D colado en cache 1H) ──
        # ATR 1H para XAUUSD normal: $5-$40. Si supera $80 los TPs/SL son erróneos.
        _atr_raw = float(df['atr'].iloc[-2])
        if _atr_raw > 80:
            logger.warning(f"  ⚠️ [1H] ATR anómalo ({_atr_raw:.1f}) — posible contaminación de datos, saltando ciclo")
            logger.warning(f"  🧹 [1H] Limpiando cache memoria y BD contaminada...")
            
            # Invalidar cache memoria para forzar recarga limpia en el siguiente ciclo
            from adapters.data_provider import _intraday_cache, _intraday_cache_lock
            with _intraday_cache_lock:
                claves = [k for k in _intraday_cache if k[0] == params['ticker_yf']]
                for k in claves:
                    _intraday_cache.pop(k, None)
                logger.warning(f"  🗑️ [1H] Cache memoria limpiado ({len(claves)} entradas)")
            
            # Purgar datos de BD para 1H — forzará descarga fresca desde TwelveData
            try:
                self.db.purgar_velas_antiguas(params['ticker_yf'], '1h', dias_max=0)
                logger.warning(f"  🗑️ [1H] BD limpiada — próximo ciclo descargará datos frescos")
            except Exception as e:
                logger.error(f"  ⚠️ [1H] Error limpiando BD: {e}")
            
            return
        ema_fast = row['ema_fast']; ema_slow = row['ema_slow']; ema_trend = row['ema_trend']
        atr = row['atr']; vol_avg = row['vol_avg']
        atr_media = float(df['atr'].rolling(20).mean().iloc[-2])
        bb_upper = row['bb_upper']; bb_lower = row['bb_lower']
        macd = row['macd']; macd_signal = row['macd_signal']
        macd_hist = row['macd_hist']; macd_hist_prev = prev['macd_hist']
        obv = row['obv']; obv_prev = prev['obv']; obv_ema = row['obv_ema']
        adx = row['adx']; di_plus = row['di_plus']; di_minus = row['di_minus']
        body = row['body']; upper_wick = row['upper_wick']; lower_wick = row['lower_wick']
        total_range = row['total_range']; is_bearish = row['is_bearish']; is_bullish = row['is_bullish']

        # df.iloc[:-1]: excluir vela viva para que close = última vela cerrada
        _df_cerrado = df.iloc[:-1]
        zrl, zrh, zsl, zsh = self.calcular_zonas_sr(_df_cerrado, atr, params['sr_lookback'], params['sr_zone_mult'])

        # ── Vela viva (en formación) — para detección de rebotes/rechazos en tiempo real ──
        row_live   = df.iloc[-1]
        close_live = float(row_live['Close'])
        low_live   = float(row_live['Low'])
        high_live  = float(row_live['High'])
        open_live  = float(row_live['Open'])

        # ── Niveles S/R múltiples para TPs estructurales ──────────────────────────
        soportes_sr, resistencias_sr = calcular_sr_multiples(
            _df_cerrado, atr, params['sr_lookback'], params['sr_zone_mult'], n_niveles=5
        )
        # Niveles intermedios wing=2: captura pivotes en tendencias sin pullbacks profundos
        _sop_interm_1h, _res_interm_1h = calcular_sr_multiples(
            _df_cerrado, atr, lookback=params['sr_lookback'], n_niveles=8, wing=2)

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
            logger.info(f"  🔻 Canal alcista ROTO — sesgo bajista reforzado (soporte canal ${linea_soporte_canal:.2f})")
        if canal_bajista_roto:
            logger.info(f"  🔺 Canal bajista ROTO — sesgo alcista reforzado (resist canal ${linea_resist_canal:.2f})")

        tol  = round(atr * 0.5, 2)   # tolerancia dinámica: 50% del ATR (ampliado para capturar toques de mecha)
        lop = params['limit_offset_pct']; cd = params['cancelar_dist']
        av   = params['anticipar_velas']; vm = params['vol_mult']
        rsms = params['rsi_min_sell']; rsmb = params['rsi_max_buy']; asm = params['atr_sl_mult']
        logger.info(f"  📍 Zonas auto — Resist: ${zrl:.1f}-${zrh:.1f} | Soporte: ${zsl:.1f}-${zsh:.1f}")

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

        # Ajuste de spread del broker: BUY paga ask (bid+spread), SELL cobra bid (bid-spread)
        spread = params.get('spread', 0.35)
        sell_entry = round(sell_limit - spread, 2)
        buy_entry  = round(buy_limit  + spread, 2)

        # Para SELL: SL en último swing HIGH por encima de la entrada + 0.3×ATR buffer
        # Cap: si el swing está muy lejos, usar el techo de zona + buffer (invalidación natural)
        sl_zona_sell = round(zrh + atr * 0.5, 2)
        sl_swing_sell_candidates = [v for v in swing_h_vals if v > sell_entry]
        if sl_swing_sell_candidates:
            sl_venta = round(min(sl_swing_sell_candidates) + atr * 0.3, 2)
        else:
            sl_venta = round(sell_entry + atr * asm, 2)  # fallback ATR
        sl_venta = min(sl_venta, sl_zona_sell)  # nunca más lejos que la zona rota

        # Para BUY: SL en último swing LOW por debajo de la entrada - 0.3×ATR buffer
        # Cap: si el swing está muy lejos, usar el suelo de zona + buffer (invalidación natural)
        sl_zona_buy = round(zsl - atr * 0.5, 2)
        sl_swing_buy_candidates = [v for v in swing_l_vals if v < buy_entry]
        if sl_swing_buy_candidates:
            sl_compra = round(max(sl_swing_buy_candidates) - atr * 0.3, 2)
        else:
            sl_compra = round(buy_entry - atr * asm, 2)   # fallback ATR
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

        def _recortar_tp_venta(tp_objetivo, tp_anterior, soportes, atr):
            """
            VENTA (precio baja): ajusta tp_objetivo asegurando que:
              1. Esté al menos sep_min (0.4×ATR) por debajo de tp_anterior.
                 Si no lo está (e.g. TP2 = mismo nivel que TP1 por bug de índice,
                 o fallback ATR calculado desde entry queda por encima de TP1),
                 toma el primer soporte significativo por debajo de tp_anterior.
              2. No haya un soporte bloqueante entre tp_anterior y tp_objetivo
                 (el precio lo tocaría antes de llegar). Si lo hay, recorta al soporte
                 más cercano a tp_anterior (el que toca primero bajando).
            """
            sep_min = atr * 0.4
            # ── Guardia: tp_objetivo debe ser menor que tp_anterior ──────────────
            if tp_objetivo >= tp_anterior - sep_min:
                siguientes = sorted(
                    [s for s in soportes if s < tp_anterior - sep_min], reverse=True
                )
                tp_objetivo = siguientes[0] if siguientes else tp_anterior - atr * 1.0
            # ── Recorte: ¿hay un soporte bloqueante entre ambos? ─────────────────
            # Excluye niveles demasiado cercanos a tp_anterior (dentro de sep_min)
            blockers = sorted(
                [s for s in soportes if tp_objetivo < s <= tp_anterior - sep_min],
                reverse=True   # mayor primero = más cercano a tp_anterior = precio lo toca primero
            )
            return round(blockers[0], 2) if blockers else round(tp_objetivo, 2)

        def _recortar_tp_compra(tp_objetivo, tp_anterior, resistencias, atr):
            """
            COMPRA (precio sube): análogo a _recortar_tp_venta pero hacia arriba.
            Recorta tp_objetivo si hay una resistencia bloqueante entre tp_anterior
            y tp_objetivo (la más cercana a tp_anterior = la que el precio toca primero).
            """
            sep_min = atr * 0.4
            # ── Guardia ───────────────────────────────────────────────────────────
            if tp_objetivo <= tp_anterior + sep_min:
                siguientes = sorted(
                    [r for r in resistencias if r > tp_anterior + sep_min]
                )
                tp_objetivo = siguientes[0] if siguientes else tp_anterior + atr * 1.0
            # ── Recorte ───────────────────────────────────────────────────────────
            blockers = sorted(
                [r for r in resistencias if tp_anterior + sep_min <= r < tp_objetivo]
            )   # menor primero = más cercano a tp_anterior = precio lo toca primero subiendo
            return round(blockers[0], 2) if blockers else round(tp_objetivo, 2)

        # VATR: factor de volumen — amplía TPs en mercados con impulso, los reduce en apáticos
        _vol_avg20  = float(df['vol_avg'].iloc[-1])
        _vol_last   = float(df['Volume'].iloc[-1])
        _vol_factor = min(max(_vol_last / _vol_avg20, 0.75), 1.50) if _vol_avg20 > 0 else 1.0

        tp1_v = _tp1_viable_sell(soportes_sr, sell_entry, sl_venta, 1.2,
                                 sell_entry - atr * params['atr_tp1_mult'] * _vol_factor)
        tp2_v = _recortar_tp_venta(
                    _tp_desde_sr(soportes_sr, 2, sell_entry - atr * params['atr_tp2_mult'] * _vol_factor),
                    tp1_v, soportes_sr, atr)
        tp3_v = _recortar_tp_venta(
                    _tp_desde_sr(soportes_sr, 3, sell_entry - atr * params['atr_tp3_mult'] * _vol_factor),
                    tp2_v, soportes_sr, atr)
        tp1_c = _tp1_viable_buy(resistencias_sr, buy_entry, sl_compra, 1.2,
                                buy_entry + atr * params['atr_tp1_mult'] * _vol_factor)
        _resis_sobre = sorted([v for v in resistencias_sr if v > buy_entry])
        tp2_c = _recortar_tp_compra(
                    _tp_desde_sr(_resis_sobre, 2, buy_entry + atr * params['atr_tp2_mult'] * _vol_factor),
                    tp1_c, resistencias_sr, atr)
        tp3_c = _recortar_tp_compra(
                    _tp_desde_sr(_resis_sobre, 3, buy_entry + atr * params['atr_tp3_mult'] * _vol_factor),
                    tp2_c, resistencias_sr, atr)

        # Ajuste spread: SL más amplio y TPs más alejados para reflejar costo real de cierre
        sl_venta  = round(sl_venta  + spread, 2)
        sl_compra = round(sl_compra - spread, 2)
        tp1_v = round(tp1_v - spread, 2)
        tp2_v = round(tp2_v - spread, 2)
        tp3_v = round(tp3_v - spread, 2)
        tp1_c = round(tp1_c + spread, 2)
        tp2_c = round(tp2_c + spread, 2)
        tp3_c = round(tp3_c + spread, 2)

        avg_candle_range    = df['total_range'].iloc[-6:-1].mean()
        aproximando_resist  = (zrl - close > 0 and zrl - close < avg_candle_range * av and close > float(df['Close'].iloc[-5]))
        aproximando_soporte = (close - zsh > 0 and close - zsh < avg_candle_range * av and close < float(df['Close'].iloc[-5]))
        en_zona_resist      = (high >= zrl - tol) and (high <= zrh + tol)
        en_zona_soporte     = (low  >= zsl - tol) and (low  <= zsh + tol)
        # Zonas extendidas: incluye niveles intermedios (wing=2) que la zona principal no cubre
        _en_resist_sr_1h    = any(abs(high - r) <= tol for r in _res_interm_1h)
        _en_sop_sr_1h       = any(abs(low  - s) <= tol for s in _sop_interm_1h)
        en_zona_resist_any  = en_zona_resist or _en_resist_sr_1h
        en_zona_soporte_any = en_zona_soporte or _en_sop_sr_1h
        cancelar_sell       = close > zrh * (1 + cd / 100)
        cancelar_buy        = close < zsl * (1 - cd / 100)
        # Bloquear si ya hay señal ACTIVA en la MISMA dirección (no duplicar orden)
        if self.db and self.db.existe_senal_activa_misma_dir(simbolo_db, 'VENTA'):
            cancelar_sell = True
            logger.info(f"  🚫 [1H] cancelar_sell=True: VENTA ya activa en BD para {simbolo_db}")
        if self.db and self.db.existe_senal_activa_misma_dir(simbolo_db, 'COMPRA'):
            cancelar_buy = True
            logger.info(f"  🚫 [1H] cancelar_buy=True: COMPRA ya activa en BD para {simbolo_db}")
        # Bloquear si hay señal ACTIVA en dirección contraria
        if self.db and self.db.existe_senal_activa_opuesta(simbolo_db, 'VENTA'):
            cancelar_buy = True
            logger.info(f"  🚫 [1H] cancelar_buy=True: VENTA activa en BD para {simbolo_db}")
        if self.db and self.db.existe_senal_activa_opuesta(simbolo_db, 'COMPRA'):
            cancelar_sell = True
            logger.warning(f"  🚫 [1H] cancelar_sell=True: COMPRA activa en BD para {simbolo_db}")

        # ── Detección en vela VIVA: rebote de soporte / rechazo de resistencia ────
        # Señales LIVE desactivadas — solo velas cerradas
        rebote_soporte_live = False
        rechazo_resist_live = False

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
            logger.info(f"  🎯 RETEST CANAL SELL detectado — línea ${linea_soporte_canal:.2f}, precio ${close:.2f}")
        if retest_canal_buy:
            logger.info(f"  🎯 RETEST CANAL BUY detectado — línea ${linea_resist_canal:.2f}, precio ${close:.2f}")

        # ── PREDICCIÓN ANTICIPADA ML ──────────────────────────────────────────
        _pred_features = {}
        _prob_buy = 0.0
        _prob_sell = 0.0
        _etiq_buy = 'NEUTRO'
        _etiq_sell = 'NEUTRO'
        try:
            _pred_features = _predictor_1h_buy.calcular_features_predictivos(
                df, zsl, zsh, zrl, zrh, atr
            )
        except Exception as e:
            logger.debug(f"  [ML 1H] Features predictivos no disponibles: {e}")
        try:
            _prob_buy, _etiq_buy = _predictor_1h_buy.predecir(_pred_features)
        except Exception as e:
            logger.debug(f"  [ML 1H] Predicción BUY no disponible: {e}")
            _prob_buy = 0.0
            _etiq_buy = 'NEUTRO'
        try:
            _prob_sell, _etiq_sell = _predictor_1h_sell.predecir(_pred_features)
        except Exception as e:
            logger.debug(f"  [ML 1H] Predicción SELL no disponible: {e}")
            _prob_sell = 0.0
            _etiq_sell = 'NEUTRO'

        # ── SCORING VENTA — solo EMA + S/R ─────────────────────────────────
        emas_bajistas = ema_fast < ema_slow
        bajo_ema200   = close < ema_trend
        adx_lateral   = adx < 20

        score_sell = (
            (2 if en_zona_resist_any else 0) +   # precio en nivel S/R de resistencia
            (2 if emas_bajistas      else 0) +   # EMA rápida por debajo de EMA lenta
            (1 if bajo_ema200        else 0)      # precio bajo EMA 200 (tendencia bajista)
        )
        score_buy = 0
        # ── Ruptura horizontal directa (sin retest) 1H ─────────────────────
        _lkb_1h = params.get('sr_lookback', 100)
        _rup_sop_1h, _niv_sop_1h = detectar_ruptura_soporte_horizontal(
            df, atr, lookback=_lkb_1h, wing=3)
        _rup_res_1h, _niv_res_1h = detectar_ruptura_resistencia_horizontal(
            df, atr, lookback=_lkb_1h, wing=3)
        if _rup_sop_1h:
            score_sell += 4
            logger.info(f"  💥 [1H] RUPTURA SOPORTE ${_niv_sop_1h:.2f} — +4 pts SELL")
        if _rup_res_1h:
            score_buy += 4
            logger.info(f"  💥 [1H] RUPTURA RESISTENCIA ${_niv_res_1h:.2f} — +4 pts BUY")
        # S/R intermedia: proximidad a nivel no detectado por wing=3
        if _en_resist_sr_1h and not en_zona_resist:
            _nsr = min(_res_interm_1h, key=lambda r: abs(high - r))
            score_sell += 2
            logger.info(f"  📌 [1H] Precio en resistencia S/R intermedia ${_nsr:.2f} — +2 pts SELL")
        if _en_sop_sr_1h and not en_zona_soporte:
            _nsr2 = min(_sop_interm_1h, key=lambda s: abs(low - s))
            score_buy += 2
            logger.info(f"  📌 [1H] Precio en soporte S/R intermedio ${_nsr2:.2f} — +2 pts BUY")

        # ── Cuña descendente / ascendente (1H) — solo detección para snapshot ──
        _lkb_cuña_1h = min(params.get('sr_lookback', 100), 80)
        _cuña_desc_1h, _t_desc_1h, _s_desc_1h = detectar_cuña_descendente(
            df, atr, lookback=_lkb_cuña_1h, wing=3, max_amplitud_pct=0.025)
        _cuña_asc_1h, _t_asc_1h, _s_asc_1h = detectar_cuña_ascendente(
            df, atr, lookback=_lkb_cuña_1h, wing=3, max_amplitud_pct=0.025)

        # ── Doble Techo / Doble Suelo (1H) ──────────────────────────────────────
        _dt_1h, _dt_nivel_1h, _dt_neck_1h = detectar_doble_techo(
            df, atr, lookback=_lkb_1h, tol_mult=0.6)
        _ds_1h, _ds_nivel_1h, _ds_neck_1h = detectar_doble_suelo(
            df, atr, lookback=_lkb_1h, tol_mult=0.6)

        # Variables para compatibilidad con snapshot y mensajes
        v_rev_alc_1h = v_rev_baj_1h = False
        v_min_1h = v_precio_1h = v_max_1h = v_precio_baj_1h = 0.0

        # ── SCORING COMPRA — solo EMA + S/R ────────────────────────────────
        emas_alcistas = ema_fast > ema_slow
        sobre_ema200  = close > ema_trend

        score_buy += (
            (2 if en_zona_soporte_any else 0) +   # precio en nivel S/R de soporte
            (2 if emas_alcistas       else 0) +   # EMA rápida por encima de EMA lenta
            (1 if sobre_ema200        else 0)      # precio sobre EMA 200 (tendencia alcista)
        )

        logger.info(f"  📊 [1H] Score SELL={score_sell} BUY={score_buy} | "
                    f"EMA {'SELL' if emas_bajistas else 'BUY'} | "
                    f"Zona {'RESIST' if en_zona_resist_any else '-'}/{'SOPO' if en_zona_soporte_any else '-'}")

        # Variables para compatibilidad con snapshot y mensajes
        _fib_nivel_1h = _fib_precio_1h = None
        _fib_tend_1h = None
        _fib_data_1h = None
        _todos_sop_1h = list(_sop_interm_1h)
        _todas_res_1h = list(_res_interm_1h)
        _rebote_baj_1h = _rebote_alc_1h = False
        _desc_baj_1h = _desc_alc_1h = ''
        dxy_bias = None
        _cot_bias = _cot_ratio = None
        _yield_bias = _yield_val = _yield_ma = None
        _oi_bias = None
        _noticias = {}
        _sesgo_news = 'ESPERAR'
        _sesgo_etiq = 'NEUTRAL'
        _sesgo_score = 0.0
        _vol_bajo = False
        # Patrones velas eliminados — stubs para mensajes de Telegram y BD
        evening_star = morning_star = False
        shooting_star = hammer = False
        vela_rechazo = vela_rebote = False
        intento_rotura_fallido = intento_caida_fallido = False

        # ── Contexto HTF (solo para filtro obligatorio HTF) ──────────────────
        _bias_1d   = tf_bias.obtener_sesgo(simbolo, '1D')
        _bias_4h   = tf_bias.obtener_sesgo(simbolo, '4H')
        _pullback_4h = tf_bias.obtener_pullback_4h(simbolo)
        _dir_1d    = _bias_1d['bias']  if _bias_1d  else tf_bias.BIAS_NEUTRAL
        _dir_4h    = _bias_4h['bias']  if _bias_4h  else tf_bias.BIAS_NEUTRAL
        _dir_1w    = tf_bias.BIAS_NEUTRAL

        # Contexto pullback 4H para mensajes
        _ctx_pb4h = ""
        if _pullback_4h and _pullback_4h.get('en_pullback'):
            _pb4h_tend  = _pullback_4h['tendencia']
            _pb4h_fib   = _pullback_4h['nivel_fib']
            _pb4h_precio = _pullback_4h['precio_nivel']
            _pb4h_prof  = _pullback_4h['profundidad']
            _fib4h_str  = f"Fib {_pb4h_fib:.3f}" if _pb4h_fib else "zona media"
            _ctx_pb4h = (f"🔄 <b>4H:</b> Pullback {_pb4h_tend} — {_fib4h_str} "
                         f"${_pb4h_precio:.0f} ({round(_pb4h_prof * 100, 0):.0f}%)"
                         if _pb4h_precio else
                         f"🔄 <b>4H:</b> Pullback {_pb4h_tend} ({round(_pb4h_prof * 100, 0):.0f}%)")
        self.contexto_pullback = _ctx_pb4h

        # ── Micro-volatilidad y momentum reciente ─────────────────────────────
        # Variables eliminadas — stubs para compatibilidad con snapshot
        _micro_vol = 1.0
        _momentum_rec = 0

        # ── Ajuste de score por predicción anticipada ML ───────────────────────
        if _prob_buy > 0.80:
            score_buy = min(score_buy + 4, 25)
            logger.info(f"  🤖 [ML 1H] Alta probabilidad BUY ({_prob_buy:.1%}) — +4 pts BUY")
        elif _prob_buy > 0.65:
            score_buy = min(score_buy + 2, 25)
            logger.info(f"  🤖 [ML 1H] Probabilidad BUY ({_prob_buy:.1%}) — +2 pts BUY")
        if _prob_sell > 0.80:
            score_sell = min(score_sell + 4, 25)
            logger.info(f"  🤖 [ML 1H] Alta probabilidad SELL ({_prob_sell:.1%}) — +4 pts SELL")
        elif _prob_sell > 0.65:
            score_sell = min(score_sell + 2, 25)
            logger.info(f"  🤖 [ML 1H] Probabilidad SELL ({_prob_sell:.1%}) — +2 pts SELL")

        # ── Snapshot de condiciones para backtesting/estudio ─────────
        _condiciones_bd = {
            # Indicadores numéricos
            'rsi': round(float(rsi), 1), 'rsi_prev': round(float(rsi_prev), 1),
            'atr': round(float(atr), 2), 'atr_media': round(float(atr_media), 2),
            'adx': round(float(adx), 1), 'di_plus': round(float(di_plus), 1), 'di_minus': round(float(di_minus), 1),
            'macd': round(float(macd), 4), 'macd_hist': round(float(macd_hist), 4),
            'vol': round(float(vol), 0), 'vol_avg': round(float(vol_avg), 0),
            'score_sell': score_sell, 'score_buy': score_buy,
            'ml_prob_buy': round(float(_prob_buy), 4), 'ml_prob_sell': round(float(_prob_sell), 4),
            'ml_label_buy': str(_etiq_buy), 'ml_label_sell': str(_etiq_sell),
            # Zonas S/R
            'zrl': round(zrl, 2), 'zrh': round(zrh, 2), 'zsl': round(zsl, 2), 'zsh': round(zsh, 2),
            # Condiciones SELL (solo EMA + S/R; resto son stubs False para compatibilidad)
            'en_zona_resist': bool(en_zona_resist), 'en_zona_resist_any': bool(en_zona_resist_any),
            'emas_bajistas': bool(emas_bajistas), 'bajo_ema200': bool(bajo_ema200),
            'adx_lateral': bool(adx_lateral),
            'canal_alcista_roto': bool(canal_alcista_roto), 'retest_canal_sell': bool(retest_canal_sell),
            'rechazo_resist_live': bool(rechazo_resist_live), 'rup_sop_1h': bool(_rup_sop_1h),
            'en_resist_sr_interm': bool(_en_resist_sr_1h and not en_zona_resist),
            'cuña_desc': str(_cuña_desc_1h) if _cuña_desc_1h else None,
            'dt_detectado': bool(_dt_1h), 'v_rev_bajista': bool(v_rev_baj_1h),
            'rebote_bajista': bool(_rebote_baj_1h),
            # Condiciones BUY
            'en_zona_soporte': bool(en_zona_soporte), 'en_zona_soporte_any': bool(en_zona_soporte_any),
            'emas_alcistas': bool(emas_alcistas), 'sobre_ema200': bool(sobre_ema200),
            'canal_bajista_roto': bool(canal_bajista_roto), 'retest_canal_buy': bool(retest_canal_buy),
            'rebote_soporte_live': bool(rebote_soporte_live), 'rup_res_1h': bool(_rup_res_1h),
            'en_sop_sr_interm': bool(_en_sop_sr_1h and not en_zona_soporte),
            'cuña_asc': str(_cuña_asc_1h) if _cuña_asc_1h else None,
            'ds_detectado': bool(_ds_1h), 'v_rev_alcista': bool(v_rev_alc_1h),
            'rebote_alcista': bool(_rebote_alc_1h),
            # Contexto macro (stubs — eliminados del scoring)
            'dxy_bias': None, 'cot_bias': None, 'oi_bias': None,
            'news_sesgo': 'ESPERAR', 'news_score': 0.0,
        }
        for _k, _v in (_pred_features or {}).items():
            _condiciones_bd[_k] = float(_v) if isinstance(_v, (int, float, np.floating)) else _v

        # ── Umbrales: scoring EMA+S/R = máximo 5 pts base (sin ML) ──────────────
        # MAXIMA = S/R + EMA alineada + tendencia (score completo = 5)
        # FUERTE  = S/R + EMA alineada (score = 4)
        # MEDIA   = S/R o EMA alineada (score = 3)
        # ALERTA  = señal débil (score = 2) — solo alertas
        _umbral_max = self.umbral_adaptativo(5, atr, atr_media)
        _umbral_fue = self.umbral_adaptativo(4, atr, atr_media)
        _umbral_med = self.umbral_adaptativo(3, atr, atr_media)
        _umbral_ale = self.umbral_adaptativo(2, atr, atr_media)
        senal_sell_maxima = score_sell >= _umbral_max
        senal_sell_fuerte = score_sell >= _umbral_fue
        senal_sell_media  = score_sell >= _umbral_med
        senal_sell_alerta = score_sell >= _umbral_ale
        senal_buy_maxima  = score_buy  >= _umbral_max
        senal_buy_fuerte  = score_buy  >= _umbral_fue
        senal_buy_media   = score_buy  >= _umbral_med
        senal_buy_alerta  = score_buy  >= _umbral_ale

        # ── FILTRO ADX MÍNIMO: mercado plano → bloquear todas las señales ──────
        # ADX < 15 = mercado sin tendencia (dormido). Las señales en este contexto
        # generan falsos positivos: RSI en 50, ATR colapsando, precio sin dirección.
        _ADX_MIN = 15
        if adx < _ADX_MIN:
            logger.info(f"  😴 [1H] ADX {round(adx, 1)} < {_ADX_MIN} — mercado plano, todas las señales bloqueadas")
            senal_sell_maxima = senal_sell_fuerte = senal_sell_media = senal_sell_alerta = False
            senal_buy_maxima  = senal_buy_fuerte  = senal_buy_media  = senal_buy_alerta  = False

        # ── FILTRO RANGO LATERAL (ADX 15-25): graduated tier-block ──────────────
        # Mercado en rango = ADX sin tendencia fuerte + rango de las últimas 20 velas estrecho.
        # En rango lateral el precio rebota S/R indefinidamente sin romper → señales falsas.
        # Acción graduada:
        #   ADX < 20  + rango < 4×ATR  → LATERAL CLARO  → solo MAXIMA
        #   ADX 20-25 + rango < 4×ATR  → LATERAL SUAVE  → solo FUERTE/MAXIMA
        _rango_20v = float(df['High'].iloc[-21:-1].max() - df['Low'].iloc[-21:-1].min())
        _rango_atr_ratio = (_rango_20v / atr) if atr > 0 else 99.0
        _lateral_rango = _rango_atr_ratio < 4.0   # rango < 4×ATR = compresión clara
        if adx < 20 and _lateral_rango:
            logger.warning(
                f"  🟡 [1H] LATERAL CLARO — ADX={adx:.1f} rango20v={_rango_20v:.0f} ({_rango_atr_ratio:.1f}×ATR) "
                f"→ solo MAXIMA (score≥{_umbral_max})"
            )
            senal_sell_fuerte = senal_sell_media = senal_sell_alerta = False
            senal_buy_fuerte  = senal_buy_media  = senal_buy_alerta  = False
        elif adx < 25 and _lateral_rango:
            logger.info(
                f"  🟡 [1H] LATERAL SUAVE — ADX={adx:.1f} rango20v={_rango_20v:.0f} ({_rango_atr_ratio:.1f}×ATR) "
                f"→ solo FUERTE/MAXIMA (score≥{_umbral_fue})"
            )
            senal_sell_media = senal_sell_alerta = False
            senal_buy_media  = senal_buy_alerta  = False

        # ── Filtro zona estricta (estrategia EMA + S/R): solo señales en zona ──
        # Las señales por debajo de MAXIMA deben tener precio dentro de zona S/R.
        # Sin esto, los scores de cuñas/patrones/V-Reversal disparan señales lejos
        # del nivel — las pruebas históricas muestran que esas pierden sistemáticamente.
        if (senal_sell_fuerte or senal_sell_media or senal_sell_alerta) and not senal_sell_maxima:
            if not en_zona_resist_any:
                logger.info(f"  🚫 [1H] SELL bloqueada: precio no en zona resist ({close:.2f} vs {zrl:.2f}-{zrh:.2f}) — requiere estrategia EMA+S/R")
                senal_sell_fuerte = senal_sell_media = senal_sell_alerta = False
        if (senal_buy_fuerte or senal_buy_media or senal_buy_alerta) and not senal_buy_maxima:
            if not en_zona_soporte_any:
                logger.info(f"  🚫 [1H] BUY bloqueada: precio no en zona soporte ({close:.2f} vs {zsl:.2f}-{zsh:.2f}) — requiere estrategia EMA+S/R")
                senal_buy_fuerte = senal_buy_media = senal_buy_alerta = False

        # ── Opción C: FILTRO HTF OBLIGATORIO — operar a favor de tendencia ────────
        # Si 4H y 1D son ambos BULLISH → bloquear SELL (precio rompiendo máximos).
        # Si 4H y 1D son ambos BEARISH → bloquear BUY (precio en caída estructural).
        # Basta con que UNO de los dos sea contrario para no bloquear (sesgo mixto → OK).
        if _dir_4h == tf_bias.BIAS_BULLISH and _dir_1d == tf_bias.BIAS_BULLISH:
            if senal_sell_alerta or senal_sell_media or senal_sell_fuerte or senal_sell_maxima:
                logger.warning(f"  🚫 [1H] SELL bloqueada: HTF alcista (4H={_dir_4h} + 1D={_dir_1d}) — no operar contra tendencia")
                senal_sell_maxima = senal_sell_fuerte = senal_sell_media = senal_sell_alerta = False
        if _dir_4h == tf_bias.BIAS_BEARISH and _dir_1d == tf_bias.BIAS_BEARISH:
            if senal_buy_alerta or senal_buy_media or senal_buy_fuerte or senal_buy_maxima:
                logger.warning(f"  🚫 [1H] BUY bloqueada: HTF bajista (4H={_dir_4h} + 1D={_dir_1d}) — no operar contra tendencia")
                senal_buy_maxima = senal_buy_fuerte = senal_buy_media = senal_buy_alerta = False

        # ── Filtro de sesión 1H: fuera de 08-21 UTC bloquear ALERTA (tf largo: MEDIA+ pasa) ──
        if not self.en_sesion_optima():
            if senal_sell_alerta and not senal_sell_media:
                logger.info(f"  🌙 [1H] Fuera sesión óptima — SELL ALERTA suprimida")
            if senal_buy_alerta and not senal_buy_media:
                logger.info(f"  🌙 [1H] Fuera sesión óptima — BUY ALERTA suprimida")
            senal_sell_alerta, senal_sell_media, senal_sell_fuerte, senal_sell_maxima = \
                self.umbral_activo_por_sesion(senal_sell_alerta, senal_sell_media, senal_sell_fuerte, senal_sell_maxima, tf_corto=False)
            senal_buy_alerta, senal_buy_media, senal_buy_fuerte, senal_buy_maxima = \
                self.umbral_activo_por_sesion(senal_buy_alerta, senal_buy_media, senal_buy_fuerte, senal_buy_maxima, tf_corto=False)

        def rr(limit, sl, tp):
            return round(abs(tp - limit) / abs(sl - limit), 1) if abs(sl - limit) > 0 else 0

        # ── Timestamps: separar vela estudiada vs envío del mensaje ──
        timestamp_vela = df.index[-2]  # Última vela CERRADA (para señales normales)
        timestamp_vela_live = df.index[-1]  # Vela EN CURSO (para señales LIVE)
        self._current_candle_ts = timestamp_vela  # para _guardar_senal auto-inject
        timestamp_envio = datetime.now(timezone.utc)  # Momento de envío del mensaje
        fecha = timestamp_vela.strftime('%Y-%m-%d %H:%M')

        clave_simbolo = simbolo
        if clave_simbolo in self.ultimo_analisis:
            ua = self.ultimo_analisis[clave_simbolo]
            if (ua['fecha'] == fecha and
                    abs(int(ua['score_sell']) - score_sell) <= 1 and
                    abs(int(ua['score_buy']) - score_buy) <= 1):
                logger.info(f"  ℹ️  Vela {fecha} ya analizada")
                return

        self.ultimo_analisis[clave_simbolo] = {'fecha': fecha, 'score_sell': score_sell, 'score_buy': score_buy}
        logger.info(f"  📅 {fecha} | Close: ${close:.2f} | ATR: ${atr:.2f} | SELL: {score_sell}/21 | BUY: {score_buy}/21")

        clave_vela = f"{simbolo}_1H_{fecha}"



        # ── FILTRO R:R MÍNIMO 1.2 (evaluar ANTES de cualquier señal) ──
        rr_sell_tp1 = rr(sell_limit, sl_venta, tp1_v)
        rr_buy_tp1  = rr(buy_limit,  sl_compra, tp1_c)
        cancelar_sell_rr = rr_sell_tp1 < 1.2
        cancelar_buy_rr  = rr_buy_tp1 < 1.2
        if cancelar_sell_rr:
            logger.warning(f"  ⛔ SELL bloqueada: R:R TP1 = {rr_sell_tp1}:1 < 1.2 mínimo")
            if self.db: self.db.guardar_log(f"SELL bloqueada R:R={rr_sell_tp1} | close={close:.2f} SL={sl_venta:.2f} TP1={tp1_v:.2f}", 'WARNING', 'gold_1h', simbolo)
        if cancelar_buy_rr:
            logger.warning(f"  ⛔ BUY bloqueada: R:R TP1 = {rr_buy_tp1}:1 < 1.2 mínimo")
            if self.db: self.db.guardar_log(f"BUY bloqueada R:R={rr_buy_tp1} | close={close:.2f} SL={sl_compra:.2f} TP1={tp1_c:.2f}", 'WARNING', 'gold_1h', simbolo)

        # Guardar flags ANTES de exclusión mutua — los PREP son avisos informativos
        # y deben dispararse aunque el bias dominante sea la dirección opuesta.
        _prep_sell_alerta = senal_sell_alerta
        _prep_buy_alerta  = senal_buy_alerta

        # ── ALERTA ANTICIPADA ML (precio aún no en zona, pero ML predice entrada) ──
        _clave_pred = f"{simbolo}_1H_PRED_{fecha}"
        _dist_buy = abs(close - zsh)
        _dist_sell = abs(zrl - close)
        _dist_max_pred = float(avg_candle_range) * 5
        _sin_conflicto_buy = True
        _sin_conflicto_sell = True
        if self.db:
            _sin_conflicto_buy = not (
                self.db.existe_senal_activa_misma_dir(simbolo_db, 'COMPRA') or
                self.db.existe_senal_activa_opuesta(simbolo_db, 'COMPRA') or
                self.db.existe_senal_reciente(simbolo_db, 'COMPRA', horas=1) or
                self.db.existe_senal_reciente_opuesta(simbolo_db, 'COMPRA', horas=1)
            )
            _sin_conflicto_sell = not (
                self.db.existe_senal_activa_misma_dir(simbolo_db, 'VENTA') or
                self.db.existe_senal_activa_opuesta(simbolo_db, 'VENTA') or
                self.db.existe_senal_reciente(simbolo_db, 'VENTA', horas=1) or
                self.db.existe_senal_reciente_opuesta(simbolo_db, 'VENTA', horas=1)
            )

        if (_prob_buy > 0.70 and not senal_buy_alerta and _dist_buy < _dist_max_pred and
                _sin_conflicto_buy and not self.ya_enviada(f"{_clave_pred}_PRED_BUY")):
            _ml_msg_buy = (
                f"🤖 <b>PREDICCIÓN ML — {self.nombre_display} {self.tf_label}</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"⚡ El modelo detecta condiciones pre-señal de COMPRA\n"
                f"📊 Probabilidad: {round(_prob_buy * 100)}%\n"
                f"💰 Precio actual: ${close:.2f}\n"
                f"📌 Zona soporte: ${zsl:.2f}–${zsh:.2f}\n"
                f"⏳ Señal reactiva estimada: 1-3 velas\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"📉 RSI: {round(rsi, 1)} ({'bajando' if rsi < rsi_prev else 'subiendo'})  ATR: ${atr:.2f}\n"
                f"🔍 Features clave:\n"
                f"   • Dist. soporte: {_pred_features.get('dist_soporte_pct', 0.0):.2f}%\n"
                f"   • Vol relativo: {_pred_features.get('vol_relativo_3v', 0.0):.2f}\n"
                f"   • MACD mejorando: {'Sí' if _pred_features.get('macd_hist_mejorando', 0) == 1 else 'No'}\n"
                f"   • ATR contrayendo: {'Sí' if _pred_features.get('atr_contrayendo', 0) == 1 else 'No'}\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"⚠️ Esta es una predicción ML, no una señal confirmada\n"
                f"⏱️ TF: 1H  📅 {fecha}"
            )
            self.enviar(_ml_msg_buy)
            self.marcar_enviada(f"{_clave_pred}_PRED_BUY")
            if self.db:
                try:
                    self._guardar_senal({
                        'timestamp': datetime.now(timezone.utc), 'simbolo': simbolo_db,
                        'direccion': 'COMPRA', 'precio_entrada': buy_entry,
                        'tp1': tp1_c, 'tp2': tp2_c, 'tp3': tp3_c, 'sl': sl_compra,
                        'score': score_buy,
                        'indicadores': json.dumps(_condiciones_bd),
                        'patron_velas': 'Predicción anticipada ML COMPRA',
                        'version_detector': 'GOLD 1H-ML-v1.0',
                        'estado': 'PENDIENTE_CONFIRM',
                    })
                except Exception as e:
                    logger.debug(f"  [ML 1H] Error guardando predicción BUY: {e}")

        if (_prob_sell > 0.70 and not senal_sell_alerta and _dist_sell < _dist_max_pred and
                _sin_conflicto_sell and not self.ya_enviada(f"{_clave_pred}_PRED_SELL")):
            _ml_msg_sell = (
                f"🤖 <b>PREDICCIÓN ML — {self.nombre_display} {self.tf_label}</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"⚡ El modelo detecta condiciones pre-señal de VENTA\n"
                f"📊 Probabilidad: {round(_prob_sell * 100)}%\n"
                f"💰 Precio actual: ${close:.2f}\n"
                f"📌 Zona resistencia: ${zrl:.2f}–${zrh:.2f}\n"
                f"⏳ Señal reactiva estimada: 1-3 velas\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"📉 RSI: {round(rsi, 1)} ({'subiendo' if rsi > rsi_prev else 'bajando'})  ATR: ${atr:.2f}\n"
                f"🔍 Features clave:\n"
                f"   • Dist. resistencia: {_pred_features.get('dist_resist_pct', 0.0):.2f}%\n"
                f"   • Vol relativo: {_pred_features.get('vol_relativo_3v', 0.0):.2f}\n"
                f"   • MACD mejorando: {'Sí' if _pred_features.get('macd_hist_mejorando', 0) == 1 else 'No'}\n"
                f"   • ATR contrayendo: {'Sí' if _pred_features.get('atr_contrayendo', 0) == 1 else 'No'}\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"⚠️ Esta es una predicción ML, no una señal confirmada\n"
                f"⏱️ TF: 1H  📅 {fecha}"
            )
            self.enviar(_ml_msg_sell)
            self.marcar_enviada(f"{_clave_pred}_PRED_SELL")
            if self.db:
                try:
                    self._guardar_senal({
                        'timestamp': datetime.now(timezone.utc), 'simbolo': simbolo_db,
                        'direccion': 'VENTA', 'precio_entrada': sell_entry,
                        'tp1': tp1_v, 'tp2': tp2_v, 'tp3': tp3_v, 'sl': sl_venta,
                        'score': score_sell,
                        'indicadores': json.dumps(_condiciones_bd),
                        'patron_velas': 'Predicción anticipada ML VENTA',
                        'version_detector': 'GOLD 1H-ML-v1.0',
                        'estado': 'PENDIENTE_CONFIRM',
                    })
                except Exception as e:
                    logger.debug(f"  [ML 1H] Error guardando predicción SELL: {e}")

        # ── Publicar / limpiar zona activa para modo caza 15M/5M ──────────────────
        if _prep_buy_alerta and not cancelar_buy and not cancelar_buy_rr:
            tf_bias.publicar_zona_activa(simbolo, tf_bias.BIAS_BULLISH, {
                'zsl': zsl, 'zsh': zsh,
                'buy_limit': buy_limit, 'sl': sl_compra,
                'tp1': tp1_c, 'tp2': tp2_c, 'tp3': tp3_c,
                'atr': atr, 'score_1h': score_buy,
            })
            logger.info(f"  🏗️ [1H] Zona BUY activa publicada — zona ${zsl:.2f}-${zsh:.2f}, limit ${buy_limit:.2f}")
        elif cancelar_buy:
            tf_bias.limpiar_zona_activa(simbolo, tf_bias.BIAS_BULLISH)

        if _prep_sell_alerta and not cancelar_sell and not cancelar_sell_rr:
            tf_bias.publicar_zona_activa(simbolo, tf_bias.BIAS_BEARISH, {
                'zrl': zrl, 'zrh': zrh,
                'sell_limit': sell_limit, 'sl': sl_venta,
                'tp1': tp1_v, 'tp2': tp2_v, 'tp3': tp3_v,
                'atr': atr, 'score_1h': score_sell,
            })
            logger.info(f"  🏗️ [1H] Zona SELL activa publicada — zona ${zrl:.2f}-${zrh:.2f}, limit ${sell_limit:.2f}")
        elif cancelar_sell:
            tf_bias.limpiar_zona_activa(simbolo, tf_bias.BIAS_BEARISH)

        # ── EXCLUSIÓN MUTUA: una sola dirección por vela (todos los niveles) ──
        if (senal_sell_alerta or senal_sell_media or senal_sell_fuerte or senal_sell_maxima) and \
           (senal_buy_alerta  or senal_buy_media  or senal_buy_fuerte  or senal_buy_maxima):
            if score_sell >= score_buy:
                senal_buy_maxima = senal_buy_fuerte = senal_buy_media = senal_buy_alerta = False
                logger.info(f"  ⚖️ Exclusión mutua: BUY suprimida (SELL {score_sell} >= BUY {score_buy})")
            else:
                senal_sell_maxima = senal_sell_fuerte = senal_sell_media = senal_sell_alerta = False
                logger.info(f"  ⚖️ Exclusión mutua: SELL suprimida (BUY {score_buy} > SELL {score_sell})")
        # Sincronizar _prep_* con el resultado de la exclusión mutua
        # para que los bloques LIVE no ignoren la dirección suprimida
        _prep_sell_alerta = _prep_sell_alerta and senal_sell_alerta
        _prep_buy_alerta  = _prep_buy_alerta  and senal_buy_alerta

        # ── PUBLICAR + FILTRO CONFLUENCIA MULTI-TF (GOLD 1H) ──
        _sesgo_dir = tf_bias.BIAS_BEARISH if score_sell > score_buy else tf_bias.BIAS_BULLISH if score_buy > score_sell else tf_bias.BIAS_NEUTRAL
        tf_bias.publicar_sesgo(simbolo, '1H', _sesgo_dir, max(score_sell, score_buy))
        _conf_sell = ""; _conf_buy = ""
        if senal_sell_media:
            _ok, _desc = tf_bias.verificar_confluencia(simbolo, '1H', tf_bias.BIAS_BEARISH, score=score_sell)
            if not _ok:
                logger.info(f"  🚫 SELL bloqueada por TF superior: {_desc[:80]}")
                senal_sell_maxima = senal_sell_fuerte = senal_sell_media = False
            else:
                _conf_sell = _desc
        if senal_buy_media:
            _ok, _desc = tf_bias.verificar_confluencia(simbolo, '1H', tf_bias.BIAS_BULLISH, score=score_buy)
            if not _ok:
                logger.info(f"  🚫 BUY bloqueada por TF superior: {_desc[:80]}")
                senal_buy_maxima = senal_buy_fuerte = senal_buy_media = False
            else:
                _conf_buy = _desc

        # ── BLOQUE CONTEXTO HTF (1D/1W/4H) para mensajes ─────────────────────
        _ctx_htf = ""
        try:
            _htf_lineas_1h = []
            for _tf_key, _label in [('1W', '1W'), ('1D', '1D'), ('4H', '4H')]:
                _bd = tf_bias.obtener_sesgo(simbolo, _tf_key)
                if _bd:
                    _b   = _bd['bias']
                    _ico = "📈" if _b == tf_bias.BIAS_BULLISH else "📉" if _b == tf_bias.BIAS_BEARISH else "➖"
                    _htf_lineas_1h.append(f"  {_ico} <b>{_label}:</b> {_b}")
                else:
                    _htf_lineas_1h.append(f"  ⏳ <b>{_tf_key}:</b> sin datos")
            _ctx_htf = "\n━━━━━━━━━━━━━━━━━━━━\n📊 <b>Contexto HTF:</b>\n" + "\n".join(_htf_lineas_1h)
        except Exception as _ctx_e:
            logger.debug(f"  [1H] Error _ctx_htf: {_ctx_e}")
        # ─────────────────────────────────────────────────────────────────────

        # ── ALERTA INMEDIATA: rebote/rechazo en vela VIVA ────────────────────────
        # Se dispara mientras la vela está formándose — no espera al cierre.
        # El precio YA tocó la zona y está rebotando/rechazando: entry = market price ahora.
        # ── ALERTAS DE APROXIMACIÓN → SEÑAL ACCIONABLE (pon la orden limit ahora) ──
        if aproximando_resist and not en_zona_resist and not cancelar_sell and not cancelar_sell_rr and _prep_sell_alerta and not self.ya_enviada(f"{clave_vela}_PREP_SELL"):
            nv = ("🔥 SELL MÁXIMA" if senal_sell_maxima else
                  "🔴 SELL FUERTE" if senal_sell_fuerte else
                  "⚡ SELL MEDIA"  if senal_sell_media  else
                  "👀 SELL ALERTA")
            msg = (f"⏳ <b>SETUP SELL {self.tf_label} — {self.nombre_display}</b> | ESPERANDO CONFIRMACIÓN 15M/5M\n"
                   f"━━━━━━━━━━━━━━━━━━━━\n"
                   f"📊 <b>Nivel:</b> {nv}\n"
                   f"💰 <b>Precio actual:</b> ${close:.2f}\n"
                   f"📌 <b>SELL LIMIT previsto:</b> ${sell_entry:.2f}  (spread ${spread:.2f} incluido)\n"
                   f"🛑 <b>Stop Loss:</b> ${sl_venta:.2f}  ← swing high estructural\n"
                   f"━━━━━━━━━━━━━━━━━━━━\n"
                   f"🎯 <b>TP1:</b> ${tp1_v:.2f}  R:R {rr(sell_entry, sl_venta, tp1_v)}:1  (zona S/R)\n"
                   f"🎯 <b>TP2:</b> ${tp2_v:.2f}  R:R {rr(sell_entry, sl_venta, tp2_v)}:1  (zona S/R)\n"
                   f"🎯 <b>TP3:</b> ${tp3_v:.2f}  R:R {rr(sell_entry, sl_venta, tp3_v)}:1  (zona S/R)\n"
                   f"━━━━━━━━━━━━━━━━━━━━\n"
                   + (f"🔻 <b>Canal alcista ROTO</b> — nivel canal ${linea_soporte_canal:.2f}\n" if canal_alcista_roto else "")
                   + f"📊 <b>Score:</b> {score_sell}/23  📉 <b>RSI:</b> {round(rsi, 1)}  📐 <b>ATR:</b> ${atr:.2f}\n"
                   f"📰 <b>Noticias:</b> {_sesgo_etiq} ({_sesgo_score:+.1f})  ➜  {_sesgo_news.replace('_', ' ')}\n"
                   f"⏱️ <b>TF:</b> 1H  📅 {fecha}  🔒 Aguardando alineación 15M/5M...")
            _bloquear_prep_sell = self.db and (
                self.db.existe_senal_reciente(simbolo_db, "VENTA", horas=1) or
                self.db.existe_senal_reciente_opuesta(simbolo_db, "VENTA", horas=1))
            if _bloquear_prep_sell:
                logger.info(f"  🚫 PREP SELL bloqueada: señal reciente o conflicto en BD")
            else:
                if self.db:
                    try:
                        self._guardar_senal({
                            'timestamp': datetime.now(timezone.utc), 'simbolo': simbolo_db,
                            'direccion': 'VENTA', 'precio_entrada': sell_entry,
                            'tp1': tp1_v, 'tp2': tp2_v, 'tp3': tp3_v, 'sl': sl_venta,
                            'score': score_sell,
                            'indicadores': json.dumps(_condiciones_bd),
                            'patron_velas': f"Evening Star:{evening_star}, Shooting Star:{shooting_star}",
                            'version_detector': 'GOLD 1H-v2.0',
                            'estado': 'PENDIENTE_CONFIRM'
                        })
                    except Exception as e:
                        logger.error(f"  ⚠️ Error BD: {e}")
                _nivel_prep_sell = ("MAXIMA" if senal_sell_maxima else "FUERTE" if senal_sell_fuerte else "MEDIA" if senal_sell_media else "ALERTA")
                if self._debe_suprimir_por_evento(_nivel_prep_sell):
                    logger.info(f"  🔕 [1H] SELL PREP suprimida por evento macro")
                else:
                    if _ctx_htf:
                        msg += _ctx_htf
                    self.enviar(msg); self.marcar_enviada(f"{clave_vela}_PREP_SELL")

        if aproximando_soporte and not en_zona_soporte and not cancelar_buy and not cancelar_buy_rr and _prep_buy_alerta and not self.ya_enviada(f"{clave_vela}_PREP_BUY"):
            nv = ("🔥 BUY MÁXIMA" if senal_buy_maxima else
                  "🟢 BUY FUERTE" if senal_buy_fuerte else
                  "⚡ BUY MEDIA"  if senal_buy_media  else
                  "👀 BUY ALERTA")
            msg = (f"⏳ <b>SETUP BUY {self.tf_label} — {self.nombre_display}</b> | ESPERANDO CONFIRMACIÓN 15M/5M\n"
                   f"━━━━━━━━━━━━━━━━━━━━\n"
                   f"📊 <b>Nivel:</b> {nv}\n"
                   f"💰 <b>Precio actual:</b> ${close:.2f}\n"
                   f"📌 <b>BUY LIMIT previsto:</b> ${buy_entry:.2f}  (spread ${spread:.2f} incluido)\n"
                   f"🛑 <b>Stop Loss:</b> ${sl_compra:.2f}  ← swing low estructural\n"
                   f"━━━━━━━━━━━━━━━━━━━━\n"
                   f"🎯 <b>TP1:</b> ${tp1_c:.2f}  R:R {rr(buy_entry, sl_compra, tp1_c)}:1  (zona S/R)\n"
                   f"🎯 <b>TP2:</b> ${tp2_c:.2f}  R:R {rr(buy_entry, sl_compra, tp2_c)}:1  (zona S/R)\n"
                   f"🎯 <b>TP3:</b> ${tp3_c:.2f}  R:R {rr(buy_entry, sl_compra, tp3_c)}:1  (zona S/R)\n"
                   f"━━━━━━━━━━━━━━━━━━━━\n"
                   + (f"🔺 <b>Canal bajista ROTO</b> — nivel canal ${linea_resist_canal:.2f}\n" if canal_bajista_roto else "")
                   + f"📊 <b>Score:</b> {score_buy}/23  📉 <b>RSI:</b> {round(rsi, 1)}  📐 <b>ATR:</b> ${atr:.2f}\n"
                   f"📰 <b>Noticias:</b> {_sesgo_etiq} ({_sesgo_score:+.1f})  ➜  {_sesgo_news.replace('_', ' ')}\n"
                   f"⏱️ <b>TF:</b> 1H  📅 {fecha}  🔒 Aguardando alineación 15M/5M...")
            _bloquear_prep_buy = self.db and (
                self.db.existe_senal_reciente(simbolo_db, "COMPRA", horas=1) or
                self.db.existe_senal_reciente_opuesta(simbolo_db, "COMPRA", horas=1))
            if _bloquear_prep_buy:
                logger.info(f"  🚫 PREP BUY bloqueada: señal reciente o conflicto en BD")
            else:
                if self.db:
                    try:
                        self._guardar_senal({
                            'timestamp': datetime.now(timezone.utc), 'simbolo': simbolo_db,
                            'direccion': 'COMPRA', 'precio_entrada': buy_entry,
                            'tp1': tp1_c, 'tp2': tp2_c, 'tp3': tp3_c, 'sl': sl_compra,
                            'score': score_buy,
                            'indicadores': json.dumps(_condiciones_bd),
                            'patron_velas': f"Morning Star:{morning_star}, Hammer:{hammer}",
                            'version_detector': 'GOLD 1H-v2.0',
                            'estado': 'PENDIENTE_CONFIRM'
                        })
                    except Exception as e:
                        logger.error(f"  ⚠️ Error BD: {e}")
                _nivel_prep_buy = ("MAXIMA" if senal_buy_maxima else "FUERTE" if senal_buy_fuerte else "MEDIA" if senal_buy_media else "ALERTA")
                if self._debe_suprimir_por_evento(_nivel_prep_buy):
                    logger.info(f"  🔕 [1H] BUY PREP suprimida por evento macro")
                else:
                    if _ctx_htf:
                        msg += _ctx_htf
                    self.enviar(msg); self.marcar_enviada(f"{clave_vela}_PREP_BUY")

        # ── SEÑALES SELL (en zona) — requiere mínimo MEDIA para enviar Telegram ──
        _tiene_rechazo_confirmado = en_zona_resist_any and (vela_rechazo or evening_star or intento_rotura_fallido)
        _sell_activa = senal_sell_media  # Tier ALERTA eliminado: solo MEDIA, FUERTE y MAXIMA
        if _sell_activa and not cancelar_sell and rr_sell_tp1 >= 1.2:
            if self.ya_enviada(f"{clave_vela}_PREP_SELL") and not (senal_sell_fuerte or senal_sell_maxima) and not _tiene_rechazo_confirmado:
                logger.info(f"  ℹ️  SELL ALERTA/MEDIA ignorada: señal accionable ya enviada")
            else:
                if senal_sell_maxima:  nivel = "🔥 SELL MÁXIMA (1H)"
                elif senal_sell_fuerte: nivel = "🔴 SELL FUERTE (1H)"
                elif senal_sell_media:  nivel = "⚠️ SELL MEDIA (1H)"
                else:                   nivel = "👀 SELL ALERTA (1H)"
                tipo_clave = ("SELL_MAX" if senal_sell_maxima else
                              "SELL_FUE" if senal_sell_fuerte else
                              "SELL_MED" if senal_sell_media  else "SELL_ALE")
                if not self.ya_enviada(f"{clave_vela}_{tipo_clave}"):
                    if self.ya_enviada(f"{clave_vela}_PREP_SELL"):
                        # Breve confirmación — señal accionable ya fue enviada antes
                        msg = (f"✅ <b>CONFIRMACIÓN SELL — {self.nombre_display} {self.tf_label}</b>\n"
                               f"━━━━━━━━━━━━━━━━━━━━\n"
                               f"{nivel} — precio ahora en zona\n"
                               f"💰 <b>Precio:</b> ${close:.2f}\n"
                               f"📊 <b>Score:</b> {score_sell}/21  📉 <b>RSI:</b> {round(rsi, 1)}\n"
                               f"⏱️ <b>TF:</b> 1H  📅 {fecha}")
                        _nivel_z_sell = ("MAXIMA" if senal_sell_maxima else "FUERTE" if senal_sell_fuerte else "MEDIA" if senal_sell_media else "ALERTA")
                        if not self._debe_suprimir_por_evento(_nivel_z_sell):
                            self.enviar(msg)
                        self.marcar_enviada(f"{clave_vela}_{tipo_clave}")
                    else:
                        # Precio saltó directo a la zona sin pre-alerta → señal completa con DB
                        if self.db and (
                            self.db.existe_senal_reciente(simbolo_db, "VENTA", horas=1) or
                            self.db.existe_senal_reciente_opuesta(simbolo_db, "VENTA", horas=1)):
                            logger.warning(f"  ⚠️  Señal VENTA 1H bloqueada — conflicto en BD"); return
                        msg = (f"{nivel} — <b>{self.nombre_display} ⏰ INTRADÍA</b>\n"
                               f"━━━━━━━━━━━━━━━━━━━━\n"
                               f"💰 <b>Precio:</b>     ${close:.2f}\n"
                               f"📌 <b>SELL LIMIT:</b> ${sell_entry:.2f}  (spread ${spread:.2f} incluido)\n"
                               f"🛑 <b>Stop Loss:</b>  ${sl_venta:.2f}  (-${round(sl_venta - sell_entry, 2)})\n"
                               f"━━━━━━━━━━━━━━━━━━━━\n"
                               f"🎯 <b>TP1:</b> ${tp1_v:.2f}  R:R {rr(sell_entry, sl_venta, tp1_v)}:1\n"
                               f"🎯 <b>TP2:</b> ${tp2_v:.2f}  R:R {rr(sell_entry, sl_venta, tp2_v)}:1\n"
                               f"🎯 <b>TP3:</b> ${tp3_v:.2f}  R:R {rr(sell_entry, sl_venta, tp3_v)}:1\n"
                               f"━━━━━━━━━━━━━━━━━━━━\n"
                               f"📊 <b>Score:</b> {score_sell}/21  📉 <b>RSI:</b> {round(rsi, 1)}  📐 <b>ADX:</b> {round(adx, 1)}\n"
                               f"📐 <b>ATR:</b> ${atr:.2f}\n"
                               f"⏱️ <b>TF:</b> 1H  📅 {fecha}\n"
                               f"🔒 <b>INTRADÍA — Cerrar antes del cierre de sesión</b>")
                        if _conf_sell:
                            msg += f"\n━━━━━━━━━━━━━━━━━━━━\n{_conf_sell}"
                        if _ctx_htf:
                            msg += _ctx_htf
                        if self.db:
                            try:
                                self._guardar_senal({
                                    'timestamp': datetime.now(timezone.utc), 'simbolo': simbolo_db,
                                    'direccion': 'VENTA', 'precio_entrada': sell_entry,
                                    'tp1': tp1_v, 'tp2': tp2_v, 'tp3': tp3_v, 'sl': sl_venta,
                                    'score': score_sell,
                                    'indicadores': json.dumps(_condiciones_bd),
                                    'patron_velas': f"Evening Star:{evening_star}, Shooting Star:{shooting_star}",
                                    'version_detector': 'GOLD 1H-v2.0'
                                })
                            except Exception as e:
                                logger.error(f"  ⚠️ Error BD: {e}")
                        _nivel_z_sell_d = ("MAXIMA" if senal_sell_maxima else "FUERTE" if senal_sell_fuerte else "MEDIA" if senal_sell_media else "ALERTA")
                        if not self._debe_suprimir_por_evento(_nivel_z_sell_d):
                            self.enviar(msg)
                        self.marcar_enviada(f"{clave_vela}_{tipo_clave}")

        # ── SEÑALES BUY (en zona) — requiere mínimo MEDIA para enviar Telegram ──
        # Si el precio realmente tocó la zona Y hay patrón de rebote → siempre confirmar
        _tiene_rebote_confirmado = en_zona_soporte_any and (vela_rebote or morning_star or intento_caida_fallido)
        _buy_activa = senal_buy_media  # Tier ALERTA eliminado: solo MEDIA, FUERTE y MAXIMA
        if _buy_activa and not cancelar_buy and rr_buy_tp1 >= 1.2:
            if self.ya_enviada(f"{clave_vela}_PREP_BUY") and not (senal_buy_fuerte or senal_buy_maxima) and not _tiene_rebote_confirmado:
                logger.info(f"  ℹ️  BUY ALERTA/MEDIA ignorada: señal accionable ya enviada")
            else:
                if senal_buy_maxima:   nivel = "🔥 BUY MÁXIMA (1H)"
                elif senal_buy_fuerte:  nivel = "🟢 BUY FUERTE (1H)"
                elif senal_buy_media:   nivel = "⚠️ BUY MEDIA (1H)"
                else:                   nivel = "👀 BUY ALERTA (1H)"
                tipo_clave = ("BUY_MAX" if senal_buy_maxima else
                              "BUY_FUE" if senal_buy_fuerte else
                              "BUY_MED" if senal_buy_media  else "BUY_ALE")
                if not self.ya_enviada(f"{clave_vela}_{tipo_clave}"):
                    if self.ya_enviada(f"{clave_vela}_PREP_BUY"):
                        # Breve confirmación — señal accionable ya fue enviada antes
                        msg = (f"✅ <b>CONFIRMACIÓN BUY — {self.nombre_display} {self.tf_label}</b>\n"
                               f"━━━━━━━━━━━━━━━━━━━━\n"
                               f"{nivel} — precio ahora en zona\n"
                               f"💰 <b>Precio:</b> ${close:.2f}\n"
                               f"📊 <b>Score:</b> {score_buy}/21  📉 <b>RSI:</b> {round(rsi, 1)}\n"
                               f"⏱️ <b>TF:</b> 1H  📅 {fecha}")
                        _nivel_z_buy = ("MAXIMA" if senal_buy_maxima else "FUERTE" if senal_buy_fuerte else "MEDIA" if senal_buy_media else "ALERTA")
                        if not self._debe_suprimir_por_evento(_nivel_z_buy):
                            self.enviar(msg)
                        self.marcar_enviada(f"{clave_vela}_{tipo_clave}")
                    else:
                        # Precio saltó directo a la zona sin pre-alerta → señal completa con DB
                        if self.db and (
                            self.db.existe_senal_reciente(simbolo_db, "COMPRA", horas=1) or
                            self.db.existe_senal_reciente_opuesta(simbolo_db, "COMPRA", horas=1)):
                            logger.warning(f"  ⚠️  Señal COMPRA 1H bloqueada — conflicto en BD"); return
                        msg = (f"{nivel} — <b>{self.nombre_display} ⏰ INTRADÍA</b>\n"
                               f"━━━━━━━━━━━━━━━━━━━━\n"
                               f"💰 <b>Precio:</b>    ${close:.2f}\n"
                               f"📌 <b>BUY LIMIT:</b> ${buy_entry:.2f}  (spread ${spread:.2f} incluido)\n"
                               f"🛑 <b>Stop Loss:</b> ${sl_compra:.2f}  (-${round(buy_entry - sl_compra, 2)})\n"
                               f"━━━━━━━━━━━━━━━━━━━━\n"
                               f"🎯 <b>TP1:</b> ${tp1_c:.2f}  R:R {rr(buy_entry, sl_compra, tp1_c)}:1\n"
                               f"🎯 <b>TP2:</b> ${tp2_c:.2f}  R:R {rr(buy_entry, sl_compra, tp2_c)}:1\n"
                               f"🎯 <b>TP3:</b> ${tp3_c:.2f}  R:R {rr(buy_entry, sl_compra, tp3_c)}:1\n"
                               f"━━━━━━━━━━━━━━━━━━━━\n"
                               f"📊 <b>Score:</b> {score_buy}/21  📉 <b>RSI:</b> {round(rsi, 1)}  📐 <b>ADX:</b> {round(adx, 1)}\n"
                               f"📐 <b>ATR:</b> ${atr:.2f}\n"
                               f"⏱️ <b>TF:</b> 1H  📅 {fecha}\n"
                               f"🔒 <b>INTRADÍA — Cerrar antes del cierre de sesión</b>")
                        if _conf_buy:
                            msg += f"\n━━━━━━━━━━━━━━━━━━━━\n{_conf_buy}"
                        if _ctx_htf:
                            msg += _ctx_htf
                        if self.db:
                            try:
                                self._guardar_senal({
                                    'timestamp': datetime.now(timezone.utc), 'simbolo': simbolo_db,
                                    'direccion': 'COMPRA', 'precio_entrada': buy_entry,
                                    'tp1': tp1_c, 'tp2': tp2_c, 'tp3': tp3_c, 'sl': sl_compra,
                                    'score': score_buy,
                                    'indicadores': json.dumps(_condiciones_bd),
                                    'patron_velas': f"Morning Star:{morning_star}, Hammer:{hammer}",
                                    'version_detector': 'GOLD 1H-v2.0'
                                })
                            except Exception as e:
                                logger.error(f"  ⚠️ Error BD: {e}")
                        _nivel_z_buy_d = ("MAXIMA" if senal_buy_maxima else "FUERTE" if senal_buy_fuerte else "MEDIA" if senal_buy_media else "ALERTA")
                        if not self._debe_suprimir_por_evento(_nivel_z_buy_d):
                            self.enviar(msg)
                        self.marcar_enviada(f"{clave_vela}_{tipo_clave}")

        # ── SEÑALES RETEST DE CANAL ROTO (alta probabilidad) ─────────────────────
        # Setup: precio rompe canal → retrocede al nivel roto → rechaza → ENTRADA
        # CASO ESPECIAL: si el canal roto va CONTRA la tendencia superior (1D/1W),
        # el retest NO es una señal en esa dirección, sino un PULLBACK de la tendencia
        # mayor → aviso de zona de compra/venta en dirección de la tendencia principal.

        if retest_canal_sell and not self.ya_enviada(f"{clave_vela}_RETEST_SELL"):
            if pullback_alcista:
                # Canal alcista roto en 1H PERO 1D/1W es BULLISH → pullback, no reversión
                # El retest de la línea rota es una ZONA DE COMPRA (rebote esperado)
                if not self.ya_enviada(f"{clave_vela}_PULLBACK_BUY"):
                    _dir_str = f"1D: {_dir_1d}" + (f" | 1W: {_dir_1w}" if _dir_1w != tf_bias.BIAS_NEUTRAL else "")
                    msg = (
                        f"⚠️ <b>PULLBACK EN TENDENCIA ALCISTA — {self.nombre_display} {self.tf_label}</b>\n"
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
                    self.enviar(msg)
                    self.marcar_enviada(f"{clave_vela}_PULLBACK_BUY")
                logger.warning(f"  ⛔ Retest SELL suprimido — pullback alcista (1D/1W bullish)")
            elif self.db and self.db.existe_senal_reciente(simbolo_db, 'VENTA', horas=1):
                logger.info(f"  ℹ️  Retest SELL duplicado")
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
                if rr1 >= 1.5 and score_sell >= 2:   # R:R mínimo + al menos 2 confirmaciones técnicas
                    _htf_conf_sell_lines = "\n".join(filter(None, [
                        f"📈 <b>4H también roto</b> — soporte ${_linea_canal_4h_sop:.0f}" if canal_sell_confirmado_4h else "",
                        f"📈 <b>1D también roto</b> — soporte ${_linea_canal_1d_sop:.0f}" if canal_sell_confirmado_1d else "",
                        f"📈 <b>1W también roto</b> — soporte ${_linea_canal_1w_sop:.0f}" if canal_sell_confirmado_1w else "",
                    ]))
                    _conf_htf_sell = (f"\n{_htf_conf_sell_lines}  ← multi-TF confirmado"
                                      if _htf_conf_sell_lines else "")
                    msg = (
                        f"🎯 <b>RETEST CANAL SELL — {self.nombre_display} {self.tf_label}</b>\n"
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
                    if self.db:
                        try:
                            self._guardar_senal({
                                'timestamp': datetime.now(timezone.utc), 'simbolo': simbolo_db,
                                'direccion': 'VENTA', 'precio_entrada': entry_rt,
                                'tp1': tp1_rt_sell, 'tp2': tp2_rt_sell, 'tp3': tp3_rt_sell,
                                'sl': sl_rt_sell, 'score': score_sell,
                                'indicadores': json.dumps(_condiciones_bd),
                                'patron_velas': f"Retest canal alcista roto, línea ${linea_soporte_canal:.2f}",
                                'version_detector': 'GOLD 1H-v2.0-RETEST'
                            })
                        except Exception as e:
                            logger.error(f"  ⚠️ Error BD: {e}")
                    self.enviar(msg)
                    self.marcar_enviada(f"{clave_vela}_RETEST_SELL")
                else:
                    logger.warning(f"  ⛔ Retest SELL bloqueado: R:R {rr1}:1 < 1.5 o score {score_sell} < 2")

        if retest_canal_buy and not self.ya_enviada(f"{clave_vela}_RETEST_BUY"):
            if pullback_bajista:
                # Canal bajista roto en 1H PERO 1D/1W es BEARISH → pullback, no reversión
                if not self.ya_enviada(f"{clave_vela}_PULLBACK_SELL"):
                    _dir_str = f"1D: {_dir_1d}" + (f" | 1W: {_dir_1w}" if _dir_1w != tf_bias.BIAS_NEUTRAL else "")
                    msg = (
                        f"⚠️ <b>PULLBACK EN TENDENCIA BAJISTA — {self.nombre_display} {self.tf_label}</b>\n"
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
                    self.enviar(msg)
                    self.marcar_enviada(f"{clave_vela}_PULLBACK_SELL")
                logger.warning(f"  ⛔ Retest BUY suprimido — pullback bajista (1D/1W bearish)")
            elif self.db and self.db.existe_senal_reciente(simbolo_db, 'COMPRA', horas=1):
                logger.info(f"  ℹ️  Retest BUY duplicado")
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
                if rr1 >= 1.5 and score_buy >= 2:   # R:R mínimo + al menos 2 confirmaciones técnicas
                    _htf_conf_buy_lines = "\n".join(filter(None, [
                        f"📈 <b>4H también roto</b> — resist ${_linea_canal_4h_res:.0f}" if canal_buy_confirmado_4h else "",
                        f"📈 <b>1D también roto</b> — resist ${_linea_canal_1d_res:.0f}" if canal_buy_confirmado_1d else "",
                        f"📈 <b>1W también roto</b> — resist ${_linea_canal_1w_res:.0f}" if canal_buy_confirmado_1w else "",
                    ]))
                    _conf_htf_buy = (f"\n{_htf_conf_buy_lines}  ← multi-TF confirmado"
                                     if _htf_conf_buy_lines else "")
                    msg = (
                        f"🎯 <b>RETEST CANAL BUY — {self.nombre_display} {self.tf_label}</b>\n"
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
                    if self.db:
                        try:
                            self._guardar_senal({
                                'timestamp': datetime.now(timezone.utc), 'simbolo': simbolo_db,
                                'direccion': 'COMPRA', 'precio_entrada': entry_rt,
                                'tp1': tp1_rt_buy, 'tp2': tp2_rt_buy, 'tp3': tp3_rt_buy,
                                'sl': sl_rt_buy, 'score': score_buy,
                                'indicadores': json.dumps(_condiciones_bd),
                                'patron_velas': f"Retest canal bajista roto, línea ${linea_resist_canal:.2f}",
                                'version_detector': 'GOLD 1H-v2.0-RETEST'
                            })
                        except Exception as e:
                            logger.error(f"  ⚠️ Error BD: {e}")
                    self.enviar(msg)
                    self.marcar_enviada(f"{clave_vela}_RETEST_BUY")
                else:
                    logger.warning(f"  ⛔ Retest BUY bloqueado: R:R {rr1}:1 < 1.5 o score {score_buy} < 2")

        # ── CANCELACIONES ───────────────────────────────────────────
        if cancelar_sell and not self.ya_enviada(f"{clave_vela}_CANCEL_SELL"):
            msg = (f"❌ <b>CANCELAR SELL LIMIT — {self.nombre_display} {self.tf_label}</b> ❌\n"
                   f"━━━━━━━━━━━━━━━━━━━━\n"
                   f"📌 <b>Orden prevista:</b> SELL LIMIT ${sell_limit:.2f}\n"
                   f"💰 <b>Precio actual:</b>  ${close:.2f}\n"
                   f"⚠️ <b>Motivo:</b> Precio rompió la resistencia (${zrh:.2f}) hacia arriba\n"
                   f"🚫 La entrada ya no es válida — cancela la orden limit\n"
                   f"━━━━━━━━━━━━━━━━━━━━\n"
                   f"⏱️ <b>TF:</b> 1H  📅 {fecha}")
            if self.db:
                try:
                    self.db.cancelar_senales_pendientes(simbolo_db, "VENTA")
                except Exception as e:
                    logger.error(f"  ⚠️ Error BD al cancelar VENTA: {e}")
            self.enviar(msg)
            self.marcar_enviada(f"{clave_vela}_CANCEL_SELL")

        if cancelar_buy and not self.ya_enviada(f"{clave_vela}_CANCEL_BUY"):
            msg = (f"❌ <b>CANCELAR BUY LIMIT — {self.nombre_display} {self.tf_label}</b> ❌\n"
                   f"━━━━━━━━━━━━━━━━━━━━\n"
                   f"📌 <b>Orden prevista:</b> BUY LIMIT ${buy_limit:.2f}\n"
                   f"💰 <b>Precio actual:</b>  ${close:.2f}\n"
                   f"⚠️ <b>Motivo:</b> Precio perforó el soporte (${zsl:.2f}) hacia abajo\n"
                   f"🚫 La entrada ya no es válida — cancela la orden limit\n"
                   f"━━━━━━━━━━━━━━━━━━━━\n"
                   f"⏱️ <b>TF:</b> 1H  📅 {fecha}")
            if self.db:
                try:
                    self.db.cancelar_senales_pendientes(simbolo_db, "COMPRA")
                except Exception as e:
                    logger.error(f"  ⚠️ Error BD al cancelar COMPRA: {e}")
            self.enviar(msg)
            self.marcar_enviada(f"{clave_vela}_CANCEL_BUY")



def main():
    global _last_ml_retrain_1h
    logger.info("🚀 Detector ORO 1H intradía iniciado")
    enviar_telegram(f"🚀 <b>Detector {simbolo_a_nombre('XAUUSD')} 1H — INTRADÍA iniciado</b>\n"
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
            logger.info(f"[{ahora_utc.strftime('%Y-%m-%d %H:%M')} UTC] 💤 Sábado — mercado cerrado. Próxima apertura Domingo 18:00 UTC. Revisando en {int(segundos_espera//60)} min...")
            time.sleep(segundos_espera)
            continue

        logger.info(f"\n[{ahora_utc.strftime('%Y-%m-%d %H:%M:%S')}] 🔄 CICLO #{ciclo} - ORO 1H")
        if (_predictor_1h_buy.necesita_reentrenamiento() or
                _predictor_1h_sell.necesita_reentrenamiento() or
                (time.time() - _last_ml_retrain_1h) >= 7 * 24 * 3600):
            try:
                _predictor_1h_buy.reentrenar_desde_bd(db)
                _predictor_1h_sell.reentrenar_desde_bd(db)
                _last_ml_retrain_1h = time.time()
                logger.info("  ✅ [ML] Modelos 1H re-entrenados")
            except Exception as e:
                logger.warning(f"  ⚠️ [ML] Re-entrenamiento fallido (continuando sin ML): {e}")
        for simbolo, params in SIMBOLOS.items():
            analizar(simbolo, params)
        logger.info(f"⏳ Esperando {CHECK_INTERVAL}s...")
        time.sleep(CHECK_INTERVAL)


if __name__ == '__main__':
    main()
