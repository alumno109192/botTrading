"""
DETECTOR GOLD 15M - SCALPING
Análisis de XAUUSD en timeframe 15 minutos para operaciones de corto plazo
Optimizado para capturar movimientos rápidos con alta frecuencia
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
import tf_bias
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
        'zona_resist_high':   3350.0,       # Resistencia actual
        'zona_resist_low':    3320.0,       # Zona de resistencia
        'zona_soporte_high':  3250.0,       # Zona de soporte
        'zona_soporte_low':   3220.0,       # Soporte fuerte
        # TPs ajustados para SCALPING (movimientos pequeños)
        'tp1_venta':          3290.0,       # TP1 conservador (-$30)
        'tp2_venta':          3270.0,       # TP2 medio (-$50)
        'tp3_venta':          3240.0,       # TP3 agresivo (-$80)
        'tp1_compra':         3330.0,       # TP1 conservador (+$30)
        'tp2_compra':         3350.0,       # TP2 medio (+$50)
        'tp3_compra':         3380.0,       # TP3 agresivo (+$80)
        'tolerancia':         8.0,          # Tolerancia ajustada para 15m
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
        if TELEGRAM_THREAD_ID:
            payload["message_thread_id"] = TELEGRAM_THREAD_ID
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
    rsi   = 100 - (100 / (1 + rs))
    return rsi

def calcular_atr(df, length):
    h = df['High']
    l = df['Low']
    c = df['Close']
    tr1 = h - l
    tr2 = (h - c.shift()).abs()
    tr3 = (l - c.shift()).abs()
    tr  = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.rolling(length).mean()
    return atr

def calcular_adx(df, length=14):
    h = df['High']
    l = df['Low']
    c = df['Close']
    
    plus_dm  = h.diff()
    minus_dm = -l.diff()
    plus_dm[plus_dm < 0]   = 0
    minus_dm[minus_dm < 0] = 0
    
    tr = calcular_atr(df, 1) * length
    plus_di  = 100 * (plus_dm.ewm(alpha=1/length).mean() / tr)
    minus_di = 100 * (minus_dm.ewm(alpha=1/length).mean() / tr)
    
    dx  = 100 * ((plus_di - minus_di).abs() / (plus_di + minus_di))
    adx = dx.ewm(alpha=1/length).mean()
    return adx

def patron_envolvente_alcista(df):
    """Patrón envolvente alcista (bullish engulfing)"""
    c1_bear = df['Close'].iloc[-2] < df['Open'].iloc[-2]
    c2_bull = df['Close'].iloc[-1] > df['Open'].iloc[-1]
    c2_envuelve = (df['Close'].iloc[-1] > df['Open'].iloc[-2] and 
                   df['Open'].iloc[-1] < df['Close'].iloc[-2])
    return c1_bear and c2_bull and c2_envuelve

def patron_envolvente_bajista(df):
    """Patrón envolvente bajista (bearish engulfing)"""
    c1_bull = df['Close'].iloc[-2] > df['Open'].iloc[-2]
    c2_bear = df['Close'].iloc[-1] < df['Open'].iloc[-1]
    c2_envuelve = (df['Close'].iloc[-1] < df['Open'].iloc[-2] and 
                   df['Open'].iloc[-1] > df['Close'].iloc[-2])
    return c1_bull and c2_bear and c2_envuelve

def patron_doji(df):
    """Detectar vela Doji (indecisión)"""
    body = abs(df['Close'].iloc[-1] - df['Open'].iloc[-1])
    range_vela = df['High'].iloc[-1] - df['Low'].iloc[-1]
    return body < (range_vela * 0.1) if range_vela > 0 else False

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
def analizar_simbolo(simbolo, params):
    global perdidas_consecutivas, ultima_senal_timestamp
    
    try:
        # Descargar datos (15m requiere menos historial)
        df = yf.download(params['ticker_yf'], period='5d', interval='15m', progress=False)
        
        if df.empty or len(df) < 100:
            print(f"⚠️ Datos insuficientes para {simbolo}")
            return
        
        # Limpiar columnas
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.droplevel(1)
        
        # Renombrar columnas (GC=F puede no tener 'Adj Close')
        if len(df.columns) == 6:
            df.columns = ['Open', 'High', 'Low', 'Close', 'Adj Close', 'Volume']
        elif len(df.columns) == 5:
            df.columns = ['Open', 'High', 'Low', 'Close', 'Volume']
        else:
            print(f"⚠️ Columnas inesperadas ({len(df.columns)}): {df.columns.tolist()}")
            return
        
        # Calcular indicadores
        close = df['Close'].iloc[-1]
        vol   = df['Volume'].iloc[-6:].mean()
        
        rsi_len = params['rsi_length']
        rsi = calcular_rsi(df['Close'], rsi_len).iloc[-1]
        
        ema_fast = df['Close'].ewm(span=params['ema_fast_len']).mean()
        ema_slow = df['Close'].ewm(span=params['ema_slow_len']).mean()
        ema_trend = df['Close'].ewm(span=params['ema_trend_len']).mean()
        
        atr_len = params['atr_length']
        atr = calcular_atr(df, atr_len).iloc[-1]
        
        adx = calcular_adx(df).iloc[-1]
        
        # Parámetros de zonas
        zrh = params['zona_resist_high']
        zrl = params['zona_resist_low']
        zsh = params['zona_soporte_high']
        zsl = params['zona_soporte_low']
        tol = params['tolerancia']
        
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
        if rsi >= params['rsi_min_sell']:
            score_sell += 2
        elif rsi >= 60:
            score_sell += 1
        
        if rsi <= params['rsi_max_buy']:
            score_buy += 2
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
        
        # Score máximo: ~15 puntos
        max_score = 15
        
        # ══════════════════════════════════════
        # NIVELES DE SEÑAL SCALPING (más permisivos)
        # ══════════════════════════════════════
        senal_sell_fuerte = score_sell >= 8
        senal_sell_media  = score_sell >= 5
        senal_sell_scalp  = score_sell >= 3  # Señal de scalping
        
        senal_buy_fuerte  = score_buy >= 8
        senal_buy_media   = score_buy >= 5
        senal_buy_scalp   = score_buy >= 3
        
        # Cancelaciones (más estrictas en scalping)
        cancelar_sell = (close < zsh) or (rsi < 30)
        cancelar_buy  = (close > zrh) or (rsi > 70)
        
        # ── SL y TP para SCALPING ──
        asm = params['atr_sl_mult']
        sl_venta  = close + (atr * asm)
        sl_compra = close - (atr * asm)
        
        tp1_v = params['tp1_venta']
        tp2_v = params['tp2_venta']
        tp3_v = params['tp3_venta']
        tp1_c = params['tp1_compra']
        tp2_c = params['tp2_compra']
        tp3_c = params['tp3_compra']
        
        # Límites de entrada
        offset_pct = params['limit_offset_pct']
        sell_limit = close * (1 + offset_pct / 100)
        buy_limit  = close * (1 - offset_pct / 100)
        
        def rr(limit, sl, tp):
            return round(abs(tp - limit) / abs(sl - limit), 1) if abs(sl - limit) > 0 else 0
        
        # ── Log consola ──
        fecha = df.index[-1].strftime('%Y-%m-%d %H:%M')
        
        # ══════════════════════════════════════
        # VERIFICAR SI YA SE ANALIZÓ
        # ══════════════════════════════════════
        clave_simbolo = simbolo
        
        if clave_simbolo in ultimo_analisis:
            ultima_fecha = ultimo_analisis[clave_simbolo]['fecha']
            ultimo_score_sell = ultimo_analisis[clave_simbolo]['score_sell']
            ultimo_score_buy = ultimo_analisis[clave_simbolo]['score_buy']
            
            if (ultima_fecha == fecha and 
                abs(ultimo_score_sell - score_sell) <= 1 and 
                abs(ultimo_score_buy - score_buy) <= 1):
                print(f"  ℹ️  Vela {fecha} ya analizada - Sin cambios")
                return
        
        ultimo_analisis[clave_simbolo] = {
            'fecha': fecha,
            'score_sell': score_sell,
            'score_buy': score_buy
        }
        
        print(f"  📅 Vela:  {fecha}")
        print(f"  💰 Close: {round(close, 2)}")
        print(f"  📊 Score SELL: {score_sell}/{max_score} | Score BUY: {score_buy}/{max_score}")
        print(f"  🔴 SELL → Scalp:{senal_sell_scalp} Media:{senal_sell_media} Fuerte:{senal_sell_fuerte}")
        print(f"  🟢 BUY  → Scalp:{senal_buy_scalp}  Media:{senal_buy_media}  Fuerte:{senal_buy_fuerte}")
        print(f"  📉 RSI: {round(rsi, 1)} | ADX: {round(adx, 1)} | ATR: {round(atr, 2)}")
        
        # ══════════════════════════════════════
        # CONTROL DE PÉRDIDAS CONSECUTIVAS
        # ══════════════════════════════════════
        if perdidas_consecutivas >= params['max_perdidas_dia']:
            print(f"  ⛔ Trading pausado: {perdidas_consecutivas} pérdidas consecutivas")
            print(f"  ⏸️  Esperando señal clara para reanudar...")
            # Solo reanudar con señales FUERTES
            if not (senal_sell_fuerte or senal_buy_fuerte):
                return
            else:
                print(f"  ✅ Señal fuerte detectada - Reanudando trading")
                perdidas_consecutivas = 0
        
        # ══════════════════════════════════════
        # ANTI-SPAM
        # ══════════════════════════════════════
        clave_vela = f"{simbolo}_{fecha}"
        
        def ya_enviada(tipo):
            return alertas_enviadas.get(f"{clave_vela}_{tipo}", False)
        
        def marcar_enviada(tipo):
            alertas_enviadas[f"{clave_vela}_{tipo}"] = True
        
        # ══════════════════════════════════════        # EXCLUSIÓN MUTUA + SESGO MULTI-TF
        # ════════════════════════════════════
        _any_sell = senal_sell_scalp or senal_sell_media or senal_sell_fuerte
        _any_buy  = senal_buy_scalp  or senal_buy_media  or senal_buy_fuerte
        if _any_sell and _any_buy:
            if score_sell >= score_buy:
                senal_buy_scalp = senal_buy_media = senal_buy_fuerte = False
                print(f"  ⚖️ Exclusión mutua: BUY suprimida (SELL {score_sell} >= BUY {score_buy})")
            else:
                senal_sell_scalp = senal_sell_media = senal_sell_fuerte = False
                print(f"  ⚖️ Exclusión mutua: SELL suprimida (BUY {score_buy} > SELL {score_sell})")
            _any_sell = senal_sell_scalp or senal_sell_media or senal_sell_fuerte
            _any_buy  = senal_buy_scalp  or senal_buy_media  or senal_buy_fuerte

        _sesgo_dir = tf_bias.BIAS_BEARISH if score_sell > score_buy else tf_bias.BIAS_BULLISH if score_buy > score_sell else tf_bias.BIAS_NEUTRAL
        tf_bias.publicar_sesgo(simbolo, '15M', _sesgo_dir, max(score_sell, score_buy))
        _conf_sell = ""; _conf_buy = ""
        if _any_sell:
            _ok, _desc = tf_bias.verificar_confluencia(simbolo, '15M', tf_bias.BIAS_BEARISH)
            if not _ok:
                print(f"  🚫 SELL bloqueada por TF superior: {_desc[:80]}")
                senal_sell_scalp = senal_sell_media = senal_sell_fuerte = False
            else:
                _conf_sell = _desc
        if _any_buy:
            _ok, _desc = tf_bias.verificar_confluencia(simbolo, '15M', tf_bias.BIAS_BULLISH)
            if not _ok:
                print(f"  🚫 BUY bloqueada por TF superior: {_desc[:80]}")
                senal_buy_scalp = senal_buy_media = senal_buy_fuerte = False
            else:
                _conf_buy = _desc

        # ════════════════════════════════════        # ENVIAR SEÑALES SCALPING
        # ══════════════════════════════════════
        

        # ── FILTRO R:R MÍNIMO 1.2 ──
        rr_sell_tp1 = rr(sell_limit, sl_venta,  tp1_v)
        rr_buy_tp1  = rr(buy_limit,  sl_compra, tp1_c)
        if rr_sell_tp1 < 1.2:
            print(f'  ⛔ SELL bloqueada: R:R TP1={rr_sell_tp1} < 1.2')
            cancelar_sell = True
        if rr_buy_tp1 < 1.2:
            print(f'  ⛔ BUY bloqueada: R:R TP1={rr_buy_tp1} < 1.2')
            cancelar_buy = True

        # ── SEÑALES VENTA ──
        if (senal_sell_scalp or senal_sell_media or senal_sell_fuerte) and not cancelar_sell:
            nivel = ("🔥 SELL FUERTE" if senal_sell_fuerte else
                     "🔴 SELL MEDIA"  if senal_sell_media else
                     "⚡ SCALP SELL")
            tipo_clave = ("SELL_FUE" if senal_sell_fuerte else
                          "SELL_MED" if senal_sell_media else
                          "SCALP_SEL")
            
            if not ya_enviada(tipo_clave):
                # Verificar duplicados en BD
                if db and db.existe_senal_reciente(f"{simbolo}_15M", 'VENTA', horas=1):
                    print(f"  ℹ️  Señal VENTA duplicada - No se guarda")
                    return
                
                calidad = "🔥 ALTA" if senal_sell_fuerte else "⚠️ MEDIA" if senal_sell_media else "⚡ SCALP"
                
                msg = (f"{nivel} — <b>GOLD 15M SCALPING</b>\n"
                       f"━━━━━━━━━━━━━━━━━━━━\n"
                       f"💰 <b>Precio:</b>     ${round(close, 2)}\n"
                       f"📌 <b>SELL LIMIT:</b> ${round(sell_limit, 2)}\n"
                       f"🛑 <b>Stop Loss:</b>  ${round(sl_venta, 2)}\n"
                       f"🎯 <b>TP1:</b> ${tp1_v}  R:R {rr(sell_limit, sl_venta, tp1_v)}:1\n"
                       f"🎯 <b>TP2:</b> ${tp2_v}  R:R {rr(sell_limit, sl_venta, tp2_v)}:1\n"
                       f"🎯 <b>TP3:</b> ${tp3_v}  R:R {rr(sell_limit, sl_venta, tp3_v)}:1\n"
                       f"━━━━━━━━━━━━━━━━━━━━\n"
                       f"📊 <b>Score:</b> {score_sell}/{max_score} | <b>Calidad:</b> {calidad}\n"
                       f"📉 <b>RSI:</b> {round(rsi, 1)} | <b>ADX:</b> {round(adx, 1)}\n"
                       f"⏱️ <b>TF:</b> 15M  📅 {fecha}")
                
                if _conf_sell:
                    msg += f"\n━━━━━━━━━━━━━━━━━━━━\n{_conf_sell}"
                
                # Guardar en BD
                if db:
                    senal_data = {
                        'timestamp': datetime.now(timezone.utc),
                        'simbolo': f"{simbolo}_15M",
                        'direccion': 'VENTA',
                        'precio_entrada': sell_limit,
                        'tp1': tp1_v,
                        'tp2': tp2_v,
                        'tp3': tp3_v,
                        'sl': sl_venta,
                        'score': score_sell,
                        'timeframe': '15M',
                        'indicadores': json.dumps({
                            'rsi': round(rsi, 1),
                            'adx': round(adx, 1),
                            'atr': round(atr, 2),
                            'ema_fast': round(ema_fast.iloc[-1], 2),
                            'ema_slow': round(ema_slow.iloc[-1], 2)
                        }),
                        'patron_velas': f"Envolvente:{patron_envolvente_bajista(df)}, Doji:{patron_doji(df)}",
                        'version_detector': '15M-SCALP-v1.0'
                    }
                    
                    try:
                        senal_id = db.guardar_senal(senal_data)
                        print(f"  💾 Señal VENTA SCALPING guardada con ID: {senal_id}")
                    except Exception as e:
                        print(f"  ⚠️ Error guardando señal: {e}")
                
                enviar_telegram(msg)
                marcar_enviada(tipo_clave)
        
        # ── SEÑALES COMPRA ──
        if (senal_buy_scalp or senal_buy_media or senal_buy_fuerte) and not cancelar_buy:
            nivel = ("🔥 BUY FUERTE" if senal_buy_fuerte else
                     "🟢 BUY MEDIA"  if senal_buy_media else
                     "⚡ SCALP BUY")
            tipo_clave = ("BUY_FUE" if senal_buy_fuerte else
                          "BUY_MED" if senal_buy_media else
                          "SCALP_BUY")
            
            if not ya_enviada(tipo_clave):
                if db and db.existe_senal_reciente(f"{simbolo}_15M", 'COMPRA', horas=1):
                    print(f"  ℹ️  Señal COMPRA duplicada - No se guarda")
                    return
                
                calidad = "🔥 ALTA" if senal_buy_fuerte else "⚠️ MEDIA" if senal_buy_media else "⚡ SCALP"
                
                msg = (f"{nivel} — <b>GOLD 15M SCALPING</b>\n"
                       f"━━━━━━━━━━━━━━━━━━━━\n"
                       f"💰 <b>Precio:</b>    ${round(close, 2)}\n"
                       f"📌 <b>BUY LIMIT:</b> ${round(buy_limit, 2)}\n"
                       f"🛑 <b>Stop Loss:</b> ${round(sl_compra, 2)}\n"
                       f"🎯 <b>TP1:</b> ${tp1_c}  R:R {rr(buy_limit, sl_compra, tp1_c)}:1\n"
                       f"🎯 <b>TP2:</b> ${tp2_c}  R:R {rr(buy_limit, sl_compra, tp2_c)}:1\n"
                       f"🎯 <b>TP3:</b> ${tp3_c}  R:R {rr(buy_limit, sl_compra, tp3_c)}:1\n"
                       f"━━━━━━━━━━━━━━━━━━━━\n"
                       f"📊 <b>Score:</b> {score_buy}/{max_score} | <b>Calidad:</b> {calidad}\n"
                       f"📉 <b>RSI:</b> {round(rsi, 1)} | <b>ADX:</b> {round(adx, 1)}\n"
                       f"⏱️ <b>TF:</b> 15M  📅 {fecha}")
                
                if _conf_buy:
                    msg += f"\n━━━━━━━━━━━━━━━━━━━━\n{_conf_buy}"
                
                if db:
                    senal_data = {
                        'timestamp': datetime.now(timezone.utc),
                        'simbolo': f"{simbolo}_15M",
                        'direccion': 'COMPRA',
                        'precio_entrada': buy_limit,
                        'tp1': tp1_c,
                        'tp2': tp2_c,
                        'tp3': tp3_c,
                        'sl': sl_compra,
                        'score': score_buy,
                        'timeframe': '15M',
                        'indicadores': json.dumps({
                            'rsi': round(rsi, 1),
                            'adx': round(adx, 1),
                            'atr': round(atr, 2),
                            'ema_fast': round(ema_fast.iloc[-1], 2),
                            'ema_slow': round(ema_slow.iloc[-1], 2)
                        }),
                        'patron_velas': f"Envolvente:{patron_envolvente_alcista(df)}, Doji:{patron_doji(df)}",
                        'version_detector': '15M-SCALP-v1.0'
                    }
                    
                    try:
                        senal_id = db.guardar_senal(senal_data)
                        print(f"  💾 Señal COMPRA SCALPING guardada con ID: {senal_id}")
                    except Exception as e:
                        print(f"  ⚠️ Error guardando señal: {e}")
                
                enviar_telegram(msg)
                marcar_enviada(tipo_clave)
    
    except Exception as e:
        print(f"❌ Error analizando {simbolo}: {e}")

# ══════════════════════════════════════
# FUNCIÓN MAIN (para importación desde run_detectors)
# ══════════════════════════════════════
def main():
    """Función principal para ejecutar el detector"""
    enviar_telegram("🚀 <b>Detector GOLD 15M SCALPING iniciado</b>\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    "⏱️  Análisis cada 2 minutos\n"
                    "⚡ Optimizado para operaciones rápidas\n"
                    "🎯 TPs: $30 / $50 / $80\n"
                    "📊 Score mínimo: 3/15")
    
    ciclo = 0
    while True:
        ciclo += 1
        print("\n" + "="*60)
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 🔄 CICLO #{ciclo} - GOLD 15M SCALPING")
        print("="*60 + "\n")
        
        for simbolo, params in SIMBOLOS.items():
            print(f"📊 Analizando {simbolo} [15M SCALPING]...\n")
            print(f"🔍 Analizando {simbolo}...")
            analizar_simbolo(simbolo, params)
        
        print("\n" + "="*60)
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] ✅ Ciclo #{ciclo} completado")
        print(f"⏳ Esperando {CHECK_INTERVAL//60} minutos...")
        print("="*60 + "\n")
        
        time.sleep(CHECK_INTERVAL)

# ══════════════════════════════════════
# BUCLE PRINCIPAL
# ══════════════════════════════════════
if __name__ == "__main__":
    main()
