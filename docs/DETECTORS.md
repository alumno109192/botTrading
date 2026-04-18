# 🔧 Creación y Modificación de Detectores

Guía paso a paso para crear un nuevo detector de señales o modificar los existentes.

---

## 📋 Contenido

1. [Estructura de un Detector](#estructura-de-un-detector)
2. [Template Base](#template-base)
3. [Pasos para Crear Detector](#pasos-para-crear-detector)
4. [Parámetros por Timeframe](#parámetros-por-timeframe)
5. [Debugging](#debugging)

---

## Estructura de un Detector

Ubicación de archivos:

```
detectors/
├── gold/
│   ├── __init__.py
│   ├── detector_gold_1d.py
│   ├── detector_gold_4h.py
│   ├── detector_gold_1h.py
│   ├── detector_gold_15m.py
│   └── detector_gold_5m.py
├── bitcoin/
│   ├── detector_bitcoin_1d.py
│   └── detector_bitcoin_4h.py
├── spx/
│   ├── detector_spx_1d.py
│   ├── detector_spx_4h.py
│   └── detector_spx_15m.py
└── ... [más activos]
```

---

## Template Base

Estructura mínima de un detector:

```python
import time
import os
from datetime import datetime
import pandas as pd
import yfinance as yf
from dotenv import load_dotenv
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from telegram_utils import enviar_telegram
from db_manager import DatabaseManager
from shared_indicators import (
    calcular_rsi, calcular_ema, calcular_atr,
    calcular_bollinger_bands, calcular_macd, calcular_adx,
    calcular_obv, patron_evening_star, patron_morning_star
)
from tf_bias import verificar_confluencia, publicar_sesgo
from yf_lock import _yf_lock

load_dotenv()

# ═════════════════════════════════════════════════════════
# CONFIGURACIÓN
# ═════════════════════════════════════════════════════════

SYMBOL = "GC=F"                    # Ticker de Yahoo Finance
SYMBOL_DISPLAY = "GOLD (1D)"       # Nombre para mostrar
TIMEFRAME = "1d"                   # 1d, 4h, 1h, 15m, 5m
CHECK_INTERVAL = 600               # Segundos entre análisis
THREAD_ID = 304                    # Telegram thread ID

# Parámetros técnicos (escalados por TF)
PERIODO_DATOS = "2y"
EMA_FAST = 9
EMA_SLOW = 21
EMA_TREND = 200
PERIODO_RSI = 14
PERIODO_ATR = 14

# Thresholds
UMBRAL_RSI_VENTA = 70
UMBRAL_RSI_COMPRA = 30
UMBRAL_SCORE_MINIMO = 6
MULTIPLICADOR_SL = 1.5

# Anti-spam
ZONAS_SOPORTE_RESISTENCIA = {
    'resistencia': [3340, 3360],
    'soporte': [3280, 3300],
}

# ═════════════════════════════════════════════════════════
# FUNCIONES DE ANÁLISIS
# ═════════════════════════════════════════════════════════

def descargar_datos():
    """Descarga datos de yfinance de forma thread-safe."""
    with _yf_lock:
        try:
            df = yf.download(
                SYMBOL,
                period=PERIODO_DATOS,
                interval=TIMEFRAME,
                progress=False
            )
            return df
        except Exception as e:
            print(f"❌ Error descargando datos: {e}")
            return None

def calcular_indicadores(df):
    """Calcula todos los indicadores técnicos."""
    if df is None or len(df) < 50:
        return None
    
    # Indicadores
    rsi = calcular_rsi(df, PERIODO_RSI)
    ema_fast = calcular_ema(df, EMA_FAST)
    ema_slow = calcular_ema(df, EMA_SLOW)
    ema_trend = calcular_ema(df, EMA_TREND)
    atr = calcular_atr(df, PERIODO_ATR)
    bb_upper, bb_mid, bb_lower, bb_width = calcular_bollinger_bands(df, 20, 2)
    macd, macd_signal, macd_hist = calcular_macd(df, 12, 26, 9)
    adx, di_plus, di_minus = calcular_adx(df, 14)
    obv = calcular_obv(df)
    
    return {
        'rsi': rsi,
        'ema_fast': ema_fast,
        'ema_slow': ema_slow,
        'ema_trend': ema_trend,
        'atr': atr,
        'bb_upper': bb_upper,
        'bb_mid': bb_mid,
        'bb_lower': bb_lower,
        'bb_width': bb_width,
        'macd': macd,
        'macd_signal': macd_signal,
        'macd_hist': macd_hist,
        'adx': adx,
        'di_plus': di_plus,
        'di_minus': di_minus,
        'obv': obv,
    }

def analizar_senales(df, indicadores):
    """Analiza la última vela y calcula score."""
    if df is None or indicadores is None:
        return None, None
    
    idx = -2  # Última vela CERRADA
    
    # Extrae últimos valores
    rsi_valor = indicadores['rsi'].iloc[idx]
    ema_f = indicadores['ema_fast'].iloc[idx]
    ema_s = indicadores['ema_slow'].iloc[idx]
    ema_t = indicadores['ema_trend'].iloc[idx]
    atr_valor = indicadores['atr'].iloc[idx]
    bb_upper = indicadores['bb_upper'].iloc[idx]
    bb_lower = indicadores['bb_lower'].iloc[idx]
    macd_val = indicadores['macd'].iloc[idx]
    adx_val = indicadores['adx'].iloc[idx]
    precio = df['Close'].iloc[idx]
    
    score_sell = 0
    score_buy = 0
    
    # SCORING LÓGICA
    
    # RSI
    if rsi_valor >= UMBRAL_RSI_VENTA:
        score_sell += 2
    if rsi_valor <= UMBRAL_RSI_COMPRA:
        score_buy += 2
    
    # EMA
    if ema_f > ema_s:
        score_buy += 1
    else:
        score_sell += 1
    
    # Bollinger
    if precio >= bb_upper:
        score_sell += 2
    if precio <= bb_lower:
        score_buy += 2
    
    # MACD
    if macd_val > 0:
        score_buy += 1
    else:
        score_sell += 1
    
    # ADX (penalización si es bajo)
    if adx_val < 20:
        score_sell -= 3
        score_buy -= 3
    
    return max(0, score_buy), max(0, score_sell)

def generar_senales(df, score_buy, score_compra, indicadores):
    """Genera señales si los scores cumplen umbrales."""
    if df is None:
        return []
    
    senales = []
    precio = df['Close'].iloc[-2]
    atr_valor = indicadores['atr'].iloc[-2]
    
    # Señal COMPRA
    if score_buy >= UMBRAL_SCORE_MINIMO:
        senales.append({
            'tipo': 'BUY',
            'precio_entrada': precio,
            'sl': precio - (atr_valor * MULTIPLICADOR_SL),
            'tp1': precio + (atr_valor * 1.2),
            'tp2': precio + (atr_valor * 2.0),
            'tp3': precio + (atr_valor * 3.0),
            'score': score_buy,
        })
    
    # Señal VENTA
    if score_sell >= UMBRAL_SCORE_MINIMO:
        senales.append({
            'tipo': 'SELL',
            'precio_entrada': precio,
            'sl': precio + (atr_valor * MULTIPLICADOR_SL),
            'tp1': precio - (atr_valor * 1.2),
            'tp2': precio - (atr_valor * 2.0),
            'tp3': precio - (atr_valor * 3.0),
            'score': score_sell,
        })
    
    return senales

def enviar_senales(senales):
    """Envía señales a Telegram y BD."""
    if not senales:
        return
    
    db = DatabaseManager()
    
    for senal in senales:
        # Verificar confluencia multi-TF
        if not verificar_confluencia(SYMBOL_DISPLAY, TIMEFRAME, senal['tipo']):
            print(f"⚠️  {SYMBOL_DISPLAY} {senal['tipo']} bloqueada por confluencia")
            continue
        
        # Calcular R:R
        if senal['tipo'] == 'BUY':
            rr = (senal['precio_entrada'] - senal['sl']) / (senal['tp1'] - senal['precio_entrada'])
        else:
            rr = (senal['sl'] - senal['precio_entrada']) / (senal['precio_entrada'] - senal['tp1'])
        
        # Formatear mensaje
        titulo = f"{'🟢' if senal['tipo'] == 'BUY' else '🔴'} {senal['tipo']} {SYMBOL_DISPLAY}"
        mensaje = f"""
{titulo}
━━━━━━━━━━━━━━━━━━━━
💰 Precio: ${senal['precio_entrada']:.2f}
🛑 SL: ${senal['sl']:.2f}
🎯 TP1: ${senal['tp1']:.2f} (R:R {rr:.1f}:1)
🎯 TP2: ${senal['tp2']:.2f}
🎯 TP3: ${senal['tp3']:.2f}
━━━━━━━━━━━━━━━━━━━━
📊 Score: {senal['score']}/15
⏱️ {TIMEFRAME.upper()}  📅 {datetime.now().strftime('%Y-%m-%d %H:%M')}
"""
        
        # Enviar Telegram
        enviar_telegram(mensaje, thread_id=THREAD_ID)
        
        # Guardar en BD
        db.guardar_senal({
            'symbol': SYMBOL_DISPLAY,
            'timeframe': TIMEFRAME,
            'direction': senal['tipo'],
            'entry_price': senal['precio_entrada'],
            'stop_loss': senal['sl'],
            'tp1': senal['tp1'],
            'tp2': senal['tp2'],
            'tp3': senal['tp3'],
            'score': senal['score'],
        })
        
        # Publicar sesgo para cascada multi-TF
        publicar_sesgo(SYMBOL_DISPLAY, TIMEFRAME, senal['tipo'], senal['score'])

def main():
    """Loop principal del detector."""
    print(f"🔵 Iniciando DETECTOR {SYMBOL_DISPLAY}...")
    
    # Diccionario para evitar spam
    ultimas_senales = {}
    
    while True:
        try:
            # Descargar y analizar
            df = descargar_datos()
            if df is None:
                time.sleep(CHECK_INTERVAL)
                continue
            
            indicadores = calcular_indicadores(df)
            score_buy, score_sell = analizar_senales(df, indicadores)
            
            # Generar señales
            senales = generar_senales(df, score_buy, score_sell, indicadores)
            
            # Evitar spam (no re-enviar misma vela)
            vela_id = f"{SYMBOL_DISPLAY}_{df.index[-2]}"
            if vela_id not in ultimas_senales:
                enviar_senales(senales)
                if senales:
                    ultimas_senales[vela_id] = True
            
            # Log
            if senales:
                print(f"✅ {SYMBOL_DISPLAY}: {len(senales)} señal(es) generada(s)")
            
            time.sleep(CHECK_INTERVAL)
            
        except Exception as e:
            print(f"❌ Error en {SYMBOL_DISPLAY}: {e}")
            time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()
```

---

## Pasos para Crear Detector

### 1. Crear Estructura de Directorio

```bash
mkdir -p detectors/mi_activo
touch detectors/mi_activo/__init__.py
touch detectors/mi_activo/detector_mi_activo_1d.py
```

### 2. Personalizar el Template

Reemplaza en el template:
- `SYMBOL` → Ticker Yahoo Finance de tu activo
- `SYMBOL_DISPLAY` → Nombre para mostrar
- `TIMEFRAME` → Período (1d, 4h, 1h, 15m, 5m)
- `CHECK_INTERVAL` → Segundos entre análisis
- `THREAD_ID` → ID del canal Telegram
- Parámetros técnicos (EMA, RSI, ATR, etc.)

### 3. Registrar el Detector en app.py

En `app.py`, añade el thread:

```python
from detectors.mi_activo import detector_mi_activo_1d

# En la función init_detectors():
threading.Thread(target=detector_mi_activo_1d.main, daemon=True).start()
```

### 4. Probar Detector Individual

```bash
python detectors/mi_activo/detector_mi_activo_1d.py
```

Debería mostrar:
```
🔵 Iniciando DETECTOR MI_ACTIVO (1D)...
```

---

## Parámetros por Timeframe

### Gold (XAUUSD)

| Parámetro | 1D | 4H | 1H | 15M | 5M |
|-----------|-----|-----|-----|-----|-----|
| EMA Fast | 9 | 18 | 36 | 5 | 5 |
| EMA Slow | 21 | 42 | 84 | 13 | 13 |
| EMA Trend | 200 | 400 | — | 50 | 50 |
| RSI | 14 | 28 | 56 | 9 | 9 |
| ATR | 14 | 28 | 28 | 10 | 10 |
| SL Mult | 1.5× | 1.2× | 1.2× | 1.5× | 1.5× |
| Período datos | 2y | 60d | 30d | 5d | 2d |
| Check Int | 10m | 4m | 4m | 2m | 2m |

### EURUSD

| Parámetro | 1D | 4H | 15M |
|-----------|-----|-----|-----|
| EMA Fast | 9 | 18 | 5 |
| EMA Slow | 21 | 42 | 13 |
| RSI | 14 | 28 | 9 |
| ATR | 14 | 28 | 10 |
| SL Mult | 1.5× | 1.2× | 1.5× |
| Check Int | 10m | 4m | 2m |

---

## Debugging

### ❌ "ModuleNotFoundError: No module named 'shared_indicators'"

**Solución:** Asegúrate que el `sys.path.append()` apunta al directorio raíz:

```python
import sys, os
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
```

### ❌ "yfinance: Invalid ticker"

**Solución:** Verifica el ticker en Yahoo Finance:
- XAUUSD → `GC=F`
- EURUSD → `EURUSD=X`
- BTCUSD → `BTC-USD`
- SPX500 → `^GSPC`

### ❌ "Telegram: Unauthorized (401)"

**Solución:** Verifica `.env`:

```bash
python -c "import os; from dotenv import load_dotenv; load_dotenv(); print(os.getenv('TELEGRAM_TOKEN'))"
```

Debe mostrar el token completo. Si no, actualiza `.env`.

### ✅ Test de Detector Local

```python
# Ejecuta en Python REPL
from detectors.gold.detector_gold_1d import descargar_datos, calcular_indicadores, analizar_senales

df = descargar_datos()
print(f"Datos: {len(df)} velas")
print(f"Precio actual: ${df['Close'].iloc[-1]:.2f}")

indicadores = calcular_indicadores(df)
score_buy, score_sell = analizar_senales(df, indicadores)
print(f"Buy Score: {score_buy}, Sell Score: {score_sell}")
```

---

*Última actualización: 2026-04-18*
