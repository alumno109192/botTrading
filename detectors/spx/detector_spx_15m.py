"""
DETECTOR SPX500 15M - SCALPING
Análisis de S&P 500 en timeframe 15 minutos para operaciones intradiarias
TPs dinámicos basados en ATR (×1.5 / ×2.5 / ×4.0)
"""
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
import tf_bias

import yfinance as yf
import pandas as pd
import numpy as np
import requests
import time
import json
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

db = None
try:
    turso_url = os.environ.get('TURSO_DATABASE_URL')
    turso_token = os.environ.get('TURSO_AUTH_TOKEN')
    if turso_url and turso_token:
        from db_manager import DatabaseManager
        db = DatabaseManager()
        print("✅ BD activada (SPX 15M)")
    else:
        print("⚠️  Sin BD - SPX 15M funcionará sin tracking")
except Exception as e:
    db = None

TELEGRAM_TOKEN   = os.environ.get('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID   = os.environ.get('TELEGRAM_CHAT_ID')
TELEGRAM_THREAD_ID = int(os.environ.get('THREAD_ID_SCALPING') or 0) or None

CHECK_INTERVAL = 2 * 60  # cada 2 minutos (scalping)

# ══════════════════════════════════════
# PARÁMETROS — SCALPING SPX 15M
# ══════════════════════════════════════
SIMBOLOS = {
    'SPX500': {
        'ticker_yf':          'ES=F',        # E-Mini S&P 500 Futures (volumen real en 15M)
        'zona_resist_high':   6850.0,        # Resistencia intradiaria abr-2026
        'zona_resist_low':    6750.0,
        'zona_soporte_high':  6600.0,        # Soporte intradiario abr-2026
        'zona_soporte_low':   6500.0,        # Mínimo semana 10-abr: 6534
        'tolerancia':         20.0,
        'limit_offset_pct':   0.10,          # Offset pequeño (scalping)
        'anticipar_velas':    2,
        'cancelar_dist':      1.5,
        'rsi_length':         9,             # RSI sensible
        'rsi_min_sell':       65.0,
        'rsi_max_buy':        35.0,
        'ema_fast_len':       5,
        'ema_slow_len':       13,
        'ema_trend_len':      50,            # Tendencia corto plazo
        'atr_length':         10,
        'atr_sl_mult':        1.5,
        'atr_tp1_mult':       1.5,
        'atr_tp2_mult':       2.5,
        'atr_tp3_mult':       4.0,
        'vol_mult':           1.2,
        'min_score':          3,             # Score mínimo
        'max_perdidas_dia':   3,
    }
}

alertas_enviadas = {}
ultimo_analisis  = {}
perdidas_consecutivas = 0

# ══════════════════════════════════════
# TELEGRAM
# ══════════════════════════════════════
def enviar_telegram(mensaje):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": mensaje, "parse_mode": "HTML"}
        if TELEGRAM_THREAD_ID:
            payload["message_thread_id"] = TELEGRAM_THREAD_ID
        r = requests.post(url, json=payload, timeout=10)
        if r.status_code == 200:
            print(f"✅ Telegram enviado → {r.status_code}")
        else:
            print(f"❌ Error Telegram → {r.status_code}")
    except Exception as e:
        print(f"❌ Error Telegram: {e}")

# ══════════════════════════════════════
# INDICADORES
# ══════════════════════════════════════
def calcular_rsi(series, length):
    delta = series.diff()
    gain  = delta.clip(lower=0)
    loss  = -delta.clip(upper=0)
    avg_g = gain.ewm(com=length - 1, min_periods=length).mean()
    avg_l = loss.ewm(com=length - 1, min_periods=length).mean()
    return 100 - (100 / (1 + avg_g / avg_l))

def calcular_atr(df, length):
    h = df['High']; l = df['Low']; c = df['Close']
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    return tr.rolling(length).mean()

def calcular_adx(df, length=10):
    h = df['High']; l = df['Low']
    plus_dm  = h.diff().clip(lower=0)
    minus_dm = (-l.diff()).clip(lower=0)
    atr = calcular_atr(df, length)
    plus_di  = 100 * (plus_dm.ewm(alpha=1/length, min_periods=length).mean() / atr.replace(0, np.nan))
    minus_di = 100 * (minus_dm.ewm(alpha=1/length, min_periods=length).mean() / atr.replace(0, np.nan))
    dx = 100 * ((plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan))
    return dx.ewm(alpha=1/length).mean()

def patron_envolvente_bajista(df):
    c1 = df.iloc[-2]; c2 = df.iloc[-1]
    return (c1['Close'] > c1['Open'] and c2['Close'] < c2['Open'] and
            c2['Close'] < c1['Open'] and c2['Open'] > c1['Close'])

def patron_envolvente_alcista(df):
    c1 = df.iloc[-2]; c2 = df.iloc[-1]
    return (c1['Close'] < c1['Open'] and c2['Close'] > c2['Open'] and
            c2['Close'] > c1['Open'] and c2['Open'] < c1['Close'])

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
        adx   = calcular_adx(df, params['atr_length'])

        close   = float(df['Close'].iloc[-1])
        high    = float(df['High'].iloc[-1])
        low     = float(df['Low'].iloc[-1])
        open_   = float(df['Open'].iloc[-1])
        vol     = float(df['Volume'].iloc[-1])
        rsi_v   = float(rsi.iloc[-1])
        atr_v   = float(atr.iloc[-1])
        adx_v   = float(adx.iloc[-1])
        ema_f_v = float(ema_f.iloc[-1])
        ema_s_v = float(ema_s.iloc[-1])
        ema_t_v = float(ema_t.iloc[-1])
        vol_avg = float(df['Volume'].iloc[-21:-1].mean()) if len(df) > 20 else 0.0
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
        tp1_v = round(sell_lim - atr_v * params['atr_tp1_mult'], 1)
        tp2_v = round(sell_lim - atr_v * params['atr_tp2_mult'], 1)
        tp3_v = round(sell_lim - atr_v * params['atr_tp3_mult'], 1)
        tp1_c = round(buy_lim  + atr_v * params['atr_tp1_mult'], 1)
        tp2_c = round(buy_lim  + atr_v * params['atr_tp2_mult'], 1)
        tp3_c = round(buy_lim  + atr_v * params['atr_tp3_mult'], 1)

        def rr(limit, sl, tp):
            d = abs(sl - limit)
            return round(abs(tp - limit) / d, 1) if d > 0 else 0

        rr_sell_tp1 = rr(sell_lim, sl_venta,  tp1_v)
        rr_buy_tp1  = rr(buy_lim,  sl_compra, tp1_c)

        # ── SCORING ──
        score_sell = 0; score_buy = 0

        # Zonas (peso 2)
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
            if ema_f_prev >= ema_s_prev: score_sell += 1  # cruce reciente
        if ema_f_v > ema_s_v:
            score_buy += 2
            if ema_f_prev <= ema_s_prev: score_buy += 1

        # Tendencia EMA50
        if close < ema_t_v: score_sell += 1
        else:               score_buy  += 1

        # ADX (fuerza de tendencia)
        if adx_v > 25:
            if score_sell > score_buy: score_sell += 1
            else:                      score_buy  += 1

        # Volumen
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

        print(f"  📅 {fecha} | 💰 {round(close, 1)} | "
              f"SELL:{score_sell}/{max_score} BUY:{score_buy}/{max_score} | "
              f"RSI:{round(rsi_v, 1)} ADX:{round(adx_v, 1)} ATR:{round(atr_v, 1)}")

        clave_vela = f"{simbolo}_{fecha}"
        def ya_enviada(tipo):    return alertas_enviadas.get(f"{clave_vela}_{tipo}", False)
        def marcar_enviada(tipo): alertas_enviadas[f"{clave_vela}_{tipo}"] = True

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

        # ── PUBLICAR + FILTRO CONFLUENCIA MULTI-TF (SPX 15M) ──
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
                msg = (f"{nivel} — <b>SPX500 (15M) ⚡ SCALPING</b>\n"
                       f"━━━━━━━━━━━━━━━━━━━━\n"
                       f"💰 <b>Precio:</b>     {round(close, 1)}\n"
                       f"📌 <b>SELL LIMIT:</b> {round(sell_lim, 1)}\n"
                       f"🛑 <b>Stop Loss:</b>  {round(sl_venta, 1)}  (+{round(sl_venta - sell_lim, 1)})\n"
                       f"━━━━━━━━━━━━━━━━━━━━\n"
                       f"🎯 <b>TP1:</b> {tp1_v}  R:R {rr(sell_lim, sl_venta, tp1_v)}:1\n"
                       f"🎯 <b>TP2:</b> {tp2_v}  R:R {rr(sell_lim, sl_venta, tp2_v)}:1\n"
                       f"🎯 <b>TP3:</b> {tp3_v}  R:R {rr(sell_lim, sl_venta, tp3_v)}:1\n"
                       f"━━━━━━━━━━━━━━━━━━━━\n"
                       f"📊 <b>Score:</b> {score_sell}/{max_score}  📉 <b>RSI:</b> {round(rsi_v, 1)}\n"
                       f"📐 <b>ATR 15M:</b> {round(atr_v, 1)}\n"
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
                msg = (f"{nivel} — <b>SPX500 (15M) ⚡ SCALPING</b>\n"
                       f"━━━━━━━━━━━━━━━━━━━━\n"
                       f"💰 <b>Precio:</b>    {round(close, 1)}\n"
                       f"📌 <b>BUY LIMIT:</b> {round(buy_lim, 1)}\n"
                       f"🛑 <b>Stop Loss:</b> {round(sl_compra, 1)}  (-{round(buy_lim - sl_compra, 1)})\n"
                       f"━━━━━━━━━━━━━━━━━━━━\n"
                       f"🎯 <b>TP1:</b> {tp1_c}  R:R {rr(buy_lim, sl_compra, tp1_c)}:1\n"
                       f"🎯 <b>TP2:</b> {tp2_c}  R:R {rr(buy_lim, sl_compra, tp2_c)}:1\n"
                       f"🎯 <b>TP3:</b> {tp3_c}  R:R {rr(buy_lim, sl_compra, tp3_c)}:1\n"
                       f"━━━━━━━━━━━━━━━━━━━━\n"
                       f"📊 <b>Score:</b> {score_buy}/{max_score}  📉 <b>RSI:</b> {round(rsi_v, 1)}\n"
                       f"📐 <b>ATR 15M:</b> {round(atr_v, 1)}\n"
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
    enviar_telegram("🚀 <b>Detector SPX500 15M — SCALPING iniciado</b>\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    "📊 Monitorizando: ES=F (E-Mini S&P 500)\n"
                    "⏱️ Timeframe: 15M  |  Modo: ⚡ SCALPING\n"
                    "🔄 Revisión cada 2 minutos\n"
                    "📐 TPs dinámicos basados en ATR (×1.5 / ×2.5 / ×4.0)\n"
                    "🔒 Señales para abrir y cerrar en el mismo día\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    "🔴 Resistencia: 6,750 - 6,850\n"
                    "🟢 Soporte:     6,500 - 6,600")
    ciclo = 0
    while True:
        ciclo += 1
        print(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 🔄 CICLO #{ciclo} - SPX 15M SCALPING")
        for simbolo, params in SIMBOLOS.items():
            print(f"🔍 Analizando {simbolo} [SPX 15M]...")
            analizar(simbolo, params)
        print(f"⏳ Esperando 2 minutos...")
        time.sleep(CHECK_INTERVAL)

if __name__ == '__main__':
    main()
