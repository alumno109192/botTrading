"""
DETECTOR EURUSD 15M - SCALPING
Análisis de EUR/USD en timeframe 15 minutos para operaciones intradiarias
TPs dinámicos basados en ATR (×1.5 / ×2.5 / ×4.0)
"""
import os
from services import tf_bias

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

from adapters.database import get_db
db = get_db()

TELEGRAM_TOKEN   = os.environ.get('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID   = os.environ.get('TELEGRAM_CHAT_ID')
TELEGRAM_THREAD_ID = int(os.environ.get('THREAD_ID_SCALPING') or 0) or None

CHECK_INTERVAL = 2 * 60  # cada 2 minutos (scalping)

fmt = lambda v: f"{v:.4f}"  # EURUSD: 4 decimales

# ══════════════════════════════════════
# PARÁMETROS — SCALPING EURUSD 15M
# ══════════════════════════════════════
SIMBOLOS = {
    'EURUSD': {
        'ticker_yf':          'EURUSD=X',   # EUR/USD en Yahoo Finance
        'zona_resist_high':   1.1950,       # Resistencia intradiaria abr-2026
        'zona_resist_low':    1.1850,
        'zona_soporte_high':  1.1600,       # Soporte intradiario abr-2026
        'zona_soporte_low':   1.1500,
        'tolerancia':         0.0020,       # 20 pips
        'limit_offset_pct':   0.04,         # 4 pips offset (scalping)
        'anticipar_velas':    2,
        'cancelar_dist':      0.5,
        'rsi_length':         9,
        'rsi_min_sell':       65.0,
        'rsi_max_buy':        35.0,
        'ema_fast_len':       5,
        'ema_slow_len':       13,
        'ema_trend_len':      50,
        'atr_length':         10,
        'atr_sl_mult':        1.5,
        'atr_tp1_mult':       1.5,
        'atr_tp2_mult':       2.5,
        'atr_tp3_mult':       4.0,
        'vol_mult':           1.2,
        'min_score':          3,
        'max_perdidas_dia':   3,
    }
}

alertas_enviadas = {}
ultimo_analisis  = {}
perdidas_consecutivas = 0


# ══════════════════════════════════════
# INDICADORES
# ══════════════════════════════════════
from core.indicators import calcular_rsi, calcular_atr, calcular_adx, patron_envolvente_alcista, patron_envolvente_bajista

# ══════════════════════════════════════
# ANÁLISIS PRINCIPAL
# ══════════════════════════════════════
def analizar(simbolo, params):
    global perdidas_consecutivas
    try:
        df = yf.download(params['ticker_yf'], period='5d', interval='15m', progress=False)
        if df.empty or len(df) < 50:
            print(f"⚠️ Datos insuficientes para {simbolo}"); return
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df = df.copy()

        rsi   = calcular_rsi(df['Close'], params['rsi_length'])
        ema_f = df['Close'].ewm(span=params['ema_fast_len'], adjust=False).mean()
        ema_s = df['Close'].ewm(span=params['ema_slow_len'], adjust=False).mean()
        ema_t = df['Close'].ewm(span=params['ema_trend_len'], adjust=False).mean()
        atr   = calcular_atr(df, params['atr_length'])
        adx, _, _   = calcular_adx(df, params['atr_length'])

        close   = float(df['Close'].iloc[-1])
        high    = float(df['High'].iloc[-1])
        low     = float(df['Low'].iloc[-1])
        open_   = float(df['Open'].iloc[-1])
        vol     = float(df['Volume'].iloc[-1]) if 'Volume' in df.columns else 0.0
        rsi_v   = float(rsi.iloc[-1])
        atr_v   = float(atr.iloc[-1])
        adx_v   = float(adx.iloc[-1])
        ema_f_v = float(ema_f.iloc[-1])
        ema_s_v = float(ema_s.iloc[-1])
        ema_t_v = float(ema_t.iloc[-1])
        vol_avg = float(df['Volume'].iloc[-21:-1].mean()) if ('Volume' in df.columns and len(df) > 20) else 0.0
        fecha   = df.index[-1].strftime('%Y-%m-%d %H:%M')

        zrh = params['zona_resist_high']; zrl = params['zona_resist_low']
        zsh = params['zona_soporte_high']; zsl = params['zona_soporte_low']
        tol = params['tolerancia'];        cd  = params['cancelar_dist']
        av  = params['anticipar_velas']

        avg_candle_range = float((df['High'] - df['Low']).iloc[-7:-1].mean())
        en_zona_resist       = (high >= zrl - tol) and (high <= zrh + tol)
        en_zona_soporte      = (low  >= zsl - tol) and (low  <= zsh + tol)
        aproximando_resist   = zrl - close > 0 and zrl - close < avg_candle_range * av
        aproximando_soporte  = close - zsh > 0 and close - zsh < avg_candle_range * av
        cancelar_sell = close > zrh * (1 + cd / 100)
        cancelar_buy  = close < zsl * (1 - cd / 100)

        lop       = params['limit_offset_pct']
        sell_lim  = close * (1 + lop / 100)
        buy_lim   = close * (1 - lop / 100)
        sl_venta  = sell_lim + atr_v * params['atr_sl_mult']
        sl_compra = buy_lim  - atr_v * params['atr_sl_mult']
        tp1_v = round(sell_lim - atr_v * params['atr_tp1_mult'], 5)
        tp2_v = round(sell_lim - atr_v * params['atr_tp2_mult'], 5)
        tp3_v = round(sell_lim - atr_v * params['atr_tp3_mult'], 5)
        tp1_c = round(buy_lim  + atr_v * params['atr_tp1_mult'], 5)
        tp2_c = round(buy_lim  + atr_v * params['atr_tp2_mult'], 5)
        tp3_c = round(buy_lim  + atr_v * params['atr_tp3_mult'], 5)

        def rr(limit, sl, tp):
            d = abs(sl - limit)
            return round(abs(tp - limit) / d, 1) if d > 0 else 0

        rr_sell_tp1 = rr(sell_lim, sl_venta,  tp1_v)
        rr_buy_tp1  = rr(buy_lim,  sl_compra, tp1_c)

        def pips(v): return round(v * 10000)

        # ── SCORING ──
        score_sell = 0; score_buy = 0

        # Zonas
        if en_zona_resist or aproximando_resist:  score_sell += 2
        if en_zona_soporte or aproximando_soporte: score_buy += 2

        # RSI
        if rsi_v >= params['rsi_min_sell']:   score_sell += 2
        elif rsi_v >= 58:                     score_sell += 1
        if rsi_v <= params['rsi_max_buy']:    score_buy  += 2
        elif rsi_v <= 42:                     score_buy  += 1

        # EMAs (cruce)
        ema_f_prev = float(ema_f.iloc[-2]); ema_s_prev = float(ema_s.iloc[-2])
        if ema_f_v < ema_s_v:
            score_sell += 2
            if ema_f_prev >= ema_s_prev: score_sell += 1
        if ema_f_v > ema_s_v:
            score_buy += 2
            if ema_f_prev <= ema_s_prev: score_buy += 1

        # Tendencia EMA50
        if close < ema_t_v: score_sell += 1
        else:               score_buy  += 1

        # ADX
        if adx_v > 25:
            if score_sell > score_buy: score_sell += 1
            else:                      score_buy  += 1

        # Volumen (EURUSD spot tiene volumen de tick, no real)
        if vol_avg > 0 and vol > vol_avg * params['vol_mult']:
            if score_sell > score_buy: score_sell += 1
            else:                      score_buy  += 1

        # Patrón velas
        if patron_envolvente_bajista(df): score_sell += 2
        if patron_envolvente_alcista(df): score_buy  += 2

        # Vela actual
        if close < open_: score_sell += 1
        else:             score_buy  += 1

        max_score = 15

        # Anti-spam por vela
        if simbolo in ultimo_analisis:
            ua = ultimo_analisis[simbolo]
            if (ua['fecha'] == fecha and
                    abs(ua['score_sell'] - score_sell) <= 1 and
                    abs(ua['score_buy']  - score_buy)  <= 1):
                print(f"  ℹ️  {fecha} ya analizado - Sin cambios"); return
        ultimo_analisis[simbolo] = {'fecha': fecha, 'score_sell': score_sell, 'score_buy': score_buy}

        print(f"  📅 {fecha} | 💰 {fmt(close)} | "
              f"SELL:{score_sell}/{max_score} BUY:{score_buy}/{max_score} | "
              f"RSI:{round(rsi_v, 1)} ATR:{pips(atr_v)}pip")

        clave_vela = f"{simbolo}_{fecha}"
        def ya_enviada(tipo):    return alertas_enviadas.get(f"{clave_vela}_{tipo}", 0) > time.time() - 172800
        def marcar_enviada(tipo): alertas_enviadas[f"{clave_vela}_{tipo}"] = time.time()

        # R:R mínimo 1.2
        if rr_sell_tp1 < 1.2: cancelar_sell = True
        if rr_buy_tp1  < 1.2: cancelar_buy  = True

        senal_sell_fuerte = score_sell >= 8
        senal_sell_media  = score_sell >= 5
        senal_sell_scalp  = score_sell >= params['min_score']
        senal_buy_fuerte  = score_buy  >= 8
        senal_buy_media   = score_buy  >= 5
        senal_buy_scalp   = score_buy  >= params['min_score']

        # Exclusión mutua
        if senal_sell_scalp and senal_buy_scalp:
            if score_sell >= score_buy:
                senal_buy_fuerte = senal_buy_media = senal_buy_scalp = False
                print(f"  ⚖️ Exclusión mutua: BUY suprimida (SELL {score_sell} >= BUY {score_buy})")
            else:
                senal_sell_fuerte = senal_sell_media = senal_sell_scalp = False
                print(f"  ⚖️ Exclusión mutua: SELL suprimida (BUY {score_buy} > SELL {score_sell})")

        # ── PUBLICAR + FILTRO CONFLUENCIA MULTI-TF (EURUSD 15M) ──
        _sesgo_dir = tf_bias.BIAS_BEARISH if score_sell > score_buy else tf_bias.BIAS_BULLISH if score_buy > score_sell else tf_bias.BIAS_NEUTRAL
        tf_bias.publicar_sesgo(simbolo, '15M', _sesgo_dir, max(score_sell, score_buy))
        _conf_sell = ""; _conf_buy = ""
        if senal_sell_scalp:
            _ok, _desc = tf_bias.verificar_confluencia(simbolo, '15M', tf_bias.BIAS_BEARISH)
            if not _ok:
                print(f"  🚫 SELL bloqueada por TF superior: {_desc[:80]}")
                senal_sell_fuerte = senal_sell_media = senal_sell_scalp = False
            else:
                _conf_sell = _desc
        if senal_buy_scalp:
            _ok, _desc = tf_bias.verificar_confluencia(simbolo, '15M', tf_bias.BIAS_BULLISH)
            if not _ok:
                print(f"  🚫 BUY bloqueada por TF superior: {_desc[:80]}")
                senal_buy_fuerte = senal_buy_media = senal_buy_scalp = False
            else:
                _conf_buy = _desc

        # ── SEÑAL SELL ──
        if senal_sell_scalp and not cancelar_sell:
            nivel      = ("🔥 SELL FUERTE" if senal_sell_fuerte else
                          "🔴 SELL MEDIA"  if senal_sell_media  else
                          "⚡ SCALP SELL")
            tipo_clave = ("SELL_FUE" if senal_sell_fuerte else
                          "SELL_MED" if senal_sell_media  else
                          "SCALP_SEL")
            if not ya_enviada(tipo_clave):
                msg = (f"{nivel} — <b>EURUSD (15M) ⚡ SCALPING</b>\n"
                       f"━━━━━━━━━━━━━━━━━━━━\n"
                       f"💰 <b>Precio:</b>     {fmt(close)}\n"
                       f"📌 <b>SELL LIMIT:</b> {fmt(sell_lim)}\n"
                       f"🛑 <b>Stop Loss:</b>  {fmt(sl_venta)}  (+{pips(sl_venta - sell_lim)} pip)\n"
                       f"━━━━━━━━━━━━━━━━━━━━\n"
                       f"🎯 <b>TP1:</b> {fmt(tp1_v)}  R:R {rr(sell_lim, sl_venta, tp1_v)}:1\n"
                       f"🎯 <b>TP2:</b> {fmt(tp2_v)}  R:R {rr(sell_lim, sl_venta, tp2_v)}:1\n"
                       f"🎯 <b>TP3:</b> {fmt(tp3_v)}  R:R {rr(sell_lim, sl_venta, tp3_v)}:1\n"
                       f"━━━━━━━━━━━━━━━━━━━━\n"
                       f"📊 <b>Score:</b> {score_sell}/{max_score}  📉 <b>RSI:</b> {round(rsi_v, 1)}\n"
                       f"📐 <b>ATR 15M:</b> {pips(atr_v)} pip\n"
                       f"⏱️ <b>TF:</b> 15M  📅 {fecha}\n"
                       f"🔒 INTRADÍA — Cerrar antes del cierre de sesión")
                if _conf_sell:
                    msg += f"\n━━━━━━━━━━━━━━━━━━━━\n{_conf_sell}"
                enviar_telegram(msg)
                marcar_enviada(tipo_clave)

        # ── SEÑAL BUY ──
        if senal_buy_scalp and not cancelar_buy:
            nivel      = ("🔥 BUY FUERTE" if senal_buy_fuerte else
                          "🟢 BUY MEDIA"  if senal_buy_media  else
                          "⚡ SCALP BUY")
            tipo_clave = ("BUY_FUE" if senal_buy_fuerte else
                          "BUY_MED" if senal_buy_media  else
                          "SCALP_BUY")
            if not ya_enviada(tipo_clave):
                msg = (f"{nivel} — <b>EURUSD (15M) ⚡ SCALPING</b>\n"
                       f"━━━━━━━━━━━━━━━━━━━━\n"
                       f"💰 <b>Precio:</b>    {fmt(close)}\n"
                       f"📌 <b>BUY LIMIT:</b> {fmt(buy_lim)}\n"
                       f"🛑 <b>Stop Loss:</b> {fmt(sl_compra)}  (-{pips(buy_lim - sl_compra)} pip)\n"
                       f"━━━━━━━━━━━━━━━━━━━━\n"
                       f"🎯 <b>TP1:</b> {fmt(tp1_c)}  R:R {rr(buy_lim, sl_compra, tp1_c)}:1\n"
                       f"🎯 <b>TP2:</b> {fmt(tp2_c)}  R:R {rr(buy_lim, sl_compra, tp2_c)}:1\n"
                       f"🎯 <b>TP3:</b> {fmt(tp3_c)}  R:R {rr(buy_lim, sl_compra, tp3_c)}:1\n"
                       f"━━━━━━━━━━━━━━━━━━━━\n"
                       f"📊 <b>Score:</b> {score_buy}/{max_score}  📉 <b>RSI:</b> {round(rsi_v, 1)}\n"
                       f"📐 <b>ATR 15M:</b> {pips(atr_v)} pip\n"
                       f"⏱️ <b>TF:</b> 15M  📅 {fecha}\n"
                       f"🔒 INTRADÍA — Cerrar antes del cierre de sesión")
                if _conf_buy:
                    msg += f"\n━━━━━━━━━━━━━━━━━━━━\n{_conf_buy}"
                enviar_telegram(msg)
                marcar_enviada(tipo_clave)

    except Exception as e:
        print(f"❌ Error analizando {simbolo}: {e}")

# ══════════════════════════════════════
# MAIN
# ══════════════════════════════════════
def main():
    enviar_telegram("🚀 <b>Detector EURUSD 15M — SCALPING iniciado</b>\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    "📊 Monitorizando: EURUSD=X\n"
                    "⏱️ Timeframe: 15M  |  Modo: ⚡ SCALPING\n"
                    "🔄 Revisión cada 2 minutos\n"
                    "📐 TPs dinámicos basados en ATR (×1.5 / ×2.5 / ×4.0)\n"
                    "🔒 Señales para abrir y cerrar en el mismo día\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    "🔴 Resistencia: 1.1850 - 1.1950\n"
                    "🟢 Soporte:     1.1500 - 1.1600")
    ciclo = 0
    while True:
        ciclo += 1
        print(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 🔄 CICLO #{ciclo} - EURUSD 15M SCALPING")
        for simbolo, params in SIMBOLOS.items():
            print(f"🔍 Analizando {simbolo} [EURUSD 15M]...")
            analizar(simbolo, params)
        print(f"⏳ Esperando 2 minutos...")
        time.sleep(CHECK_INTERVAL)

if __name__ == '__main__':
    main()
