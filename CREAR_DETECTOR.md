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
8. [Sistema de Scoring](#sistema-de-scoring)
9. [Sistema Anti-Spam](#sistema-anti-spam)
10. [Ejecución y Pruebas](#ejecución-y-pruebas)
11. [Despliegue](#despliegue)

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

## 8. Sistema de Scoring

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

## 9. Sistema Anti-Spam

### 9.1. Control de alertas duplicadas

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

### 9.2. Evitar alertas repetidas en la misma vela

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

## 10. Ejecución y Pruebas

### 10.1. Bucle principal

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

### 10.2. Ejecutar el detector

```bash
python detector_gold.py
```

### 10.3. Verificar que funciona

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

## 11. Despliegue

### 11.1. Subir a GitHub

```bash
git add .
git commit -m "Initial commit: Detector XAUUSD"
git remote add origin https://github.com/tu-usuario/BotTrading.git
git push -u origin main
```

### 11.2. Ejecutar en servidor (Render, Railway, etc.)

**Crear `Procfile`:**
```
worker: python detector_gold.py
```

**Variables de entorno en el servidor:**
- `TELEGRAM_TOKEN`: Tu token del bot
- `TELEGRAM_CHAT_ID`: Tu chat ID

### 11.3. Mantener activo 24/7

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
