# 📘 Guía Completa: Crear un Detector de Señales Técnicas para XAUUSD

Esta guía detalla paso a paso cómo crear un detector automatizado de señales de trading para XAUUSD (Oro) con análisis técnico completo y alertas a Telegram.

---

## 📋 Tabla de Contenidos

1. [Prerequisitos](#prerequisitos)
2. [Configuración del Entorno](#configuración-del-entorno)
3. [Obtención de Credenciales](#obtención-de-credenciales)
4. [Estructura del Proyecto](#estructura-del-proyecto)
5. [Código del Detector](#código-del-detector)
6. [Parámetros de Configuración](#parámetros-de-configuración)
7. [Indicadores Técnicos](#indicadores-técnicos)
8. [Detección del Sentimiento del Mercado](#detección-del-sentimiento-del-mercado)
9. [Sistema de Scoring](#sistema-de-scoring)
10. [Sistema Anti-Spam](#sistema-anti-spam)
11. [Ejecución y Pruebas](#ejecución-y-pruebas)
12. [Despliegue](#despliegue)

---

## 1. Prerequisitos

### Software necesario:
- **Python 3.8+** instalado
- **Git** para control de versiones
- **Cuenta de Telegram**
- **Editor de código** (VS Code recomendado)

### Conocimientos básicos:
- Python básico
- Conceptos de análisis técnico (RSI, EMA, ATR)
- Uso de terminal/línea de comandos

---

## 2. Configuración del Entorno

### Paso 2.1: Crear directorio del proyecto

```bash
mkdir BotTrading
cd BotTrading
```

### Paso 2.2: Inicializar Git

```bash
git init
```

### Paso 2.3: Crear entorno virtual

```bash
python -m venv venv
```

### Paso 2.4: Activar entorno virtual

**Windows:**
```bash
.\venv\Scripts\Activate.ps1
```

**Linux/Mac:**
```bash
source venv/bin/activate
```

### Paso 2.5: Crear archivo `requirements.txt`

```txt
yfinance>=0.2.0
pandas>=2.0.0
numpy>=1.24.0
requests==2.31.0
python-dotenv==1.0.1
```

### Paso 2.6: Instalar dependencias

```bash
pip install -r requirements.txt
```

---

## 3. Obtención de Credenciales

### 3.1. Token de Telegram (TELEGRAM_TOKEN)

1. Abre Telegram y busca **@BotFather**
2. Envía el comando: `/start`
3. Crea un nuevo bot: `/newbot`
4. Asigna un nombre: `Alertas Trading`
5. Asigna un username: `alertas_trading_bot`
6. **Copia el token** que te proporciona (formato: `1234567890:ABCdefGHIjklMNOpqrsTUVwxyz`)

### 3.2. Chat ID de Telegram (TELEGRAM_CHAT_ID)

**Opción A - Chat Personal:**
1. Busca en Telegram: **@userinfobot**
2. Inicia conversación
3. Te dará tu **chat_id** (número positivo, ej: `123456789`)

**Opción B - Grupo:**
1. Crea un grupo en Telegram
2. Añade tu bot al grupo
3. Envía un mensaje en el grupo
4. Ve a: `https://api.telegram.org/bot<TU_TOKEN>/getUpdates`
5. Busca `"chat":{"id":-123456789}` (número negativo para grupos)

### 3.3. Crear archivo `.env`

Crea un archivo llamado `.env` en la raíz del proyecto:

```env
TELEGRAM_TOKEN=1234567890:ABCdefGHIjklMNOpqrsTUVwxyz
TELEGRAM_CHAT_ID=123456789
```

### 3.4. Crear `.env.example` (para GitHub)

```env
TELEGRAM_TOKEN=tu_token_del_bot_aqui
TELEGRAM_CHAT_ID=tu_chat_id_aqui
```

### 3.5. Crear `.gitignore`

```gitignore
# Python
__pycache__/
*.py[cod]
*.so
.Python
venv/
env/

# Environment variables
.env

# IDE
.vscode/
.idea/

# OS
.DS_Store
```

---

## 4. Estructura del Proyecto

```
BotTrading/
├── detector_gold.py      # Detector principal de XAUUSD
├── detector_spx.py       # Detector de SPX500 (opcional)
├── run_detectors.py      # Ejecutor multi-detector
├── requirements.txt      # Dependencias
├── .env                  # Credenciales (NO subir a Git)
├── .env.example          # Plantilla de credenciales
├── .gitignore            # Archivos a ignorar
├── README.md             # Documentación principal
├── EJECUTAR.md           # Instrucciones de ejecución
└── CREAR_DETECTOR.md     # Esta guía
```

---

## 5. Código del Detector

### 5.1. Estructura básica de `detector_gold.py`

```python
import yfinance as yf
import pandas as pd
import numpy as np
import requests
import os
import time
from datetime import datetime
from dotenv import load_dotenv

# Cargar variables de entorno
load_dotenv()

# ══════════════════════════════════════
# CONFIGURACIÓN
# ══════════════════════════════════════
TELEGRAM_TOKEN   = os.environ.get('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')
CHECK_INTERVAL   = 14 * 60  # 14 minutos en segundos
```

### 5.2. Función para enviar mensajes a Telegram

```python
def enviar_telegram(mensaje):
    """Envía un mensaje HTML a Telegram"""
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": mensaje,
            "parse_mode": "HTML"
        }
        r = requests.post(url, json=payload, timeout=10)
        print(f"✅ Telegram enviado → {r.status_code}")
    except Exception as e:
        print(f"❌ Error Telegram: {e}")
```

---

## 6. Parámetros de Configuración

### 6.1. Definir parámetros del instrumento

```python
SIMBOLOS = {
    'XAUUSD': {
        # Ticker de Yahoo Finance
        'ticker_yf': 'GC=F',
        
        # Zonas de resistencia y soporte
        'zona_resist_high':   4900.0,
        'zona_resist_low':    4750.0,
        'zona_soporte_high':  4400.0,
        'zona_soporte_low':   4200.0,
        
        # Take Profits
        'tp1_venta':   4627.0,
        'tp2_venta':   4374.0,
        'tp3_venta':   4099.0,
        'tp1_compra':  4900.0,
        'tp2_compra':  5100.0,
        'tp3_compra':  5300.0,
        
        # Parámetros de análisis
        'tolerancia':        30.0,    # Tolerancia para zonas
        'limit_offset_pct':  0.3,     # % offset para límites
        'anticipar_velas':   3,       # Velas de anticipación
        'cancelar_dist':     1.0,     # % para cancelar
        
        # Indicadores técnicos
        'rsi_length':      14,
        'rsi_min_sell':    55.0,
        'rsi_max_buy':     45.0,
        'ema_fast_len':    9,
        'ema_slow_len':    21,
        'ema_trend_len':   200,
        'atr_length':      14,
        'atr_sl_mult':     1.5,
        'vol_mult':        1.2,
    }
}
```

### 6.2. Explicación de parámetros clave

| Parámetro | Descripción |
|-----------|-------------|
| `ticker_yf` | Código del instrumento en Yahoo Finance |
| `zona_resist_high/low` | Nivel superior e inferior de resistencia |
| `zona_soporte_high/low` | Nivel superior e inferior de soporte |
| `tolerancia` | Rango de tolerancia para entrar en zona |
| `rsi_min_sell` | RSI mínimo para considerar venta |
| `rsi_max_buy` | RSI máximo para considerar compra |
| `atr_sl_mult` | Multiplicador de ATR para Stop Loss |

---

## 7. Indicadores Técnicos

### 7.1. RSI (Relative Strength Index)

```python
def calcular_rsi(series, length):
    """Calcula el RSI de una serie de precios"""
    delta = series.diff()
    gain  = delta.clip(lower=0)
    loss  = -delta.clip(upper=0)
    avg_g = gain.ewm(com=length - 1, min_periods=length).mean()
    avg_l = loss.ewm(com=length - 1, min_periods=length).mean()
    rs    = avg_g / avg_l
    return 100 - (100 / (1 + rs))
```

### 7.2. EMA (Exponential Moving Average)

```python
def calcular_ema(series, length):
    """Calcula la media móvil exponencial"""
    return series.ewm(span=length, adjust=False).mean()
```

### 7.3. ATR (Average True Range)

```python
def calcular_atr(df, length):
    """Calcula el Average True Range"""
    high = df['High']
    low = df['Low']
    close_prev = df['Close'].shift(1)
    
    tr = pd.concat([
        high - low,
        (high - close_prev).abs(),
        (low - close_prev).abs()
    ], axis=1).max(axis=1)
    
    return tr.ewm(com=length - 1, min_periods=length).mean()
```

---

## 8. Detección del Sentimiento del Mercado

El sentimiento del mercado es crucial para determinar la fuerza y dirección de las señales. El detector analiza múltiples factores para determinar si el mercado está en un contexto **alcista**, **bajista** o **neutral**.

### 8.1. Componentes del Sentimiento del Mercado

#### A) Estructura de Precios

La estructura del mercado revela la tendencia subyacente analizando la formación de máximos y mínimos.

```python
# Análisis de estructura bajista
max_decreciente = (high < prev['High']) and (prev['High'] < p2['High'])
min_decreciente = (low < prev['Low']) and (prev['Low'] < p2['Low'])
estructura_bajista = max_decreciente or min_decreciente

# Análisis de estructura alcista
max_creciente = (high > prev['High']) and (prev['High'] > p2['High'])
min_creciente = (low > prev['Low']) and (prev['Low'] > p2['Low'])
estructura_alcista = max_creciente or min_creciente
```

**Interpretación:**
- 🔴 **Estructura Bajista**: Máximos y/o mínimos decrecientes → Presión vendedora
- 🟢 **Estructura Alcista**: Máximos y/o mínimos crecientes → Presión compradora
- ⚪ **Estructura Neutral**: Sin patrón claro → Consolidación

#### B) Posición Relativa a EMAs

Las medias móviles exponenciales ayudan a identificar la tendencia general del mercado.

```python
# Clasificar EMAs
df['ema_fast'] = calcular_ema(df['Close'], 9)
df['ema_slow'] = calcular_ema(df['Close'], 21)
df['ema_trend'] = calcular_ema(df['Close'], 200)

# Determinar tendencia por EMAs
emas_bajistas = ema_fast < ema_slow  # EMA 9 < EMA 21
emas_alcistas = ema_fast > ema_slow  # EMA 9 > EMA 21

# Posición respecto a EMA 200 (tendencia de largo plazo)
sobre_ema200 = close > ema_trend  # Tendencia alcista general
bajo_ema200 = close < ema_trend   # Tendencia bajista general
```

**Interpretación:**
- 🟢 **Sentimiento Alcista**: Precio > EMA200 y EMA9 > EMA21
- 🔴 **Sentimiento Bajista**: Precio < EMA200 y EMA9 < EMA21
- ⚪ **Sentimiento Mixto**: Señales contradictorias

#### C) Divergencias RSI

Las divergencias revelan debilidad en la tendencia actual y posibles reversiones.

```python
# Divergencia Bajista (precio sube, RSI no confirma)
lookback = 5
price_new_high = high > df['High'].iloc[-lookback-2:-2].max()
rsi_lower_high = rsi < df['rsi'].iloc[-lookback-2:-2].max()
divergencia_bajista = price_new_high and rsi_lower_high and rsi > 50

# Divergencia Alcista (precio baja, RSI no confirma)
price_new_low = low < df['Low'].iloc[-lookback-2:-2].min()
rsi_higher_low = rsi > df['rsi'].iloc[-lookback-2:-2].min()
divergencia_alcista = price_new_low and rsi_higher_low and rsi < 50
```

**Interpretación:**
- ⚠️ **Divergencia Bajista**: El precio hace nuevos máximos pero el RSI no los confirma → Debilidad alcista, posible reversión bajista
- ⚠️ **Divergencia Alcista**: El precio hace nuevos mínimos pero el RSI no los confirma → Debilidad bajista, posible reversión alcista

#### D) Análisis de Volumen

El volumen confirma la validez de los movimientos de precio.

```python
# Calcular volumen promedio
df['vol_avg'] = df['Volume'].rolling(20).mean()

# Detectar volumen alto en rechazo/rebote
vol_alto_rechazo = vol > vol_avg * vol_mult  # Rechazo con volumen alto
vol_alto_rebote = vol > vol_avg * vol_mult   # Rebote con volumen alto

# Detectar volumen decreciente (señal de debilidad)
vol_decreciente = (vol < prev['Volume']) and (prev['Volume'] < p2['Volume'])
```

**Interpretación:**
- ✅ **Volumen Alto + Movimiento**: Confirma la fuerza del movimiento
- ❌ **Volumen Bajo + Movimiento**: Movimiento débil, posible reversión
- 📉 **Volumen Decreciente**: Pérdida de momentum

#### E) Zonas de Soporte y Resistencia

La reacción del precio en zonas clave revela el sentimiento institucional.

```python
# Definir zonas
zona_resist_high = 4900.0
zona_resist_low = 4750.0
zona_soporte_high = 4400.0
zona_soporte_low = 4200.0
tolerancia = 30.0

# Detectar si el precio está en zona
en_zona_resist = (high >= zona_resist_low - tolerancia) and \
                 (high <= zona_resist_high + tolerancia)

en_zona_soporte = (low >= zona_soporte_low - tolerancia) and \
                  (low <= zona_soporte_high + tolerancia)

# Detectar intentos de rotura fallidos (revela sentimiento)
intento_rotura_fallido = (high >= zona_resist_low) and (close < zona_resist_low)
intento_caida_fallido = (low <= zona_soporte_high) and (close > zona_soporte_high)
```

**Interpretación:**
- 🔴 **Rechazo en Resistencia**: Fuerte sentimiento bajista, vendedores dominan
- 🟢 **Rebote en Soporte**: Fuerte sentimiento alcista, compradores dominan
- ⚡ **Rotura de Zona**: Cambio de sentimiento del mercado

### 8.2. Confluencia de Señales (Sentimiento Combinado)

El sentimiento real se determina cuando **múltiples factores coinciden**:

```python
def detectar_sentimiento_bajista(df, row, prev, p2):
    """
    Detecta sentimiento bajista del mercado
    Returns: (sentimiento_score, factores_detectados)
    """
    factores = []
    score = 0
    
    # 1. Estructura bajista
    if max_decreciente or min_decreciente:
        factores.append("Estructura bajista")
        score += 2
    
    # 2. EMAs bajistas + precio bajo EMA200
    if emas_bajistas:
        factores.append("EMAs bajistas")
        score += 1
    if bajo_ema200:
        factores.append("Bajo EMA200")
        score += 1
    
    # 3. Divergencia bajista
    if divergencia_bajista:
        factores.append("Divergencia bajista")
        score += 2
    
    # 4. RSI alto girando
    if rsi_alto_girando or rsi_sobrecompra:
        factores.append("RSI sobrecompra")
        score += 1
    
    # 5. Rechazo en resistencia
    if en_zona_resist and vela_rechazo:
        factores.append("Rechazo en resistencia")
        score += 3
    
    return score, factores

# Uso:
sentimiento_score, factores = detectar_sentimiento_bajista(df, row, prev, p2)

if sentimiento_score >= 6:
    print(f"🔴 SENTIMIENTO BAJISTA FUERTE ({sentimiento_score}/10)")
    print(f"   Factores: {', '.join(factores)}")
elif sentimiento_score >= 3:
    print(f"⚠️ SENTIMIENTO BAJISTA MODERADO ({sentimiento_score}/10)")
else:
    print(f"⚪ SENTIMIENTO NEUTRAL/ALCISTA ({sentimiento_score}/10)")
```

### 8.3. Patrones de Mercado según Sentimiento

#### Sentimiento BAJISTA dominante (Score 6-10):
```
✓ Precio en resistencia o bajo EMA200
✓ EMAs descendentes (EMA9 < EMA21)
✓ Estructura de máximos y mínimos descendentes
✓ RSI en sobrecompra (>70) o girando a la baja
✓ Divergencias bajistas
✓ Volumen alto en rechazos
✓ Patrones de velas bajistas (Shooting Star, Engulfing)
```

#### Sentimiento ALCISTA dominante (Score 6-10):
```
✓ Precio en soporte o sobre EMA200
✓ EMAs ascendentes (EMA9 > EMA21)
✓ Estructura de máximos y mínimos ascendentes
✓ RSI en sobreventa (<30) o girando al alza
✓ Divergencias alcistas
✓ Volumen alto en rebotes
✓ Patrones de velas alcistas (Hammer, Bullish Engulfing)
```

#### Sentimiento NEUTRAL (Score 0-3):
```
? Señales contradictorias
? Precio en rango lateral
? EMAs entrelazadas
? RSI en zona neutral (40-60)
? Volumen bajo
→ Esperar confirmación antes de operar
```

### 8.4. Ejemplo Práctico: Análisis Completo de Sentimiento

```python
def analizar_sentimiento_completo(simbolo, df, row, prev, p2):
    """Análisis completo del sentimiento del mercado"""
    
    print(f"\n{'='*50}")
    print(f"📊 ANÁLISIS DE SENTIMIENTO - {simbolo}")
    print(f"{'='*50}\n")
    
    fecha = df.index[-2].strftime('%Y-%m-%d')
    close = row['Close']
    rsi = row['rsi']
    
    print(f"📅 Fecha: {fecha}")
    print(f"💰 Precio: {close:.2f}")
    print(f"📉 RSI: {rsi:.1f}\n")
    
    # 1. ESTRUCTURA
    print("═══ 1. ESTRUCTURA DE PRECIOS ═══")
    if estructura_bajista:
        print("  🔴 Estructura BAJISTA detectada")
    elif estructura_alcista:
        print("  🟢 Estructura ALCISTA detectada")
    else:
        print("  ⚪ Estructura NEUTRAL\n")
    
    # 2. EMAs
    print("\n═══ 2. MEDIAS MÓVILES ═══")
    print(f"  EMA 9:   {ema_fast:.2f}")
    print(f"  EMA 21:  {ema_slow:.2f}")
    print(f"  EMA 200: {ema_trend:.2f}")
    
    if emas_bajistas and bajo_ema200:
        print("  🔴 TENDENCIA BAJISTA confirmada")
    elif emas_alcistas and sobre_ema200:
        print("  🟢 TENDENCIA ALCISTA confirmada")
    else:
        print("  ⚪ TENDENCIA MIXTA")
    
    # 3. DIVERGENCIAS
    print("\n═══ 3. DIVERGENCIAS ═══")
    if divergencia_bajista:
        print("  ⚠️ DIVERGENCIA BAJISTA: Precio fuerte, RSI débil")
    elif divergencia_alcista:
        print("  ⚠️ DIVERGENCIA ALCISTA: Precio débil, RSI fuerte")
    else:
        print("  ✓ Sin divergencias relevantes")
    
    # 4. ZONAS
    print("\n═══ 4. ZONAS CLAVE ═══")
    if en_zona_resist:
        print(f"  🔴 En RESISTENCIA ({zona_resist_low}-{zona_resist_high})")
    elif en_zona_soporte:
        print(f"  🟢 En SOPORTE ({zona_soporte_low}-{zona_soporte_high})")
    else:
        dist_resist = zona_resist_low - close
        dist_soporte = close - zona_soporte_high
        print(f"  ↗️ Distancia a resistencia: {dist_resist:.0f}")
        print(f"  ↘️ Distancia a soporte: {dist_soporte:.0f}")
    
    # 5. VOLUMEN
    print("\n═══ 5. VOLUMEN ═══")
    vol_ratio = vol / vol_avg
    if vol_ratio > 1.5:
        print(f"  🔊 Volumen ALTO ({vol_ratio:.1f}x promedio)")
    elif vol_ratio > 1.0:
        print(f"  📊 Volumen NORMAL ({vol_ratio:.1f}x promedio)")
    else:
        print(f"  🔇 Volumen BAJO ({vol_ratio:.1f}x promedio)")
    
    # CONCLUSIÓN
    print(f"\n{'='*50}")
    sentimiento_bajista = sum([
        estructura_bajista * 2,
        emas_bajistas,
        bajo_ema200,
        divergencia_bajista * 2,
        en_zona_resist * 2
    ])
    
    sentimiento_alcista = sum([
        estructura_alcista * 2,
        emas_alcistas,
        sobre_ema200,
        divergencia_alcista * 2,
        en_zona_soporte * 2
    ])
    
    if sentimiento_bajista >= 6:
        print("🔴 SENTIMIENTO: BAJISTA FUERTE")
        print("   → Favorece señales de VENTA")
    elif sentimiento_alcista >= 6:
        print("🟢 SENTIMIENTO: ALCISTA FUERTE")
        print("   → Favorece señales de COMPRA")
    else:
        print("⚪ SENTIMIENTO: NEUTRAL/MIXTO")
        print("   → Esperar confirmación adicional")
    
    print(f"{'='*50}\n")
```

### 8.5. Integración en el Sistema de Alertas

```python
# En la función analizar(), después de calcular los scores:

# Detectar sentimiento general
sentimiento_bajista_score = sum([
    estructura_bajista * 2,
    emas_bajistas,
    bajo_ema200,
    divergencia_bajista * 2,
    (rsi > 60)
])

sentimiento_alcista_score = sum([
    estructura_alcista * 2,
    emas_alcistas,
    sobre_ema200,
    divergencia_alcista * 2,
    (rsi < 40)
])

# Ajustar mensaje según sentimiento
if senal_sell_alerta:
    contexto = ""
    if sentimiento_bajista_score >= 6:
        contexto = "\n🎯 <b>CONTEXTO:</b> Fuerte sentimiento bajista del mercado"
    elif sentimiento_bajista_score >= 3:
        contexto = "\n⚠️ <b>CONTEXTO:</b> Sentimiento bajista moderado"
    else:
        contexto = "\n⚪ <b>CONTEXTO:</b> Sentimiento mixto - Operar con precaución"
    
    mensaje = f"{nivel_alerta}\n" + contexto + f"\n{detalles_trade}"
    enviar_telegram(mensaje)
```

### 8.6. Mejores Prácticas

#### ✅ Operar SOLO con confluencia
- **Mínimo 3 factores** de sentimiento alineados
- Evitar señales contradictorias
- Esperar confirmación en múltiples timeframes

#### ✅ Dar prioridad a:
1. **Estructura de precios** (máximos/mínimos)
2. **Posición respecto a EMA200**
3. **Reacción en zonas clave**
4. **Divergencias**
5. **Volumen confirmatorio**

#### ❌ Evitar operar cuando:
- Sentimiento neutral (score < 3)
- Señales contradictorias
- Volumen muy bajo
- Precio en medio del rango (lejos de zonas)

---

## 9. Sistema de Scoring

### 8.1. Condiciones para señales de VENTA (Score 0-15)

```python
# Condiciones básicas (2 puntos cada una)
score_sell += 2 if en_zona_resist else 0
score_sell += 2 if vela_rechazo else 0
score_sell += 2 if vol_alto_rechazo else 0

# Condiciones RSI (1 punto cada una)
score_sell += 1 if rsi_alto_girando else 0
score_sell += 1 if rsi_sobrecompra else 0
score_sell += 1 if divergencia_bajista else 0

# Condiciones estructura (1 punto cada una)
score_sell += 1 if emas_bajistas else 0
score_sell += 1 if estructura_bajista else 0
score_sell += 1 if bajo_ema200 else 0

# Condiciones especiales (1 punto cada una)
score_sell += 1 if intento_rotura_fallido else 0
score_sell += 1 if vol_decreciente else 0
score_sell += 1 if (shooting_star and vol_alto_rechazo) else 0
```

### 8.2. Niveles de alerta basados en score

```python
# Definir niveles de señal
senal_sell_maxima = score_sell >= 10  # ⚡ Muy fuerte
senal_sell_fuerte = score_sell >= 8   # 🔴 Fuerte
senal_sell_media  = score_sell >= 6   # ⚠️ Media
senal_sell_alerta = score_sell >= 4   # 👀 Observar
```

### 8.3. Patrones de velas japonesas

```python
# Shooting Star (señal bajista)
shooting_star = (
    is_bearish and 
    upper_wick > body * 2 and 
    lower_wick < body * 0.3 and 
    en_zona_resist
)

# Hammer (señal alcista)
hammer = (
    is_bullish and 
    lower_wick > body * 2 and 
    upper_wick < body * 0.3 and 
    en_zona_soporte
)

# Engulfing bajista
bearish_engulfing = (
    is_bearish and 
    open_ >= prev['High'] and 
    close <= prev['Low'] and 
    en_zona_resist
)
```

---

## 10. Sistema Anti-Spam

### 10.1. Control de alertas duplicadas

```python
# Variables globales
alertas_enviadas = {}
ultimo_analisis = {}

# Verificar si ya se analizó la vela actual
def verificar_vela_analizada(simbolo, fecha, score_sell, score_buy):
    clave_simbolo = simbolo
    
    if clave_simbolo in ultimo_analisis:
        ultima_fecha = ultimo_analisis[clave_simbolo]['fecha']
        ultimo_score_sell = ultimo_analisis[clave_simbolo]['score_sell']
        ultimo_score_buy = ultimo_analisis[clave_simbolo]['score_buy']
        
        # Si es la misma fecha y scores similares (±1)
        if (ultima_fecha == fecha and 
            abs(ultimo_score_sell - score_sell) <= 1 and 
            abs(ultimo_score_buy - score_buy) <= 1):
            print(f"ℹ️ Vela {fecha} ya analizada - Sin cambios")
            return True
    
    # Actualizar último análisis
    ultimo_analisis[clave_simbolo] = {
        'fecha': fecha,
        'score_sell': score_sell,
        'score_buy': score_buy
    }
    return False
```

### 10.2. Evitar alertas repetidas en la misma vela

```python
clave_vela = f"{simbolo}_{fecha}"

def ya_enviada(tipo):
    return alertas_enviadas.get(f"{clave_vela}_{tipo}", False)

def marcar_enviada(tipo):
    alertas_enviadas[f"{clave_vela}_{tipo}"] = True

# Uso:
if senal_sell_maxima and not ya_enviada('SELL_MAX'):
    enviar_telegram(mensaje)
    marcar_enviada('SELL_MAX')
```

---

## 11. Ejecución y Pruebas

### 11.1. Bucle principal

```python
def main():
    print("🚀 Detector de señales iniciado")
    print(f"⏱️ Revisando cada {CHECK_INTERVAL//60} minutos")
    
    # Mensaje de inicio
    enviar_telegram(
        "🚀 <b>Detector XAUUSD iniciado</b>\\n"
        "━━━━━━━━━━━━━━━━━━━━\\n"
        "⏱️ Revisión cada 14 minutos\\n"
        "✅ Sistema anti-spam activo"
    )
    
    while True:
        for simbolo, params in SIMBOLOS.items():
            analizar(simbolo, params)
        
        print(f"\\n⏳ Esperando {CHECK_INTERVAL//60} minutos...\\n")
        time.sleep(CHECK_INTERVAL)

if __name__ == '__main__':
    main()
```

### 11.2. Ejecutar el detector

```bash
python detector_gold.py
```

### 11.3. Verificar que funciona

Deberías ver:
```
🚀 Detector de señales iniciado
⏱️ Revisando cada 14 minutos
📊 Símbolos: ['XAUUSD']
✅ Telegram enviado → 200
🔍 Analizando XAUUSD...
```

Y recibir un mensaje en Telegram confirmando el inicio.

---

## 12. Despliegue

### 12.1. Subir a GitHub

```bash
git add .
git commit -m "Initial commit: Detector XAUUSD"
git remote add origin https://github.com/tu-usuario/BotTrading.git
git push -u origin main
```

### 12.2. Ejecutar en servidor (Render, Railway, etc.)

**Crear `Procfile`:**
```
worker: python detector_gold.py
```

**Variables de entorno en el servidor:**
- `TELEGRAM_TOKEN`: Tu token del bot
- `TELEGRAM_CHAT_ID`: Tu chat ID

### 12.3. Mantener activo 24/7

El detector verifica cada 14 minutos, lo que:
- ✅ Mantiene el proceso activo
- ✅ Evita que el servidor entre en sleep
- ✅ Permite análisis oportuno de nuevas velas

---

## 📊 Ejemplo de Mensaje de Alerta

```
⚡ SELL MÁXIMA ⚡
━━━━━━━━━━━━━━━━━━━━
📈 Símbolo:    XAUUSD
💰 Precio:     4783.2
📌 SELL LIMIT: 4765.0
🛑 Stop Loss:  4825.0
🎯 TP1: 4627  R:R 2.3:1
🎯 TP2: 4374  R:R 6.5:1
🎯 TP3: 4099  R:R 11.1:1
━━━━━━━━━━━━━━━━━━━━
📊 Score: 12/15
📉 RSI: 68.2
⏱️ TF: 1D  📅 2026-04-04
```

---

## 🔍 Troubleshooting

### Error: "Import yfinance could not be resolved"
- Asegúrate de haber activado el entorno virtual
- Reinstala: `pip install yfinance`

### Error: "401 Unauthorized" al enviar Telegram
- Verifica que el `TELEGRAM_TOKEN` sea correcto
- Verifica que el archivo `.env` esté en la raíz del proyecto

### Error: "Bad Request: chat not found"
- Verifica que el `TELEGRAM_CHAT_ID` sea correcto
- Para grupos, debe ser negativo (ej: `-123456789`)

### No recibo alertas
- Verifica que el bot esté en el chat/grupo
- Revisa los logs del detector
- Comprueba que el sistema anti-spam no esté bloqueando

---

## 📚 Recursos Adicionales

- **Yahoo Finance Tickers**: https://finance.yahoo.com/
- **Telegram Bot API**: https://core.telegram.org/bots/api
- **pandas Documentation**: https://pandas.pydata.org/docs/
- **yfinance Documentation**: https://github.com/ranaroussi/yfinance

---

## ✅ Checklist Final

- [ ] Python 3.8+ instalado
- [ ] Entorno virtual creado y activado
- [ ] Dependencias instaladas
- [ ] Token de Telegram obtenido
- [ ] Chat ID obtenido
- [ ] Archivo `.env` configurado
- [ ] Código del detector creado
- [ ] Parámetros configurados
- [ ] Prueba local exitosa
- [ ] Alertas recibidas en Telegram
- [ ] Código subido a GitHub
- [ ] Sistema anti-spam verificado

---

## 🎓 Próximos Pasos

Una vez tengas funcionando el detector de XAUUSD:

1. **Crear más detectores** (SPX500, EUR/USD, etc.)
2. **Usar `run_detectors.py`** para ejecutar múltiples simultáneamente
3. **Optimizar parámetros** según tu estrategia
4. **Desplegar en servidor** para funcionamiento 24/7
5. **Backtest** tus señales con datos históricos

---

**¡Felicidades!** Has creado tu primer detector automatizado de señales de trading. 🚀

Para soporte o mejoras, consulta la documentación en el [README.md](README.md) o el archivo [EJECUTAR.md](EJECUTAR.md).
