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
        'atr_sl_mult':        1.0,    # SL ajustado para intradía
        'atr_tp1_mult':       1.5,    # TP1: 1.5× ATR (intradía alcanzable)
        'atr_tp2_mult':       2.5,    # TP2: 2.5× ATR
        'atr_tp3_mult':       4.0,    # TP3: 4.0× ATR (objetivo ambicioso)
        'vol_mult':           1.2,
        'spread':             0.35,         # Spread típico broker CFD (XAUUSD)
    }
} = {}
ultimo_analisis  = {}
# Estado previo de pullback por símbolo — permite edge-trigger (solo dispara al
# activarse, no mientras persiste). Se resetea al resolverse el pullback.
_estado_pullback: dict = {}  # clave → bool (True = pullback activo en ciclo anterior)

# Instancia singleton — persiste alertas_enviadas entre ciclos
_detector_instance: 'GoldDetector1H | None' = None


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

        tp1_v = _tp1_viable_sell(soportes_sr, sell_entry, sl_venta, 1.2,
                                 sell_entry - atr * params['atr_tp1_mult'])
        tp2_v = _recortar_tp_venta(
                    _tp_desde_sr(soportes_sr, 2, sell_entry - atr * params['atr_tp2_mult']),
                    tp1_v, soportes_sr, atr)
        tp3_v = _recortar_tp_venta(
                    _tp_desde_sr(soportes_sr, 3, sell_entry - atr * params['atr_tp3_mult']),
                    tp2_v, soportes_sr, atr)
        tp1_c = _tp1_viable_buy(resistencias_sr, buy_entry, sl_compra, 1.2,
                                buy_entry + atr * params['atr_tp1_mult'])
        _resis_sobre = sorted([v for v in resistencias_sr if v > buy_entry])
        tp2_c = _recortar_tp_compra(
                    _tp_desde_sr(_resis_sobre, 2, buy_entry + atr * params['atr_tp2_mult']),
                    tp1_c, resistencias_sr, atr)
        tp3_c = _recortar_tp_compra(
                    _tp_desde_sr(_resis_sobre, 3, buy_entry + atr * params['atr_tp3_mult']),
                    tp2_c, resistencias_sr, atr)

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
        # Permite alertas DURANTE la formación de la vela sin esperar al cierre.
        # BUY: la vela en curso tocó el soporte (low en zona) y ya está rebotando.
        rebote_soporte_live = (
            (low_live  >= zsl - tol * 1.2) and (low_live  <= zsh + tol * 1.5) and  # low tocó zona soporte (mecha amplificada)
            close_live > open_live and                                   # vela alcista (rebote)
            close_live > zsh - tol and                                   # cierre saliendo de la zona
            low_live   < zsh                                             # efectivamente entró en zona
        )
        # SELL: la vela en curso tocó la resistencia y está rechazando.
        rechazo_resist_live = (
            (high_live >= zrl - tol * 1.2) and (high_live <= zrh + tol * 1.5) and  # high tocó zona resist (mecha amplificada)
            close_live < open_live and                                   # vela bajista (rechazo)
            close_live < zrh + tol and                                   # cierre en/bajo la zona
            high_live  > zrl                                             # efectivamente tocó zona
        )
        if rebote_soporte_live:
            logger.info(f"  ⚡ [LIVE] Rebote en soporte — low_live=${low_live:.2f}  close_live=${close_live:.2f}")
        if rechazo_resist_live:
            logger.info(f"  ⚡ [LIVE] Rechazo en resistencia — high_live=${high_live:.2f}  close_live=${close_live:.2f}")

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

        # ── SCORING VENTA ──────────────────────────────────────────
        intento_rotura_fallido = (high >= zrl) and (close < zrl)
        shooting_star     = is_bearish and upper_wick > body*2 and lower_wick < body*0.3 and en_zona_resist_any
        bearish_engulfing = is_bearish and open_ >= float(prev['High']) and close <= float(prev['Low']) and en_zona_resist_any
        bearish_marubozu  = is_bearish and body > total_range*0.8 and en_zona_resist_any
        doji_resist       = body < total_range*0.1 and en_zona_resist_any and upper_wick > body*2
        vela_rechazo      = shooting_star or bearish_engulfing or bearish_marubozu or doji_resist
        rsi_alto_girando  = (rsi >= rsms) and (rsi < rsi_prev)
        rsi_sobrecompra   = rsi >= 70
        # Contexto: aceleración RSI, micro-volatilidad, momentum intraday (compartidos sell/buy)
        _rsi_baj_3, _rsi_sub_3 = calcular_aceleracion_rsi(df['rsi'])
        _micro_vol    = calcular_micro_volatilidad(df)
        _momentum_rec = calcular_momentum_reciente(df)
        rsi_acelerando_bajada = rsi_alto_girando and _rsi_baj_3
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
            (2 if en_zona_resist_any       else 0) +
            (2 if vela_rechazo             else 0) +
            (2 if vol_alto_rechazo         else 0) +
            (1 if rsi_alto_girando         else 0) +
            (1 if rsi_acelerando_bajada     else 0) +   # RSI bajando 3 velas consecutivas
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
        score_buy = 0  # inicializar aquí para que ruptura/cuña BUY no se pierdan al hacer +=
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

        # ── Cuña descendente / ascendente (1H) ──────────────────────────────
        _lkb_cuña_1h = min(params.get('sr_lookback', 100), 80)
        _cuña_desc_1h, _t_desc_1h, _s_desc_1h = detectar_cuña_descendente(
            df, atr, lookback=_lkb_cuña_1h, wing=3, max_amplitud_pct=0.025)
        _cuña_asc_1h, _t_asc_1h, _s_asc_1h = detectar_cuña_ascendente(
            df, atr, lookback=_lkb_cuña_1h, wing=3, max_amplitud_pct=0.025)
        if _cuña_desc_1h == 'ruptura_alcista':
            score_buy += 6
            logger.info(f"  📐 [1H] CUÑA DESC ROTA AL ALZA (techo ${_t_desc_1h:.2f}) — +6 pts BUY")
        elif _cuña_desc_1h == 'ruptura_bajista':
            score_sell += 6
            logger.info(f"  📐 [1H] CUÑA DESC ROTA A LA BAJA (suelo ${_s_desc_1h:.2f}) — +6 pts SELL")
        elif _cuña_desc_1h == 'compresion':
            score_buy += 2
            logger.info(f"  📐 [1H] CUÑA DESC en compresión ${_s_desc_1h:.2f}-${_t_desc_1h:.2f} — +2 pts BUY")
        if _cuña_asc_1h == 'ruptura_bajista':
            score_sell += 6
            logger.info(f"  📐 [1H] CUÑA ASC ROTA A LA BAJA (suelo ${_s_asc_1h:.2f}) — +6 pts SELL")
        elif _cuña_asc_1h == 'compresion':
            score_sell += 2
            logger.info(f"  📐 [1H] CUÑA ASC en compresión ${_s_asc_1h:.2f}-${_t_asc_1h:.2f} — +2 pts SELL")

        # ── Doble Techo / Doble Suelo (1H) ──────────────────────────────────────
        _dt_1h, _dt_nivel_1h, _dt_neck_1h = detectar_doble_techo(
            df, atr, lookback=_lkb_1h, tol_mult=0.6)
        _ds_1h, _ds_nivel_1h, _ds_neck_1h = detectar_doble_suelo(
            df, atr, lookback=_lkb_1h, tol_mult=0.6)
        if _dt_1h:
            score_sell += 4
            logger.info(f"  🔻 [1H] DOBLE TECHO (M) detectado — techo=${_dt_nivel_1h:.1f} cuello=${_dt_neck_1h:.1f} — +4 pts SELL")
        if _ds_1h:
            score_buy += 4
            logger.info(f"  🔺 [1H] DOBLE SUELO (W) detectado — suelo=${_ds_nivel_1h:.1f} cuello=${_ds_neck_1h:.1f} — +4 pts BUY")

        # ── V-Reversal (1H) — Reversión vertical de alta velocidad ─────────────
        # Parámetros 1H: lookback=12 velas (~12h), mínimo 5.0 ATR caída, 4.0 ATR rebote
        v_rev_alc_1h, v_min_1h, v_precio_1h = detectar_v_reversal_alcista(
            df, atr, lookback=12, min_caida_atr=5.0, min_rebote_atr=4.0)
        v_rev_baj_1h, v_max_1h, v_precio_baj_1h = detectar_v_reversal_bajista(
            df, atr, lookback=12, min_subida_atr=5.0, min_caida_atr=4.0)
        if v_rev_alc_1h:
            score_buy += 5
            logger.info(f"  ⚡ [1H] V-REVERSAL ALCISTA detectado — mín ${v_min_1h:.2f} → ${v_precio_1h:.2f} — +5 pts BUY")
        if v_rev_baj_1h:
            score_sell += 5
            logger.info(f"  ⚡ [1H] V-REVERSAL BAJISTA detectado — máx ${v_max_1h:.2f} → ${v_precio_baj_1h:.2f} — +5 pts SELL")

        if adx_lateral and not en_zona_resist_any: score_sell = max(0, score_sell - 3)

        # ── SCORING COMPRA ─────────────────────────────────────────
        intento_caida_fallido   = (low <= zsh) and (close > zsh)
        hammer            = is_bullish and lower_wick > body*2 and upper_wick < body*0.3 and en_zona_soporte_any
        bullish_engulfing = is_bullish and open_ <= float(prev['Low']) and close >= float(prev['High']) and en_zona_soporte_any
        bullish_marubozu  = is_bullish and body > total_range*0.8 and en_zona_soporte_any
        doji_soporte      = body < total_range*0.1 and en_zona_soporte_any and lower_wick > body*2
        vela_rebote       = hammer or bullish_engulfing or bullish_marubozu or doji_soporte
        rsi_bajo_girando  = (rsi <= rsmb) and (rsi > rsi_prev)
        rsi_acelerando_subida = rsi_bajo_girando and _rsi_sub_3
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

        score_buy += (
            (2 if en_zona_soporte_any        else 0) +
            (2 if vela_rebote                else 0) +
            (2 if vol_alto_rebote            else 0) +
            (1 if rsi_bajo_girando           else 0) +
            (1 if rsi_acelerando_subida       else 0) +   # RSI subiendo 3 velas consecutivas
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
        # ADX lateral en zona soporte: no penalizar (consolidación = agotamiento bajista)
        if adx_lateral and not en_zona_soporte_any: score_buy = max(0, score_buy - 3)

        # ── FIBONACCI RETRACEMENT DINÁMICO (1H) ─────────────────────────────────
        _fib_nivel_1h, _fib_precio_1h, _fib_tend_1h = detectar_precio_en_fibonacci(
            df, atr, lookback=params.get('sr_lookback', 80), tol_mult=0.5)
        if _fib_nivel_1h is not None:
            if _fib_tend_1h == 'bajista':
                _pts_fib_1h = 5 if _fib_nivel_1h in (0.382, 0.5) else 3
                score_sell += _pts_fib_1h
                logger.info(f"  🔢 [1H] Fib {_fib_nivel_1h} BAJISTA en ${_fib_precio_1h:.2f} — +{_pts_fib_1h} pts SELL")
            else:
                _pts_fib_1h = 5 if _fib_nivel_1h in (0.5, 0.618) else 3
                score_buy += _pts_fib_1h
                logger.info(f"  🔢 [1H] Fib {_fib_nivel_1h} ALCISTA en ${_fib_precio_1h:.2f} — +{_pts_fib_1h} pts BUY")

        _fib_data_1h = calcular_fibonacci(df, lookback=params.get('sr_lookback', 80))
        if _fib_data_1h:
            _n1 = _fib_data_1h['niveles']
            logger.info(
                f"  📐 [1H] Fib ({_fib_data_1h['tendencia']}) "
                f"0.382=${_n1[0.382]:.0f} 0.5=${_n1[0.5]:.0f} "
                f"0.618=${_n1[0.618]:.0f} 0.786=${_n1[0.786]:.0f}"
            )

        # ── REBOTE EN S/R O FIBONACCI (1H) ──────────────────────────────────────
        _todos_sop_1h  = list(_sop_interm_1h) + ([_fib_precio_1h] if _fib_tend_1h == 'alcista' and _fib_nivel_1h else [])
        _todas_res_1h  = list(_res_interm_1h) + ([_fib_precio_1h] if _fib_tend_1h == 'bajista' and _fib_nivel_1h else [])

        # ── Añadir niveles de Pivots diarios a S/R ──────────────────────────────
        if _pivots_1h:
            _piv_sop, _piv_res, _piv_info = evaluar_precio_vs_pivots(
                close, high, low, _pivots_1h, atr, tol_mult=0.3)
            _todos_sop_1h = list({*_todos_sop_1h, *_piv_sop})
            _todas_res_1h = list({*_todas_res_1h, *_piv_res})
            logger.info(f"  🎯 [1H] Pivots diarios: PP=${_pivots_1h['PP']:.2f} | {_piv_info}")

        _rsi_serie_1h  = df['rsi']

        _rebote_baj_1h, _desc_baj_1h = detectar_rebote_bajista(
            df, atr, _rsi_serie_1h, _todas_res_1h, tol_mult=0.6)
        _rebote_alc_1h, _desc_alc_1h = detectar_rebote_alcista(
            df, atr, _rsi_serie_1h, _todos_sop_1h, tol_mult=0.6)

        if _rebote_baj_1h:
            score_sell += 4
            logger.info(f"  🔄 [1H] REBOTE BAJISTA detectado ({_desc_baj_1h}) — +4 pts SELL")
        if _rebote_alc_1h:
            score_buy += 4
            logger.info(f"  🔄 [1H] REBOTE ALCISTA detectado ({_desc_alc_1h}) — +4 pts BUY")

        # ── Ajuste por sesgo DXY (correlación inversa Gold/USD) ──
        dxy_bias = get_dxy_bias()
        score_buy, score_sell = ajustar_score_por_dxy(score_buy, score_sell, dxy_bias)

        # ── Ajuste por COT Report (posiciones institucionales semanales) ──
        _cot_bias, _cot_ratio = get_cot_bias()
        score_buy, score_sell = ajustar_score_por_cot(score_buy, score_sell, _cot_bias)

        # ── Ajuste por Yields Reales 10Y (correlación inversa con Gold) ──
        _yield_bias, _yield_val, _yield_ma = get_yield_bias()
        score_buy, score_sell = ajustar_score_por_yield(score_buy, score_sell, _yield_bias)

        # ── Ajuste por Open Interest / Volumen (fuerza de tendencia) ──
        _oi_bias = get_oi_bias()
        score_buy, score_sell = ajustar_score_por_oi(score_buy, score_sell, _oi_bias)

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

        # ── Filtro de volumen: penalizar señales en velas de bajo volumen ──
        score_sell, score_buy, _vol_bajo = self.ajustar_scores_por_volumen(
            score_sell, score_buy, vol, vol_avg, vm)
        if _vol_bajo:
            logger.info(f"  ⚠️ [1H] Volumen bajo ({vol:.0f} < {vol_avg * vm:.0f}) — scores penalizados -3")

        # ── Contexto multi-TF: ¿es el canal roto un PULLBACK dentro de tendencia? ──
        _bias_1d   = tf_bias.obtener_sesgo(simbolo, '1D')
        _bias_1w   = tf_bias.obtener_sesgo(simbolo, '1W')
        _bias_4h   = tf_bias.obtener_sesgo(simbolo, '4H')
        _canal_4h  = tf_bias.obtener_canal_4h(simbolo)
        _canal_1d  = tf_bias.obtener_canal_1d(simbolo)
        _canal_1w  = tf_bias.obtener_canal_1w(simbolo)
        _pullback_4h = tf_bias.obtener_pullback_4h(simbolo)   # estado pullback del TF superior
        _dir_1d    = _bias_1d['bias']  if _bias_1d  else tf_bias.BIAS_NEUTRAL
        _dir_1w    = _bias_1w['bias']  if _bias_1w  else tf_bias.BIAS_NEUTRAL
        _dir_4h    = _bias_4h['bias']  if _bias_4h  else tf_bias.BIAS_NEUTRAL

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
            logger.warning(f"  ⚠️ PULLBACK alcista — canal roto bajista pero HTF (1D/1W/4H) es BULLISH → penalizar SELL")

            # ── Alerta crítica: solo al activarse el pullback (edge-trigger) ──
            # No se repite mientras el pullback persiste; se resetea al resolverse.
            clave_alerta = f"pullback_alcista_{simbolo}"
            estaba_activo = _estado_pullback.get(clave_alerta, False)
            _estado_pullback[clave_alerta] = True

            if not estaba_activo and self.db:
                try:
                    senales_activas = self.db.obtener_senales_activas()
                    sell_activas = [s for s in senales_activas 
                                    if s['direccion'] == 'VENTA' 
                                    and simbolo in s['simbolo']]
                    
                    if sell_activas:
                        _htf_desc = []
                        if _dir_4h == tf_bias.BIAS_BULLISH: _htf_desc.append("4H↗️")
                        if _dir_1d == tf_bias.BIAS_BULLISH: _htf_desc.append("1D↗️")
                        if _dir_1w == tf_bias.BIAS_BULLISH: _htf_desc.append("1W↗️")
                        _htf_str = " + ".join(_htf_desc) if _htf_desc else "HTF alcista"
                        
                        mensaje_alerta = f"""
🚨 <b>ALERTA CRÍTICA - PULLBACK ALCISTA</b>

⚠️ {simbolo} 1H detecta <b>CONFLICTO</b>:
• 1H: Canal alcista roto (señal bajista)
• HTF: {_htf_str} <b>BULLISH</b>
• Precio: ${close:.2f}

📊 <b>Señales SELL activas en riesgo: {len(sell_activas)}</b>

{chr(10).join([f"  • {s['simbolo']} - Entrada: ${s['precio_entrada']:.2f}" for s in sell_activas[:3]])}

💡 <b>RECOMENDACIÓN:</b>
• Considerar cierre defensivo de SELL
• Mover SL a breakeven
• Esperar confirmación HTF bajista

━━━━━━━━━━━━━━━━━━━━
ℹ️ <i>Alerta única — no se repetirá mientras el pullback esté activo</i>
⏰ {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}
"""
                        enviar_telegram(mensaje_alerta)
                        logger.info(f"  🚨 Alerta PULLBACK alcista enviada — {len(sell_activas)} señales SELL en riesgo")
                except Exception as e:
                    logger.error(f"  ❌ Error verificando señales activas para alerta pullback: {e}")
        else:
            # Pullback resuelto → resetear para que la próxima activación vuelva a notificar
            _estado_pullback.pop(f"pullback_alcista_{simbolo}", None)
        if pullback_bajista:
            score_buy  = max(0, score_buy  - 4)
            logger.warning(f"  ⚠️ PULLBACK bajista — canal roto alcista pero HTF (1D/1W/4H) es BEARISH → penalizar BUY")

            # ── Alerta crítica: solo al activarse el pullback (edge-trigger) ──
            # No se repite mientras el pullback persiste; se resetea al resolverse.
            clave_alerta = f"pullback_bajista_{simbolo}"
            estaba_activo = _estado_pullback.get(clave_alerta, False)
            _estado_pullback[clave_alerta] = True

            if not estaba_activo and self.db:
                try:
                    senales_activas = self.db.obtener_senales_activas()
                    buy_activas = [s for s in senales_activas 
                                   if s['direccion'] == 'COMPRA' 
                                   and simbolo in s['simbolo']]
                    
                    if buy_activas:
                        _htf_desc = []
                        if _dir_4h == tf_bias.BIAS_BEARISH: _htf_desc.append("4H↘️")
                        if _dir_1d == tf_bias.BIAS_BEARISH: _htf_desc.append("1D↘️")
                        if _dir_1w == tf_bias.BIAS_BEARISH: _htf_desc.append("1W↘️")
                        _htf_str = " + ".join(_htf_desc) if _htf_desc else "HTF bajista"
                        
                        mensaje_alerta = f"""
🚨 <b>ALERTA CRÍTICA - PULLBACK BAJISTA</b>

⚠️ {simbolo} 1H detecta <b>CONFLICTO</b>:
• 1H: Canal bajista roto (señal alcista)
• HTF: {_htf_str} <b>BEARISH</b>
• Precio: ${close:.2f}

📊 <b>Señales BUY activas en riesgo: {len(buy_activas)}</b>

{chr(10).join([f"  • {s['simbolo']} - Entrada: ${s['precio_entrada']:.2f}" for s in buy_activas[:3]])}

💡 <b>RECOMENDACIÓN:</b>
• Considerar cierre defensivo de BUY
• Mover SL a breakeven
• Esperar confirmación HTF alcista

━━━━━━━━━━━━━━━━━━━━
ℹ️ <i>Alerta única — no se repetirá mientras el pullback esté activo</i>
⏰ {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}
"""
                        enviar_telegram(mensaje_alerta)
                        logger.info(f"  🚨 Alerta PULLBACK bajista enviada — {len(buy_activas)} señales BUY en riesgo")
                except Exception as e:
                    logger.error(f"  ❌ Error verificando señales activas para alerta pullback: {e}")
        else:
            # Pullback resuelto → resetear para que la próxima activación vuelva a notificar
            _estado_pullback.pop(f"pullback_bajista_{simbolo}", None)
        if _sell_confirmado_htf:
            _htf_labels = " | ".join(filter(None, [
                f"4H(${_linea_canal_4h_sop:.0f})" if canal_sell_confirmado_4h else "",
                f"1D(${_linea_canal_1d_sop:.0f})" if canal_sell_confirmado_1d else "",
                f"1W(${_linea_canal_1w_sop:.0f})" if canal_sell_confirmado_1w else "",
            ]))
            logger.info(f"  ✅ Canal SELL confirmado en TFs superiores: {_htf_labels}")
        if _buy_confirmado_htf:
            _htf_labels = " | ".join(filter(None, [
                f"4H(${_linea_canal_4h_res:.0f})" if canal_buy_confirmado_4h else "",
                f"1D(${_linea_canal_1d_res:.0f})" if canal_buy_confirmado_1d else "",
                f"1W(${_linea_canal_1w_res:.0f})" if canal_buy_confirmado_1w else "",
            ]))
            logger.info(f"  ✅ Canal BUY confirmado en TFs superiores: {_htf_labels}")

        # ── Micro-volatilidad y momentum reciente ─────────────────────────────
        if _micro_vol > 1.5:
            if score_sell > score_buy:
                score_sell = min(score_sell + 1, 23)
                logger.info(f"  📈 [1H] Micro-vol {_micro_vol:.2f} (expansión) — +1 SELL")
            elif score_buy > score_sell:
                score_buy = min(score_buy + 1, 23)
                logger.info(f"  📈 [1H] Micro-vol {_micro_vol:.2f} (expansión) — +1 BUY")
        elif _micro_vol < 0.8:
            score_sell = max(0, score_sell - 1)
            score_buy  = max(0, score_buy  - 1)
            logger.info(f"  😴 [1H] Micro-vol {_micro_vol:.2f} (dormido) — -1 ambos scores")
        if _momentum_rec == -1 and en_zona_resist_any:
            score_sell = min(score_sell + 1, 23)
            logger.info(f"  🔻 [1H] Momentum bajista en resistencia — +1 SELL")
        elif _momentum_rec == 1 and en_zona_soporte_any:
            score_buy = min(score_buy + 1, 23)
            logger.info(f"  🔺 [1H] Momentum alcista en soporte — +1 BUY")

        # Umbrales 1H (estrictos para filtrar ruido intradía)
        # Bonus de vela viva: +3 si la vela en curso ya confirmó rebote/rechazo en zona
        if rebote_soporte_live:
            score_buy  = min(score_buy  + 3, 23)
        if rechazo_resist_live:
            score_sell = min(score_sell + 3, 23)

        # ── Snapshot completo de condiciones para backtesting/estudio ─────────
        _condiciones_bd = {
            # Indicadores numéricos
            'rsi': round(float(rsi), 1), 'rsi_prev': round(float(rsi_prev), 1),
            'atr': round(float(atr), 2), 'atr_media': round(float(atr_media), 2),
            'adx': round(float(adx), 1), 'di_plus': round(float(di_plus), 1), 'di_minus': round(float(di_minus), 1),
            'macd': round(float(macd), 4), 'macd_hist': round(float(macd_hist), 4),
            'vol': round(float(vol), 0), 'vol_avg': round(float(vol_avg), 0),
            'score_sell': score_sell, 'score_buy': score_buy,
            # Zonas S/R
            'zrl': round(zrl, 2), 'zrh': round(zrh, 2), 'zsl': round(zsl, 2), 'zsh': round(zsh, 2),
            # Condiciones SELL
            'en_zona_resist': bool(en_zona_resist), 'en_zona_resist_any': bool(en_zona_resist_any),
            'vela_rechazo': bool(vela_rechazo), 'shooting_star': bool(shooting_star),
            'bearish_engulfing': bool(bearish_engulfing), 'bearish_marubozu': bool(bearish_marubozu),
            'doji_resist': bool(doji_resist), 'vol_alto_rechazo': bool(vol_alto_rechazo),
            'rsi_alto_girando': bool(rsi_alto_girando), 'rsi_sobrecompra': bool(rsi_sobrecompra),
            'divergencia_bajista': bool(divergencia_bajista),
            'emas_bajistas': bool(emas_bajistas), 'bajo_ema200': bool(bajo_ema200),
            'estructura_bajista': bool(estructura_bajista),
            'intento_rotura_fallido': bool(intento_rotura_fallido),
            'vol_decreciente': bool(vol_decreciente), 'bb_toca_superior': bool(bb_toca_superior),
            'evening_star': bool(evening_star), 'macd_cruce_bajista': bool(macd_cruce_bajista),
            'macd_negativo': bool(macd_negativo), 'macd_divergencia_bajista': bool(macd_divergencia_bajista),
            'adx_bajista': bool(adx_bajista), 'adx_lateral': bool(adx_lateral),
            'obv_divergencia_bajista': bool(obv_divergencia_bajista), 'obv_decreciente': bool(obv_decreciente),
            'canal_alcista_roto': bool(canal_alcista_roto), 'retest_canal_sell': bool(retest_canal_sell),
            'rechazo_resist_live': bool(rechazo_resist_live), 'rup_sop_1h': bool(_rup_sop_1h),
            'en_resist_sr_interm': bool(_en_resist_sr_1h and not en_zona_resist),
            'cuña_desc': str(_cuña_desc_1h) if _cuña_desc_1h else None,
            'cuña_asc': str(_cuña_asc_1h) if _cuña_asc_1h else None,
            'dt_detectado': bool(_dt_1h), 'v_rev_bajista': bool(v_rev_baj_1h),
            'rebote_bajista': bool(_rebote_baj_1h),
            'fib_nivel': float(_fib_nivel_1h) if _fib_nivel_1h else None,
            'fib_tend': str(_fib_tend_1h) if _fib_nivel_1h else None,
            # Condiciones BUY
            'en_zona_soporte': bool(en_zona_soporte), 'en_zona_soporte_any': bool(en_zona_soporte_any),
            'vela_rebote': bool(vela_rebote), 'hammer': bool(hammer),
            'bullish_engulfing': bool(bullish_engulfing), 'bullish_marubozu': bool(bullish_marubozu),
            'doji_soporte': bool(doji_soporte), 'vol_alto_rebote': bool(vol_alto_rebote),
            'rsi_bajo_girando': bool(rsi_bajo_girando), 'rsi_sobreventa': bool(rsi_sobreventa),
            'divergencia_alcista': bool(divergencia_alcista),
            'emas_alcistas': bool(emas_alcistas), 'sobre_ema200': bool(sobre_ema200),
            'estructura_alcista': bool(estructura_alcista),
            'intento_caida_fallido': bool(intento_caida_fallido),
            'vol_decreciente_sell': bool(vol_decreciente_sell), 'bb_toca_inferior': bool(bb_toca_inferior),
            'morning_star': bool(morning_star), 'macd_cruce_alcista': bool(macd_cruce_alcista),
            'macd_positivo': bool(macd_positivo), 'macd_divergencia_alcista': bool(macd_divergencia_alcista),
            'adx_alcista': bool(adx_alcista),
            'obv_divergencia_alcista': bool(obv_divergencia_alcista), 'obv_creciente': bool(obv_creciente),
            'canal_bajista_roto': bool(canal_bajista_roto), 'retest_canal_buy': bool(retest_canal_buy),
            'rebote_soporte_live': bool(rebote_soporte_live), 'rup_res_1h': bool(_rup_res_1h),
            'en_sop_sr_interm': bool(_en_sop_sr_1h and not en_zona_soporte),
            'ds_detectado': bool(_ds_1h), 'v_rev_alcista': bool(v_rev_alc_1h),
            'rebote_alcista': bool(_rebote_alc_1h),
            # Contexto macro/multi-TF
            'dxy_bias': str(dxy_bias) if dxy_bias else None,
            'cot_bias': str(_cot_bias) if _cot_bias else None,
            'oi_bias': str(_oi_bias) if _oi_bias else None,
            'news_sesgo': str(_sesgo_news), 'news_score': round(float(_sesgo_score), 2),
            'htf_4h': str(_dir_4h), 'htf_1d': str(_dir_1d), 'htf_1w': str(_dir_1w),
            'n_confirm_sell': int(_n_confirm_sell), 'n_confirm_buy': int(_n_confirm_buy),
            'pullback_alcista': bool(pullback_alcista), 'pullback_bajista': bool(pullback_bajista),
        }

        _umbral_max = self.umbral_adaptativo(15, atr, atr_media)   # antes: 12
        _umbral_fue = self.umbral_adaptativo(13, atr, atr_media)   # antes: 11
        _umbral_med = self.umbral_adaptativo(10, atr, atr_media)   # antes: 8
        _umbral_ale = self.umbral_adaptativo(8,  atr, atr_media)   # antes: 6
        senal_sell_maxima = score_sell >= _umbral_max
        senal_sell_fuerte = score_sell >= _umbral_fue
        senal_sell_media  = score_sell >= _umbral_med
        senal_sell_alerta = score_sell >= _umbral_ale
        senal_buy_maxima  = score_buy  >= _umbral_max
        senal_buy_fuerte  = score_buy  >= _umbral_fue
        senal_buy_media   = score_buy  >= _umbral_med
        senal_buy_alerta  = score_buy  >= _umbral_ale

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



        # Clave para alertas de vela viva (caduca al cierre de la vela, TTL 1h)
        clave_vela_live = f"{simbolo}_1H_LIVE_{df.index[-1].strftime('%Y-%m-%d %H')}"
        def ya_enviada_live(tipo): return self.alertas_enviadas.get(f"{clave_vela_live}_{tipo}", 0) > time.time() - 3600
        def marcar_enviada_live(tipo): self.alertas_enviadas[f"{clave_vela_live}_{tipo}"] = time.time()

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
        # IMPORTANTE: usa flags pre-exclusión-mutua (_prep_*) para que un sesgo general
        # en la dirección opuesta no suprima alertas de acción de precio real.
        if rebote_soporte_live and _prep_buy_alerta and not cancelar_buy and not cancelar_buy_rr and not ya_enviada_live('LIVE_BUY'):
            nv = ("🔥 BUY MÁXIMA" if senal_buy_maxima else
                  "🟢 BUY FUERTE" if senal_buy_fuerte else
                  "⚡ BUY MEDIA"  if senal_buy_media  else
                  "👀 BUY ALERTA")
            # SL debajo del mínimo de la vela viva + buffer
            sl_live = round(low_live - atr * 0.3, 2)
            
            # ── TPs LIVE: recalcular desde close_live (no reutilizar tp1_c/tp2_c/tp3_c) ──
            tp1_live = _tp1_viable_buy(resistencias_sr, close_live, sl_live, 1.2,
                                       close_live + atr * params['atr_tp1_mult'])
            _resis_sobre_live = sorted([v for v in resistencias_sr if v > close_live])
            tp2_live = _recortar_tp_compra(
                _tp_desde_sr(_resis_sobre_live, 2, close_live + atr * params['atr_tp2_mult']),
                tp1_live, resistencias_sr, atr)
            tp3_live = _recortar_tp_compra(
                _tp_desde_sr(_resis_sobre_live, 3, close_live + atr * params['atr_tp3_mult']),
                tp2_live, resistencias_sr, atr)
            
            rr_live = rr(close_live, sl_live, tp1_live)
            fecha_live = timestamp_vela_live.strftime('%Y-%m-%d %H:%M')
            hora_envio = timestamp_envio.strftime('%H:%M:%S')
            msg = (f"⚡ <b>REBOTE EN SOPORTE — ORO (XAUUSD) 1H</b>  ← VELA EN CURSO\n"
                   f"━━━━━━━━━━━━━━━━━━━━\n"
                   f"📊 <b>Nivel:</b> {nv} | V-REVERSAL ACTIVO\n"
                   f"💰 <b>Precio actual:</b>  ${close_live:.2f}  (ENTRADA MARKET)\n"
                   f"📉 <b>Low vela:</b>       ${low_live:.2f}  ← tocó soporte ${zsl:.0f}-${zsh:.0f}\n"
                   f"🛑 <b>Stop Loss:</b>      ${sl_live:.2f}  (-${round(close_live - sl_live, 2)})\n"
                   f"━━━━━━━━━━━━━━━━━━━━\n"
                   f"🎯 <b>TP1:</b> ${tp1_live:.2f}  R:R {rr_live}:1\n"
                   f"🎯 <b>TP2:</b> ${tp2_live:.2f}  R:R {rr(close_live, sl_live, tp2_live)}:1\n"
                   f"🎯 <b>TP3:</b> ${tp3_live:.2f}  R:R {rr(close_live, sl_live, tp3_live)}:1\n"
                   f"━━━━━━━━━━━━━━━━━━━━\n"
                   f"📊 <b>Score:</b> {score_buy}/23  📉 <b>RSI:</b> {round(rsi, 1)}  📐 <b>ATR:</b> ${atr:.2f}\n"
                   f"⏱️ <b>TF:</b> 1H\n"
                   f"📅 <b>Estudio:</b> {fecha_live} UTC  🕐 <b>Envío:</b> {hora_envio} UTC")
            _bloquear_buy_live = self.db and (
                self.db.existe_senal_reciente(simbolo_db, "COMPRA", horas=1) or
                self.db.existe_senal_reciente_opuesta(simbolo_db, "COMPRA", horas=1))
            if _bloquear_buy_live:
                logger.info(f"  🚫 BUY LIVE bloqueada: señal reciente o conflicto en BD")
            else:
                if self.db:
                    try:
                        self._guardar_senal({
                            'timestamp': datetime.now(timezone.utc), 'simbolo': simbolo_db,
                            'direccion': 'COMPRA', 'precio_entrada': close_live,
                            'tp1': tp1_live, 'tp2': tp2_live, 'tp3': tp3_live, 'sl': sl_live,
                            'score': score_buy,
                            'indicadores': json.dumps(_condiciones_bd),
                            'patron_velas': f"ReboteVivo:True, Low={low_live:.2f}",
                            'version_detector': 'GOLD 1H-v2.1'
                        })
                    except Exception as e:
                        logger.error(f"  ⚠️ Error BD: {e}")
                _nivel_live_buy = ("MAXIMA" if senal_buy_maxima else "FUERTE" if senal_buy_fuerte else "MEDIA" if senal_buy_media else "ALERTA")
                if self._debe_suprimir_por_evento(_nivel_live_buy):
                    logger.info(f"  🔕 [1H] BUY LIVE suprimida por evento macro")
                else:
                    self.enviar(msg); marcar_enviada_live('LIVE_BUY')

        if rechazo_resist_live and _prep_sell_alerta and not cancelar_sell and not cancelar_sell_rr and not ya_enviada_live('LIVE_SELL'):
            nv = ("🔥 SELL MÁXIMA" if senal_sell_maxima else
                  "🔴 SELL FUERTE" if senal_sell_fuerte else
                  "⚡ SELL MEDIA"  if senal_sell_media  else
                  "👀 SELL ALERTA")
            sl_live = round(high_live + atr * 0.3, 2)
            
            # ── TPs LIVE: recalcular desde close_live (no reutilizar tp1_v/tp2_v/tp3_v) ──
            _sop_debajo_live = sorted([s for s in soportes_sr if s < close_live], reverse=True)
            tp1_live = _tp1_viable_sell(_sop_debajo_live, close_live, sl_live, 1.2,
                                        close_live - atr * params['atr_tp1_mult'])
            tp2_live = _recortar_tp_venta(
                _tp_desde_sr(_sop_debajo_live, 2, close_live - atr * params['atr_tp2_mult']),
                tp1_live, soportes_sr, atr)
            tp3_live = _recortar_tp_venta(
                _tp_desde_sr(_sop_debajo_live, 3, close_live - atr * params['atr_tp3_mult']),
                tp2_live, soportes_sr, atr)
            
            rr_live = rr(close_live, sl_live, tp1_live)
            fecha_live = timestamp_vela_live.strftime('%Y-%m-%d %H:%M')
            hora_envio = timestamp_envio.strftime('%H:%M:%S')
            msg = (f"⚡ <b>RECHAZO EN RESISTENCIA — ORO (XAUUSD) 1H</b>  ← VELA EN CURSO\n"
                   f"━━━━━━━━━━━━━━━━━━━━\n"
                   f"📊 <b>Nivel:</b> {nv} | V-REVERSAL ACTIVO\n"
                   f"💰 <b>Precio actual:</b>  ${close_live:.2f}  (ENTRADA MARKET)\n"
                   f"📈 <b>High vela:</b>       ${high_live:.2f}  ← tocó resist ${zrl:.0f}-${zrh:.0f}\n"
                   f"🛑 <b>Stop Loss:</b>      ${sl_live:.2f}  (+${round(sl_live - close_live, 2)})\n"
                   f"━━━━━━━━━━━━━━━━━━━━\n"
                   f"🎯 <b>TP1:</b> ${tp1_live:.2f}  R:R {rr_live}:1\n"
                   f"🎯 <b>TP2:</b> ${tp2_live:.2f}  R:R {rr(close_live, sl_live, tp2_live)}:1\n"
                   f"🎯 <b>TP3:</b> ${tp3_live:.2f}  R:R {rr(close_live, sl_live, tp3_live)}:1\n"
                   f"━━━━━━━━━━━━━━━━━━━━\n"
                   f"📊 <b>Score:</b> {score_sell}/23  📉 <b>RSI:</b> {round(rsi, 1)}  📐 <b>ATR:</b> ${atr:.2f}\n"
                   f"⏱️ <b>TF:</b> 1H\n"
                   f"📅 <b>Estudio:</b> {fecha_live} UTC  🕐 <b>Envío:</b> {hora_envio} UTC")
            _bloquear_sell_live = self.db and (
                self.db.existe_senal_reciente(simbolo_db, "VENTA", horas=1) or
                self.db.existe_senal_reciente_opuesta(simbolo_db, "VENTA", horas=1))
            if _bloquear_sell_live:
                logger.info(f"  🚫 SELL LIVE bloqueada: señal reciente o conflicto en BD")
            else:
                if self.db:
                    try:
                        self._guardar_senal({
                            'timestamp': datetime.now(timezone.utc), 'simbolo': simbolo_db,
                            'direccion': 'VENTA', 'precio_entrada': close_live,
                            'tp1': tp1_live, 'tp2': tp2_live, 'tp3': tp3_live, 'sl': sl_live,
                            'score': score_sell,
                            'indicadores': json.dumps(_condiciones_bd),
                            'patron_velas': f"RechazoVivo:True, High={high_live:.2f}",
                            'version_detector': 'GOLD 1H-v2.1'
                        })
                    except Exception as e:
                        logger.error(f"  ⚠️ Error BD: {e}")
                _nivel_live_sell = ("MAXIMA" if senal_sell_maxima else "FUERTE" if senal_sell_fuerte else "MEDIA" if senal_sell_media else "ALERTA")
                if self._debe_suprimir_por_evento(_nivel_live_sell):
                    logger.info(f"  🔕 [1H] SELL LIVE suprimida por evento macro")
                else:
                    self.enviar(msg); marcar_enviada_live('LIVE_SELL')

        # ── ALERTAS DE APROXIMACIÓN → SEÑAL ACCIONABLE (pon la orden limit ahora) ──
        if aproximando_resist and not en_zona_resist and not cancelar_sell and not cancelar_sell_rr and _prep_sell_alerta and not self.ya_enviada(f"{clave_vela}_PREP_SELL"):
            nv = ("🔥 SELL MÁXIMA" if senal_sell_maxima else
                  "🔴 SELL FUERTE" if senal_sell_fuerte else
                  "⚡ SELL MEDIA"  if senal_sell_media  else
                  "👀 SELL ALERTA")
            msg = (f"⏳ <b>SETUP SELL 1H — ORO (XAUUSD)</b> | ESPERANDO CONFIRMACIÓN 15M/5M\n"
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
            msg = (f"⏳ <b>SETUP BUY 1H — ORO (XAUUSD)</b> | ESPERANDO CONFIRMACIÓN 15M/5M\n"
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
        _sell_activa = senal_sell_media or (_prep_sell_alerta and _tiene_rechazo_confirmado and senal_sell_alerta)
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
                        msg = (f"✅ <b>CONFIRMACIÓN SELL — ORO 1H</b>\n"
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
                        msg = (f"{nivel} — <b>ORO (XAUUSD) ⏰ INTRADÍA</b>\n"
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
        _buy_activa = senal_buy_media or (_prep_buy_alerta and _tiene_rebote_confirmado and senal_buy_alerta)
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
                        msg = (f"✅ <b>CONFIRMACIÓN BUY — ORO 1H</b>\n"
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
                        msg = (f"{nivel} — <b>ORO (XAUUSD) ⏰ INTRADÍA</b>\n"
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
            msg = (f"❌ <b>CANCELAR SELL LIMIT — ORO (XAUUSD) 1H</b> ❌\n"
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
            msg = (f"❌ <b>CANCELAR BUY LIMIT — ORO (XAUUSD) 1H</b> ❌\n"
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
    logger.info("🚀 Detector ORO 1H intradía iniciado")
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
            logger.info(f"[{ahora_utc.strftime('%Y-%m-%d %H:%M')} UTC] 💤 Sábado — mercado cerrado. Próxima apertura Domingo 18:00 UTC. Revisando en {int(segundos_espera//60)} min...")
            time.sleep(segundos_espera)
            continue

        logger.info(f"\n[{ahora_utc.strftime('%Y-%m-%d %H:%M:%S')}] 🔄 CICLO #{ciclo} - ORO 1H")
        for simbolo, params in SIMBOLOS.items():
            analizar(simbolo, params)
        logger.info(f"⏳ Esperando {CHECK_INTERVAL}s...")
        time.sleep(CHECK_INTERVAL)


if __name__ == '__main__':
    main()
