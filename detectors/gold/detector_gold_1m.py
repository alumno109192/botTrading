"""
DETECTOR GOLD 1M - MICRO-SCALPING ULTRA-RÁPIDO
Análisis de XAUUSD en timeframe 1 minuto para capturar impulsos y breakouts
Diseñado para entrar cuando hay un movimiento grande que los TFs superiores no detectan a tiempo

Filosofía:
  - Detecta breakouts y V-reversals en caliente (no espera cierre de vela 5M)
  - Usa sesgo del 5M como TF superior para confirmar dirección
  - Parámetros ajustados a la volatilidad real de 1 minuto en oro
  - Solo opera en sesión London/NY (07-18 UTC)
  - ADX mínimo 20 — no opera en rango plano
"""
import os
import json
import time
import logging
from datetime import datetime, timezone

from adapters.data_provider import get_ohlcv
from adapters.database import get_db
from adapters.telegram import enviar_telegram as _enviar_telegram_base
from core.base_detector_gold import GoldBaseDetector as BaseDetector
from core import indicators as ind
from services import tf_bias
from services.dxy_bias import get_dxy_bias, ajustar_score_por_dxy
from services.economic_calendar import (
    obtener_aviso_macro, debe_bloquear_trading,
    enviar_alerta_bloqueo,
)
from dotenv import load_dotenv
import pandas as pd

load_dotenv()
logger = logging.getLogger('bottrading')

db = get_db()

# ══════════════════════════════════════════════════════
# CONFIGURACIÓN
# ══════════════════════════════════════════════════════
TELEGRAM_TOKEN     = os.environ.get('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID   = os.environ.get('TELEGRAM_CHAT_ID')
TELEGRAM_THREAD_ID = int(os.environ.get('THREAD_ID_SCALPING') or 0) or None

CHECK_INTERVAL = 30  # 30 segundos — máxima reactividad

_aviso_macro = ""

def enviar_telegram(mensaje):
    sufijo = f"\n⚠️ <b>Evento macro próximo:</b> {_aviso_macro}" if _aviso_macro else ""
    return _enviar_telegram_base(mensaje + sufijo, TELEGRAM_THREAD_ID)


# ══════════════════════════════════════════════════════
# PARÁMETROS — MICRO-SCALP GOLD 1M
# ══════════════════════════════════════════════════════
SIMBOLOS = {
    'XAUUSD': {
        'ticker_yf':      'GC=F',
        'sr_lookback':    60,     # 60 velas 1M = 1h de historia S/R
        'sr_zone_mult':   0.5,    # zona estrecha en 1M
        'atr_tp1_mult':   1.5,    # TP1: 1.5× ATR 1M
        'atr_tp2_mult':   2.5,    # TP2: 2.5× ATR
        'atr_tp3_mult':   3.5,    # TP3: 3.5× ATR (objetivo máximo)
        'atr_min':        2.0,    # ATR mínimo $2 — evita señales en congestión
        'limit_offset_pct': 0.04, # offset muy pequeño en 1M
        'rsi_length':     5,      # RSI ultra-rápido
        'rsi_min_sell':   65.0,
        'rsi_max_buy':    35.0,
        'ema_fast_len':   3,
        'ema_slow_len':   8,
        'ema_trend_len':  20,
        'atr_length':     5,
        'atr_sl_mult':    1.2,    # SL ajustado para 1M
        'vol_mult':       1.5,    # umbral volumen más exigente en 1M
    }
}

# ══════════════════════════════════════════════════════
# CLASE DETECTOR 1M
# ══════════════════════════════════════════════════════
class GoldDetector1M(BaseDetector):

    def analizar(self, simbolo, params):

        # ── Bloqueo FOMC/NFP/CPI ────────────────────────────────────────────
        bloqueado, desc_evento, minutos = debe_bloquear_trading(90)
        if bloqueado:
            logger.warning(f"  🚫 [1M] Trading bloqueado — {desc_evento} en {minutos} min")
            enviar_alerta_bloqueo(desc_evento, minutos, ['1M'])
            return

        # ── Sesión activa: solo London/NY (07-18 UTC) ────────────────────────
        if not self.en_sesion_optima():
            logger.debug(f"  🌙 [1M] Fuera de sesión óptima")
            return

        # ── Aviso macro (no bloquea, aparece en el mensaje) ─────────────────
        self.aviso_macro = obtener_aviso_macro(30, '1M', simbolo)

        try:
            df, is_delayed = get_ohlcv(params['ticker_yf'], period='2d', interval='1m')

            if df is None or df.empty or len(df) < 60:
                logger.warning(f"  ⚠️ [1M] Datos insuficientes para {simbolo}")
                return

            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.droplevel(1)
            if 'Open' not in df.columns:
                if len(df.columns) == 5:
                    df.columns = ['Open', 'High', 'Low', 'Close', 'Volume']
                else:
                    logger.warning(f"  ⚠️ [1M] Columnas inesperadas: {df.columns.tolist()}")
                    return

            close = float(df['Close'].iloc[-1])

            # ── Indicadores base ────────────────────────────────────────────
            atr_series  = ind.calcular_atr(df, params['atr_length'])
            atr         = float(atr_series.iloc[-1])
            atr_media   = float(atr_series.rolling(20).mean().iloc[-1])
            rsi         = float(ind.calcular_rsi(df['Close'], params['rsi_length']).iloc[-1])
            adx, _, _   = ind.calcular_adx(df)
            adx         = float(adx.iloc[-1])
            ema_fast    = df['Close'].ewm(span=params['ema_fast_len']).mean()
            ema_slow    = df['Close'].ewm(span=params['ema_slow_len']).mean()
            ema_trend   = df['Close'].ewm(span=params['ema_trend_len']).mean()

            # ── Volumen relativo ─────────────────────────────────────────────
            vol_actual = float(df['Volume'].iloc[-1])
            vol_medio  = float(df['Volume'].iloc[-20:].mean())

            # ── Zonas S/R dinámicas ──────────────────────────────────────────
            zrl, zrh, zsl, zsh = self.calcular_zonas_sr(
                df, atr, params['sr_lookback'], params['sr_zone_mult'])
            tol = round(atr * 0.3, 2)

            # ── Scoring ──────────────────────────────────────────────────────
            score_buy  = 0
            score_sell = 0

            # 1. Posición vs. EMAs
            if close > float(ema_fast.iloc[-1]) > float(ema_slow.iloc[-1]):
                score_buy += 2
            elif close < float(ema_fast.iloc[-1]) < float(ema_slow.iloc[-1]):
                score_sell += 2

            # Tendencia de EMA lenta (ema_trend)
            if float(ema_fast.iloc[-1]) > float(ema_trend.iloc[-1]):
                score_buy += 1
            elif float(ema_fast.iloc[-1]) < float(ema_trend.iloc[-1]):
                score_sell += 1

            # 2. RSI
            if rsi < params['rsi_max_buy']:
                score_buy += 2
            elif rsi > params['rsi_min_sell']:
                score_sell += 2

            # 3. Patrones de vela (señales fuertes en 1M)
            if ind.patron_envolvente_alcista(df):
                score_buy += 3
                logger.info(f"  🕯️ [1M] Envolvente alcista — +3 BUY")
            if ind.patron_envolvente_bajista(df):
                score_sell += 3
                logger.info(f"  🕯️ [1M] Envolvente bajista — +3 SELL")

            # 4. Stop hunt (señal más fiable en 1M)
            if ind.detectar_stop_hunt_alcista(df, atr):
                score_buy += 4
                logger.info(f"  🎣 [1M] Stop hunt alcista — +4 BUY")
            if ind.detectar_stop_hunt_bajista(df, atr):
                score_sell += 4
                logger.info(f"  🎣 [1M] Stop hunt bajista — +4 SELL")

            # 5. V-Reversal (el principal señalizador de impulsos bruscos)
            v_alc, v_min, v_precio     = ind.detectar_v_reversal_alcista(
                df, atr, lookback=15, min_caida_atr=2.5, min_rebote_atr=2.0)
            v_baj, v_max, v_precio_baj = ind.detectar_v_reversal_bajista(
                df, atr, lookback=15, min_subida_atr=2.5, min_caida_atr=2.0)
            if v_alc:
                score_buy += 5
                logger.info(f"  ⚡ [1M] V-REVERSAL ALCISTA — mín ${v_min:.2f} → ${v_precio:.2f} — +5 BUY")
            if v_baj:
                score_sell += 5
                logger.info(f"  ⚡ [1M] V-REVERSAL BAJISTA — máx ${v_max:.2f} → ${v_precio_baj:.2f} — +5 SELL")

            # 6. Breakout horizontal (el principal capturador de impulsos)
            lkb = params['sr_lookback']
            _rup_sop, _niv_sop = ind.detectar_ruptura_soporte_horizontal(df, atr, lookback=lkb, wing=2)
            _rup_res, _niv_res = ind.detectar_ruptura_resistencia_horizontal(df, atr, lookback=lkb, wing=2)
            if _rup_sop:
                score_sell += 5
                logger.info(f"  💥 [1M] RUPTURA SOPORTE ${_niv_sop:.2f} — +5 SELL")
            if _rup_res:
                score_buy += 5
                logger.info(f"  💥 [1M] RUPTURA RESISTENCIA ${_niv_res:.2f} — +5 BUY")

            # 7. Retest S/R (entrada precisa tras rotura)
            _ret_res, _niv_ret_res = ind.detectar_retest_resistencia(df, atr, lookback=lkb, wing=2)
            _ret_sop, _niv_ret_sop = ind.detectar_retest_soporte(df, atr, lookback=lkb, wing=2)
            if _ret_res:
                score_sell += 4
                logger.info(f"  🔁 [1M] RETEST RESISTENCIA ${_niv_ret_res:.2f} — +4 SELL")
            if _ret_sop:
                score_buy += 4
                logger.info(f"  🔁 [1M] RETEST SOPORTE ${_niv_ret_sop:.2f} — +4 BUY")

            # 8. Zona S/R
            en_zona_resist  = (df['High'].iloc[-1] >= zrl - tol) and (df['High'].iloc[-1] <= zrh + tol)
            en_zona_soporte = (df['Low'].iloc[-1]  >= zsl - tol) and (df['Low'].iloc[-1]  <= zsh + tol)
            if en_zona_resist:
                score_sell += 2
                logger.info(f"  📍 [1M] En zona resistencia ${zrl:.2f}–${zrh:.2f} — +2 SELL")
            if en_zona_soporte:
                score_buy += 2
                logger.info(f"  📍 [1M] En zona soporte ${zsl:.2f}–${zsh:.2f} — +2 BUY")

            # 9. Micro-volatilidad (aceleración de rango)
            _micro_vol = ind.calcular_micro_volatilidad(df, window=10)
            if _micro_vol > 1.5:
                if score_sell > score_buy:
                    score_sell += 1
                elif score_buy > score_sell:
                    score_buy += 1
                logger.info(f"  📈 [1M] Micro-vol {_micro_vol:.2f} (expansión) — +1 dominante")

            max_score = 26

            # ── Ajuste DXY ──────────────────────────────────────────────────
            dxy_bias = get_dxy_bias()
            score_buy, score_sell = ajustar_score_por_dxy(score_buy, score_sell, dxy_bias)

            # ── Filtro volumen ───────────────────────────────────────────────
            score_sell, score_buy, _vol_bajo = self.ajustar_scores_por_volumen(
                score_sell, score_buy, vol_actual, vol_medio, params['vol_mult'])
            if _vol_bajo:
                logger.info(f"  ⚠️ [1M] Volumen bajo — scores penalizados -3")

            # ── ADX mínimo: no operar en laterales ──────────────────────────
            _ADX_MIN = 20
            if adx < _ADX_MIN:
                logger.info(f"  😴 [1M] ADX {adx:.1f} < {_ADX_MIN} — mercado plano, señales bloqueadas")
                return

            # ── Umbral adaptativo ────────────────────────────────────────────
            _umbral = self.umbral_adaptativo(11, atr, atr_media)
            senal_sell = score_sell >= _umbral
            senal_buy  = score_buy  >= _umbral

            # ── Exclusión mutua ──────────────────────────────────────────────
            if senal_sell and senal_buy:
                if score_sell >= score_buy:
                    senal_buy = False
                else:
                    senal_sell = False

            fecha = df.index[-1].strftime('%Y-%m-%d %H:%M')
            logger.info(f"  [1M] {fecha}  ${close:.2f}  SELL:{score_sell}  BUY:{score_buy}  "
                        f"RSI:{rsi:.1f}  ADX:{adx:.1f}  ATR:{atr:.2f}")

            # ── Guard anti-duplicados ────────────────────────────────────────
            _clave = simbolo
            if _clave in self.ultimo_analisis:
                ul = self.ultimo_analisis[_clave]
                if (ul['fecha'] == fecha and
                        abs(ul['score_sell'] - score_sell) <= 1 and
                        abs(ul['score_buy'] - score_buy) <= 1):
                    return
            self.ultimo_analisis[_clave] = {
                'fecha': fecha, 'score_sell': score_sell, 'score_buy': score_buy}

            # ── Sesgo TF superior (5M): confirma dirección ───────────────────
            _ok_sell = True; _ok_buy = True; _desc_sell = ""; _desc_buy = ""
            if senal_sell:
                _ok_sell, _desc_sell = tf_bias.verificar_confluencia(
                    simbolo, '1M', tf_bias.BIAS_BEARISH)
                if not _ok_sell:
                    logger.info(f"  🚫 [1M] SELL bloqueada por TF superior: {_desc_sell[:60]}")
                    senal_sell = False
            if senal_buy:
                _ok_buy, _desc_buy = tf_bias.verificar_confluencia(
                    simbolo, '1M', tf_bias.BIAS_BULLISH)
                if not _ok_buy:
                    logger.info(f"  🚫 [1M] BUY bloqueada por TF superior: {_desc_buy[:60]}")
                    senal_buy = False

            if not senal_sell and not senal_buy:
                return

            # ── Entradas y niveles ───────────────────────────────────────────
            atr_ef  = max(atr, params['atr_min'])
            offset  = params['limit_offset_pct']
            sell_limit = round(close * (1 + offset / 100), 2)
            buy_limit  = round(close * (1 - offset / 100), 2)

            sl_venta  = round(close + atr_ef * params['atr_sl_mult'], 2)
            sl_compra = round(close - atr_ef * params['atr_sl_mult'], 2)

            # VATR
            _vol_avg20  = float(df['Volume'].rolling(20).mean().iloc[-1])
            _vol_last   = float(df['Volume'].iloc[-1])
            _vol_factor = min(max(_vol_last / _vol_avg20, 0.75), 1.50) if _vol_avg20 > 0 else 1.0

            tp1_v = round(sell_limit - atr_ef * params['atr_tp1_mult'] * _vol_factor, 2)
            tp2_v = round(sell_limit - atr_ef * params['atr_tp2_mult'] * _vol_factor, 2)
            tp3_v = round(sell_limit - atr_ef * params['atr_tp3_mult'] * _vol_factor, 2)
            tp1_c = round(buy_limit  + atr_ef * params['atr_tp1_mult'] * _vol_factor, 2)
            tp2_c = round(buy_limit  + atr_ef * params['atr_tp2_mult'] * _vol_factor, 2)
            tp3_c = round(buy_limit  + atr_ef * params['atr_tp3_mult'] * _vol_factor, 2)

            def rr(entry, sl, tp):
                return round(abs(tp - entry) / abs(sl - entry), 1) if abs(sl - entry) > 0 else 0

            _RR_MIN = 1.3
            _simbolo_db = f"{simbolo}_1M"

            _condiciones_bd = {
                'rsi': round(rsi, 1), 'atr': round(atr, 2), 'adx': round(adx, 1),
                'vol_actual': round(vol_actual, 0), 'vol_medio': round(vol_medio, 0),
                'score_sell': score_sell, 'score_buy': score_buy,
                'zrl': round(zrl, 2), 'zrh': round(zrh, 2),
                'zsl': round(zsl, 2), 'zsh': round(zsh, 2),
                'v_rev_alc': bool(v_alc), 'v_rev_baj': bool(v_baj),
                'rup_res': bool(_rup_res), 'rup_sop': bool(_rup_sop),
                'retest_res': bool(_ret_res), 'retest_sop': bool(_ret_sop),
                'envolvente_alc': bool(ind.patron_envolvente_alcista(df)),
                'envolvente_baj': bool(ind.patron_envolvente_bajista(df)),
                'dxy_bias': str(dxy_bias) if dxy_bias else None,
                'vol_factor': round(_vol_factor, 3),
            }

            # ────────────────────────────────────────────────────────────────
            # SEÑAL SELL
            # ────────────────────────────────────────────────────────────────
            if senal_sell:
                rr_tp1 = rr(sell_limit, sl_venta, tp1_v)
                if rr_tp1 < _RR_MIN:
                    logger.warning(f"  ⛔ [1M] SELL bloqueada R:R {rr_tp1}:1 < {_RR_MIN}")
                else:
                    _clave_as = f"{simbolo}_1m_sell"
                    _cooldown = self.alertas_enviadas.get(_clave_as, 0)
                    if time.time() - _cooldown < 300:  # 5 min cooldown
                        logger.info(f"  ⏳ [1M] SELL cooldown activo — {int(300-(time.time()-_cooldown))}s restantes")
                    else:
                        # Guardar en BD
                        senal_id = None
                        if self.db and not self.db.existe_senal_activa_tf(_simbolo_db):
                            try:
                                senal_id = self._guardar_senal({
                                    'timestamp': datetime.now(timezone.utc),
                                    'simbolo': _simbolo_db,
                                    'direccion': 'VENTA',
                                    'precio_entrada': round(close, 2),
                                    'tp1': tp1_v, 'tp2': tp2_v, 'tp3': tp3_v,
                                    'sl': round(sl_venta, 2),
                                    'score': score_sell,
                                    'indicadores': json.dumps(_condiciones_bd),
                                    'patron_velas': 'breakout_impulso_1m',
                                    'version_detector': '1M-v1',
                                })
                            except Exception as _e:
                                logger.error(f"  ⚠️ [1M] Error guardando SELL: {_e}")

                        _conf_txt = f"\n📊 Confluencia: {_desc_sell[:60]}" if _desc_sell else ""
                        _id_txt   = f"\n🆔 ID: #{senal_id}" if senal_id else ""
                        msg = (
                            f"⚡ <b>📛 IMPULSO SELL — GOLD 1M</b>\n"
                            f"━━━━━━━━━━━━━━━━━━━━\n"
                            f"💰 Precio:  ${round(close, 2)}\n"
                            f"📍 <b>SELL LIMIT: ${sell_limit}</b>\n"
                            f"🛑 SL: ${round(sl_venta, 2)}  ({round(sl_venta - sell_limit, 1):+.1f} pts)\n"
                            f"🎯 TP1: ${tp1_v}  R:R {rr_tp1}:1\n"
                            f"💎 <b>TP2: ${tp2_v}  R:R {rr(sell_limit, sl_venta, tp2_v)}:1</b>\n"
                            f"🎯 TP3: ${tp3_v}  R:R {rr(sell_limit, sl_venta, tp3_v)}:1\n"
                            f"━━━━━━━━━━━━━━━━━━━━\n"
                            f"📊 Score: {score_sell}/{max_score}  RSI:{round(rsi,1)}  ADX:{round(adx,1)}"
                            f"  Vol×{round(_vol_factor,2)}\n"
                            f"⏱️ 1M  📅 {fecha}"
                            f"{_conf_txt}{_id_txt}"
                        )
                        self.enviar(msg)
                        self.alertas_enviadas[_clave_as] = time.time()
                        if self.db:
                            self.db.set_antispam(_clave_as, self.alertas_enviadas[_clave_as])
                        tf_bias.publicar_sesgo(simbolo, '1M', tf_bias.BIAS_BEARISH, score_sell)
                        logger.info(f"  ✅ [1M] SELL enviada (score {score_sell}/{max_score})")

            # ────────────────────────────────────────────────────────────────
            # SEÑAL BUY
            # ────────────────────────────────────────────────────────────────
            if senal_buy:
                rr_tp1 = rr(buy_limit, sl_compra, tp1_c)
                if rr_tp1 < _RR_MIN:
                    logger.warning(f"  ⛔ [1M] BUY bloqueada R:R {rr_tp1}:1 < {_RR_MIN}")
                else:
                    _clave_ab = f"{simbolo}_1m_buy"
                    _cooldown = self.alertas_enviadas.get(_clave_ab, 0)
                    if time.time() - _cooldown < 300:  # 5 min cooldown
                        logger.info(f"  ⏳ [1M] BUY cooldown activo — {int(300-(time.time()-_cooldown))}s restantes")
                    else:
                        # Guardar en BD
                        senal_id = None
                        if self.db and not self.db.existe_senal_activa_tf(_simbolo_db):
                            try:
                                senal_id = self._guardar_senal({
                                    'timestamp': datetime.now(timezone.utc),
                                    'simbolo': _simbolo_db,
                                    'direccion': 'COMPRA',
                                    'precio_entrada': round(close, 2),
                                    'tp1': tp1_c, 'tp2': tp2_c, 'tp3': tp3_c,
                                    'sl': round(sl_compra, 2),
                                    'score': score_buy,
                                    'indicadores': json.dumps(_condiciones_bd),
                                    'patron_velas': 'breakout_impulso_1m',
                                    'version_detector': '1M-v1',
                                })
                            except Exception as _e:
                                logger.error(f"  ⚠️ [1M] Error guardando BUY: {_e}")

                        _conf_txt = f"\n📊 Confluencia: {_desc_buy[:60]}" if _desc_buy else ""
                        _id_txt   = f"\n🆔 ID: #{senal_id}" if senal_id else ""
                        msg = (
                            f"⚡ <b>📗 IMPULSO BUY — GOLD 1M</b>\n"
                            f"━━━━━━━━━━━━━━━━━━━━\n"
                            f"💰 Precio:  ${round(close, 2)}\n"
                            f"📍 <b>BUY LIMIT: ${buy_limit}</b>\n"
                            f"🛑 SL: ${round(sl_compra, 2)}  ({round(sl_compra - buy_limit, 1):+.1f} pts)\n"
                            f"🎯 TP1: ${tp1_c}  R:R {rr_tp1}:1\n"
                            f"💎 <b>TP2: ${tp2_c}  R:R {rr(buy_limit, sl_compra, tp2_c)}:1</b>\n"
                            f"🎯 TP3: ${tp3_c}  R:R {rr(buy_limit, sl_compra, tp3_c)}:1\n"
                            f"━━━━━━━━━━━━━━━━━━━━\n"
                            f"📊 Score: {score_buy}/{max_score}  RSI:{round(rsi,1)}  ADX:{round(adx,1)}"
                            f"  Vol×{round(_vol_factor,2)}\n"
                            f"⏱️ 1M  📅 {fecha}"
                            f"{_conf_txt}{_id_txt}"
                        )
                        self.enviar(msg)
                        self.alertas_enviadas[_clave_ab] = time.time()
                        if self.db:
                            self.db.set_antispam(_clave_ab, self.alertas_enviadas[_clave_ab])
                        tf_bias.publicar_sesgo(simbolo, '1M', tf_bias.BIAS_BULLISH, score_buy)
                        logger.info(f"  ✅ [1M] BUY enviada (score {score_buy}/{max_score})")

        except Exception as e:
            logger.error(f"  ❌ [1M] Error en análisis {simbolo}: {e}", exc_info=True)


# ══════════════════════════════════════════════════════
# BUCLE PRINCIPAL
# ══════════════════════════════════════════════════════
def main():
    logger.info("🚀 [1M] Iniciando detector Gold 1M micro-scalp")
    detector = GoldDetector1M(db=db)
    # Cargar anti-spam persistido desde BD
    if db:
        for simbolo in SIMBOLOS:
            for sfx in ('sell', 'buy'):
                _k = f"{simbolo}_1m_{sfx}"
                _ts = db.get_antispam(_k)
                if _ts:
                    detector.alertas_enviadas[_k] = _ts

    while True:
        for simbolo, params in SIMBOLOS.items():
            detector.analizar(simbolo, params)
        time.sleep(CHECK_INTERVAL)


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    main()
