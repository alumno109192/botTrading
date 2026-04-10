# 📚 WIKI — Bot Trading

**Sistema automatizado de detección de señales de trading. Analiza BTCUSD, XAUUSD, SPX500, NAS100, EURUSD, WTI OIL y PLATA con indicadores técnicos, envía alertas a Telegram y hace seguimiento de señales activas.**

> Este fichero consolida toda la documentación del proyecto.

---

## 📋 Índice

1. [Descripción General](#1-descripción-general)
2. [Instalación y Configuración](#2-instalación-y-configuración)
3. [Ejecución](#3-ejecución)
4. [Arquitectura del Sistema](#4-arquitectura-del-sistema)
5. [Estructura de Detectores](#5-estructura-de-detectores)
6. [Sistema de Señales y Scoring](#6-sistema-de-señales-y-scoring)
7. [Indicadores Técnicos Implementados](#7-indicadores-técnicos-implementados)
8. [Patrones de Velas Implementados](#8-patrones-de-velas-implementados)
9. [Análisis de Sentimiento del Mercado](#9-análisis-de-sentimiento-del-mercado)
10. [Timeframes Múltiples — 1D y 4H](#10-timeframes-múltiples--1d-y-4h)
11. [Detector Scalping 15M (XAUUSD)](#11-detector-scalping-15m-xauusd)
12. [Monitor de Señales (TP/SL)](#12-monitor-de-señales-tpsl)
13. [Base de Datos — Turso](#13-base-de-datos--turso)
14. [Sistema de Tracking de Señales](#14-sistema-de-tracking-de-señales)
15. [Dashboard de Estadísticas](#15-dashboard-de-estadísticas)
16. [Configuración de Intervalos](#16-configuración-de-intervalos)
17. [Despliegue en Render](#17-despliegue-en-render)
18. [Crear un Nuevo Detector (Guía)](#18-crear-un-nuevo-detector-guía)
19. [Análisis Completo de Indicadores Faltantes](#19-análisis-completo-de-indicadores-faltantes)
20. [Próximos Pasos y Roadmap](#20-próximos-pasos-y-roadmap)

---

## 1. Descripción General

Bot Flask desplegado en **Render** que ejecuta detectores de señales técnicas en background threads. Cuando se detecta una señal, se envía un mensaje formateado a Telegram con niveles de entrada, SL y TPs. El monitor de señales sigue el precio en tiempo real y notifica cuando se alcanzan objetivos.

### Instrumentos y Timeframes

| Instrumento | Timeframes | Directorio |
|---|---|---|
| ₿ BTCUSD | 1D + 4H | `detectors/bitcoin/` |
| 🥇 XAUUSD | 1D + 4H + 15M (scalping) | `detectors/gold/` |
| 📈 SPX500 | 1D + 4H | `detectors/spx/` |
| 📊 NAS100 | 1D + 4H | `detectors/nasdaq/` |
| 💶 EURUSD | 1D + 4H | `detectors/eurusd/` |
| 🛢️ WTI OIL | 1D + 4H | `detectors/wti/` |
| 🪙 PLATA (XAGUSD) | 1D + 4H | `detectors/silver/` |

### Variables de entorno

| Variable | Descripción |
|---|---|
| `TELEGRAM_TOKEN` | Token del bot (desde @BotFather) |
| `TELEGRAM_CHAT_ID` | ID del chat donde enviar alertas |
| `TURSO_DATABASE_URL` | URL de la BD Turso (`libsql://...`) |
| `TURSO_AUTH_TOKEN` | Token de autenticación Turso |

---

## 2. Instalación y Configuración

### Requisitos

- Python 3.8+
- Bot de Telegram
- Cuenta Turso (BD cloud gratuita)
- Cuenta Render (despliegue)

### Pasos

```bash
git clone https://github.com/alumno109192/botTrading.git
cd botTrading
python -m venv venv
.\venv\Scripts\python.exe -m pip install -r requirements.txt
```

Copiar `.env.example` a `.env` y rellenar:

```env
TELEGRAM_TOKEN=tu_token_del_bot
TELEGRAM_CHAT_ID=tu_chat_id
TURSO_DATABASE_URL=libsql://tu-db.turso.io
TURSO_AUTH_TOKEN=tu_token_turso
```

### Obtener TELEGRAM_TOKEN

1. Abrir Telegram → buscar **@BotFather**
2. Enviar `/newbot` y seguir instrucciones
3. Copiar el token proporcionado (formato: `1234567890:ABCdef...`)

### Obtener TELEGRAM_CHAT_ID

**Chat personal:**
1. Buscar **@userinfobot** en Telegram → inicia conversación → devuelve tu chat_id (número positivo)

**Grupo:**
1. Añade el bot al grupo
2. Envía un mensaje
3. Visita `https://api.telegram.org/bot<TU_TOKEN>/getUpdates`
4. Busca `"chat":{"id":-123456789}` (negativo para grupos)

### Seguridad

- `.env` en `.gitignore` — credenciales nunca se suben a GitHub
- Tokens cargados exclusivamente desde variables de entorno
- Queries a BD parametrizadas (sin concatenación de strings)

---

## 3. Ejecución

### Todos los detectores + monitor (recomendado)

```bash
.\venv\Scripts\python.exe app.py
```

### Detector individual (desarrollo/testing)

```bash
.\venv\Scripts\python.exe detectors/bitcoin/detector_bitcoin_4h.py
.\venv\Scripts\python.exe detectors/gold/detector_gold_4h.py
.\venv\Scripts\python.exe detectors/spx/detector_spx_4h.py
```

### Utilidades

```bash
# Limpiar señales duplicadas en BD (ejecutar si hay duplicados visibles)
.\venv\Scripts\python.exe limpiar_duplicados.py

# Ver estadísticas del sistema
.\venv\Scripts\python.exe stats_dashboard.py

# Probar conexión a BD
.\venv\Scripts\python.exe test_system.py

# Solo monitor de señales (debug)
.\venv\Scripts\python.exe signal_monitor.py
```

El servidor Flask escucha en `http://0.0.0.0:5000` con endpoint `/health` para keep-alive.

---

## 4. Arquitectura del Sistema

```
app.py (Flask)
├── detector_bitcoin_1d  ─┐
├── detector_bitcoin_4h   │
├── detector_gold_1d      ├─ Threads en background (cada 4-10 min)
├── detector_gold_4h      │
├── detector_gold_15m     │
├── detector_spx_1d       │
├── detector_spx_4h      ─┘
└── signal_monitor ───── Revisa señales activas cada 5 min (TP/SL)
```

**Base de datos:** Turso (SQLite en la nube) — almacena señales activas, estado y beneficio.

### Sistema de 8 Threads

| Thread | Activo | Timeframe | Intervalo | Estado |
|--------|--------|-----------|-----------|--------|
| 1 | Bitcoin | 1D | 10 min | ✅ Activo |
| 2 | Gold | 1D | 10 min | ✅ Activo |
| 3 | SPX | 1D | 10 min | ✅ Activo |
| 4 | Bitcoin | 4H | 4 min | ✅ Activo |
| 5 | Gold | 4H | 4 min | ✅ Activo |
| 6 | SPX | 4H | 4 min | ✅ Activo |
| 7 | Monitor señales | - | 3 min | ✅ Activo |
| 8 | Keep-alive | - | 1 min | ✅ Activo |

### Flujo de Datos

```
┌─────────────────────────────────────┐
│     DETECTORES (bucle periódico)    │
│   Bitcoin / Gold / SPX × 1D+4H     │
└────────────────┬────────────────────┘
                 │ Detecta señal
                 ↓
        ┌────────────────────┐
        │   db_manager.py    │
        │   guardar_senal()  │
        └────────┬───────────┘
                 │ Guarda en Turso DB
                 ↓
        ┌────────────────────┐
        │  Tabla: senales    │
        │  Estado: ACTIVA    │
        └────────┬───────────┘
                 │ Monitor revisa
                 ↓
        ┌────────────────────────┐
        │   signal_monitor.py   │
        │   verificar_niveles() │
        └────────┬───────────────┘
                 │
     ┌───────────┴───────────┐
     ↓                       ↓
TP alcanzado           SL alcanzado
Estado: TP1/2/3        Estado: SL
     └───────────┬───────────┘
                 ↓
       Notificación Telegram
```

---

## 5. Estructura de Detectores

```
detectors/
├── __init__.py
├── README.md
├── bitcoin/
│   ├── __init__.py
│   ├── detector_bitcoin_1d.py    # Timeframe diario
│   └── detector_bitcoin_4h.py    # Timeframe 4 horas
├── gold/
│   ├── __init__.py
│   ├── detector_gold_15m.py      # Scalping 15 minutos
│   ├── detector_gold_1d.py       # Timeframe diario
│   └── detector_gold_4h.py       # Timeframe 4 horas
├── spx/
│   ├── __init__.py
│   ├── detector_spx_1d.py
│   └── detector_spx_4h.py
├── nasdaq/
│   ├── __init__.py
│   ├── detector_nasdaq_1d.py     # NQ=F (Nasdaq 100 Futures)
│   └── detector_nasdaq_4h.py
├── eurusd/
│   ├── __init__.py
│   ├── detector_eurusd_1d.py     # EURUSD=X
│   └── detector_eurusd_4h.py
├── wti/
│   ├── __init__.py
│   ├── detector_wti_1d.py        # CL=F (Crude Oil Futures)
│   └── detector_wti_4h.py
└── silver/
    ├── __init__.py
    ├── detector_silver_1d.py     # SI=F (Silver Futures)
    └── detector_silver_4h.py
```

### Diferencias clave entre 1D y 4H

| Característica | 1D | 4H |
|----------------|----|----|
| Revisión | 10 min | 4 min |
| Periodo datos yfinance | 2y | 60d |
| EMA rápida | 9 | 18 |
| EMA lenta | 21 | 42 |
| EMA trend | 200 | 400 |
| RSI periodo | 14 | 28 |
| MACD | 12/26/9 | 24/52/18 |
| ATR periodo | 14 | 28 |
| Bollinger | 20 | 40 |
| Score Alerta | 3 | 5 |
| Score Media | 7 | 9 |
| Score Fuerte | 10 | 12 |
| Score Máxima | 13 | 14 |
| SL multiplier (BTC) | 2.5x | 2.0x |
| SL multiplier (Gold) | 1.5x | 1.2x |
| SL multiplier (SPX) | 2.0x | 1.6x |
| Señales/semana (estimado) | 1-2 | 3-5 |

### Símbolos en BD

Los detectores guardan señales con sufijo de timeframe:
- `BTCUSD_1D`, `BTCUSD_4H`
- `XAUUSD_1D`, `XAUUSD_4H`, `XAUUSD_15M`
- `SPX500_1D`, `SPX500_4H`

---

## 6. Sistema de Señales y Scoring

### Puntuación por indicador

| Indicador | Peso | Notas |
|---|---|---|
| Zona soporte/resistencia | +2 | Zonas definidas por parámetros |
| Patrón de vela (rechazo/rebote) | +2 | Shooting star, hammer, engulfing... |
| Volumen alto en zona | +2 | `vol > vol_avg × vol_mult` |
| RSI sobrecompra/sobreventa | +1-2 | Umbrales configurables |
| Cruce EMA rápida/lenta | +1-2 | Cruce reciente suma extra |
| Bandas de Bollinger | +2 | Toca banda extrema |
| MACD cruce bajista/alcista | +2 | Confirmado con histograma |
| ADX tendencia | +2 | ADX > 25 con DI alineado |
| Evening/Morning Star | +2 | Patrón de reversión 3 velas |
| OBV divergencia | +1 | Confirmación por volumen acumulado |
| Divergencia RSI/precio | +1 | |
| **Penalización mercado lateral** | **-3** | ADX < 20 |

**Score máximo:** ~24 puntos (varía por detector)

### Niveles de alerta

| Nivel | Score (1D) | Score (4H) | Descripción |
|---|---|---|---|
| 👀 ALERTA | ≥3 | ≥5 | Observar, posible oportunidad |
| ⚠️ MEDIA | ≥7 | ≥9 | Probabilidad moderada |
| 🔴🟢 FUERTE | ≥10 | ≥12 | Alta probabilidad |
| ⚡ MÁXIMA | ≥13 | ≥14 | Confluencia múltiple fuerte |

### Filtros obligatorios

- **Liquidez BTC:** `vol < vol_avg × 0.5` → señal bloqueada (solo BTCUSD 1D y 4H)
- **Anti-duplicado:** no se emite si ya existe señal ACTIVA para ese símbolo+dirección en BD
- **Cancelación por precio:** precio demasiado lejos de la zona (configurable por `cancelar_dist`)

### Formato de alerta Telegram

```
🔴 SELL FUERTE — BITCOIN 4H
━━━━━━━━━━━━━━━━━━━━
💰 Precio:     $95,500
📌 SELL LIMIT: $96,000
🛑 Stop Loss:  $98,000
🎯 TP1: $85,000  R:R 2.3:1
🎯 TP2: $75,000  R:R 6.5:1
🎯 TP3: $65,000  R:R 11.1:1
━━━━━━━━━━━━━━━━━━━━
📊 Score: 12/15  📉 RSI: 68.2
⏱️ TF: 4H  📅 2026-04-10
```

---

## 7. Indicadores Técnicos Implementados

### RSI (Relative Strength Index)

```python
# Período: 14 (1D) / 28 (4H)
# Umbrales:
#   XAUUSD: Sell ≥55, Buy ≤45
#   SPX500: Sell ≥60, Buy ≤40
#   BTCUSD: Sell ≥60, Buy ≤40

def calcular_rsi(series, length):
    delta = series.diff()
    gain  = delta.clip(lower=0)
    loss  = -delta.clip(upper=0)
    avg_g = gain.ewm(com=length - 1, min_periods=length).mean()
    avg_l = loss.ewm(com=length - 1, min_periods=length).mean()
    rs    = avg_g / avg_l
    return 100 - (100 / (1 + rs))
```

Señales: RSI alto/bajo girando (+1 pt), sobrecompra/sobreventa (+1 pt), divergencia (+1 pt)

### EMAs (Exponential Moving Averages)

```python
# EMA 9  → corto plazo
# EMA 21 → medio plazo
# EMA 200 → tendencia largo plazo

def calcular_ema(series, length):
    return series.ewm(span=length, adjust=False).mean()
```

Señales: EMAs alineadas (+1 pt), precio sobre/bajo EMA200 (+1 pt)

### ATR (Average True Range)

```python
# Período: 14 (1D)
# Multiplicadores: XAUUSD 1.5x, SPX500 2.0x, BTCUSD 2.5x

def calcular_atr(df, length):
    high = df['High']
    low  = df['Low']
    close_prev = df['Close'].shift(1)
    tr = pd.concat([
        high - low,
        (high - close_prev).abs(),
        (low - close_prev).abs()
    ], axis=1).max(axis=1)
    return tr.ewm(com=length - 1, min_periods=length).mean()

# SL dinámico:
# SL_venta  = max(zona_resist_high, close + ATR * multiplicador)
# SL_compra = min(zona_soporte_low,  close - ATR * multiplicador)
```

### Bandas de Bollinger ⭐⭐⭐⭐⭐

```python
from ta.volatility import BollingerBands

bb = BollingerBands(close=df['Close'], window=20, window_dev=2)
df['bb_upper'] = bb.bollinger_hband()
df['bb_mid']   = bb.bollinger_mavg()
df['bb_lower'] = bb.bollinger_lband()
df['bb_width'] = bb.bollinger_wband()  # Para detectar squeeze
```

Señales: bb_toca_superior (+2 pt VENTA), bb_toca_inferior (+2 pt COMPRA), bb_squeeze (neutral)

### MACD ⭐⭐⭐⭐⭐

```python
from ta.trend import MACD

macd = MACD(close=df['Close'], window_slow=26, window_fast=12, window_sign=9)
df['macd']        = macd.macd()
df['macd_signal'] = macd.macd_signal()
df['macd_hist']   = macd.macd_diff()
```

Señales VENTA: cruce bajista (+2 pt), divergencia bajista (+1 pt), MACD negativo (+1 pt)  
Señales COMPRA: cruce alcista (+2 pt), divergencia alcista (+1 pt), MACD positivo (+1 pt)

### ADX (Average Directional Index) ⭐⭐⭐⭐⭐

```python
from ta.trend import ADXIndicator

adx = ADXIndicator(high=df['High'], low=df['Low'], close=df['Close'], window=14)
df['adx']      = adx.adx()
df['di_plus']  = adx.adx_pos()
df['di_minus'] = adx.adx_neg()

# ADX < 20 → mercado lateral → PENALIZACIÓN -3 puntos
# ADX > 25 con DI alineado → tendencia fuerte → +2 puntos
```

**Niveles ADX:** <20 lateral, 20-25 tendencia débil, >25 tendencia fuerte

### OBV (On-Balance Volume) ⭐⭐⭐⭐

```python
# Si close > close_anterior: OBV += volumen
# Si close < close_anterior: OBV -= volumen
# También: obv_ema (EMA 20 del OBV)
```

Señales: divergencia bajista/alcista (+1 pt), OBV decreciente/creciente vs EMA (+1 pt)

### Análisis de Volumen

```python
# Promedio móvil: 20 períodos
# Multiplicadores: XAUUSD 1.2x, SPX500 1.3x, BTCUSD 1.5x

# Volumen alto: +2 puntos
# Volumen decreciente (3 velas): +1 punto
# Confluencia vela + vol alto: +1 punto extra
```

---

## 8. Patrones de Velas Implementados

### Patrones Bajistas (en Resistencia)

**Shooting Star** — Mecha superior > 2× body, mecha inferior < 0.3× body, vela bajista (+2 pts)

**Bearish Engulfing** — Open ≥ High anterior, Close ≤ Low anterior, vela bajista (+2 pts)

**Bearish Marubozu** — Body > 80% del rango total (+2 pts)

**Doji en Resistencia** — Body < 10% del rango, mecha superior > 2× body (+1 pt)

### Patrones Alcistas (en Soporte)

**Hammer** — Mecha inferior > 2× body, mecha superior < 0.3× body, vela alcista (+2 pts)

**Bullish Engulfing** — Open ≤ Low anterior, Close ≥ High anterior, vela alcista (+2 pts)

**Bullish Marubozu** — Body > 80% del rango total (+2 pts)

**Doji en Soporte** — Body < 10% del rango, mecha inferior > 2× body (+1 pt)

### Patrones de Reversión 3 Velas

**Evening Star (bajista):**
1. Vela 1: Alcista grande (body > 60% rango)
2. Vela 2: Pequeña indecisa con gap al alza (body < 30% rango)
3. Vela 3: Bajista grande que cierra dentro de vela 1
→ +2 puntos VENTA

**Morning Star (alcista):**
1. Vela 1: Bajista grande
2. Vela 2: Pequeña indecisa con gap a la baja
3. Vela 3: Alcista grande que cierra dentro de vela 1
→ +2 puntos COMPRA

---

## 9. Análisis de Sentimiento del Mercado

El sentimiento determina el contexto de la señal y suma puntos adicionales.

### Componentes del Sentimiento

**Estructura de Precios:**
```python
# Bajista: máximos y mínimos decrecientes → +2 pts sentimiento bajista
# Alcista: máximos y mínimos crecientes  → +2 pts sentimiento alcista
max_decreciente = (high < prev['High']) and (prev['High'] < p2['High'])
estructura_bajista = max_decreciente or min_decreciente
```

**EMAs:**
- EMA9 < EMA21 → tendencia bajista (+1 pt)
- Precio < EMA200 → largo plazo bajista (+1 pt)

**Divergencias RSI:**
```python
# Divergencia bajista: precio hace nuevo máximo, RSI no confirma
price_new_high    = high > df['High'].iloc[-lookback-2:-2].max()
rsi_lower_high    = rsi  < df['rsi'].iloc[-lookback-2:-2].max()
divergencia_bajista = price_new_high and rsi_lower_high and rsi > 50
```

**Proximidad/reacción en zonas S/R:**
```python
en_zona_resist  = (high >= zona_resist_low - tolerancia) and (high <= zona_resist_high + tolerancia)
intento_fallido = (high >= zona_resist_low) and (close < zona_resist_low)
```

### Scoring del Sentimiento (0-10 pts)

| Factor | Puntos |
|--------|--------|
| Estructura de precios alineada | 2 |
| EMAs alineadas + EMA200 | 1+1 |
| RSI en zona extrema | 1 |
| En zona S/R con reacción | 2 |
| Divergencia RSI | 2 |
| Tendencia largo plazo | 1 |

### Validación de Confluencia

```python
# ALTA fiabilidad: score técnico ≥6 Y sentimiento alineado ≥4
# BAJA fiabilidad: score técnico ≥6 PERO sentimiento contrario
# → Advertencia: "Operar con cautela"
```

---

## 10. Timeframes Múltiples — 1D y 4H

### Ajuste de Periodos

Los periodos de indicadores se escalan proporcionalmente al timeframe (1D → 4H: ×2):

| Indicador | 1D | 4H | 1H (futuro) |
|-----------|----|----|-------------|
| EMA Rápida | 9 | 18 | 72 |
| EMA Lenta | 21 | 42 | 168 |
| RSI | 14 | 28 | 112 |
| MACD fast/slow/signal | 12/26/9 | 24/52/18 | 96/208/72 |
| ADX | 14 | 28 | 112 |
| ATR | 14 | 28 | 112 |
| Bollinger | 20 | 40 | 160 |

### Datos yfinance

```python
# 1D
df = yf.download(ticker, period='2y',  interval='1d')   # ~504 velas

# 4H
df = yf.download(ticker, period='60d', interval='4h')   # ~240 velas max
# Nota: yfinance limita 4H a ~60 días (~120 velas para SPX por horario bursátil)

# 15M
df = yf.download(ticker, period='5d',  interval='15m')
```

### Thresholds de datos mínimos

| Timeframe | Activo | Velas mínimas |
|-----------|--------|---------------|
| 4H | BTC / Gold | 200 |
| 4H | SPX | 80 (menos velas por horario bursátil) |

### Volumen de señales esperado

| Timeframe | Señales/semana por activo | Total 3 activos |
|-----------|---------------------------|-----------------|
| 1D | 1-2 | 3-6 |
| 4H | 3-5 | 9-15 |
| **Total** | - | **12-21** |

### Ajuste de SL/TP por timeframe

**4H (menos agresivo que 1D):**
```python
sl_compra = buy_limit  - (2.0 * atr_4h)   # Era 2.5x en 1D
tp1 = 1.2 × riesgo    # Objetivos más cercanos
tp2 = 2.0 × riesgo
tp3 = 3.0 × riesgo
```

---

## 11. Detector Scalping 15M (XAUUSD)

Detector especializado en scalping para XAUUSD en timeframe de 15 minutos.

### Características

- **Timeframe:** 15M
- **Frecuencia de análisis:** Cada 2 minutos
- **Datos:** `period='5d', interval='15m'`

### Indicadores (periodos adaptados a scalping)

| Indicador | Periodo | Notas |
|-----------|---------|-------|
| RSI | 9 | Más sensible |
| EMA fast | 5 | Ultra rápida |
| EMA slow | 13 | Confirmación |
| EMA trend | 50 | Tendencia corto plazo |
| ATR | 10 | Sensible a volatilidad |
| ADX | 14 | Fuerza tendencia |

### Objetivos

- **TP1:** $30 (~1%)
- **TP2:** $50 (~1.5%)
- **TP3:** $80 (~2.5%)
- **SL:** 1.5x ATR (ajustado)

### Niveles de señal (más laxos que 1D)

| Nivel | Score |
|-------|-------|
| ⚡ SCALP | ≥3 |
| ⚠️ MEDIA | ≥5 |
| 🔥 FUERTE | ≥8 |

### Control de pérdidas

- Máximo 3 pérdidas consecutivas → trading pausado automáticamente
- Reanudación solo con señales FUERTES (≥8 puntos)

### Símbolo en BD: `XAUUSD_15M`

### Mejor momento de uso

- Sesión de Londres: 08:00-16:00 GMT
- Overlap London/NY: 13:00-16:00 GMT (máxima liquidez)
- Evitar: sesión asiática, viernes tarde, festivos

---

## 12. Monitor de Señales (TP/SL)

`signal_monitor.py` revisa cada **3 minutos** todas las señales ACTIVAS en BD.

### Acciones por evento

| Evento | Mensaje Telegram | Acción recomendada |
|---|---|---|
| TP1 alcanzado | 🎯 TP1 ALCANZADO | Cerrar 33% + mover SL a breakeven |
| TP2 alcanzado | 🎯🎯 TP2 ALCANZADO | Cerrar 33% + mover SL a TP1 |
| TP3 alcanzado | 🎯🎯🎯 TP3 ALCANZADO | Cerrar 100% restante |
| SL alcanzado | ❌ STOP LOSS | Cerrar 100% |

### Estados de señales

- `ACTIVA` — Señal abierta, esperando TP o SL
- `TP1` — Alcanzó primer objetivo
- `TP2` — Alcanzó segundo objetivo
- `TP3` — Alcanzó tercer objetivo
- `SL` — Stop Loss activado
- `CANCELADA` — Más de 7 días activa

### Lógica de verificación

```python
# Para señal COMPRA
if precio_actual >= tp3:    → estado TP3 (cierre)
elif precio_actual >= tp2:  → estado TP2
elif precio_actual >= tp1:  → estado TP1
elif precio_actual <= sl:   → estado SL (cierre)

# Para señal VENTA (invertido)
if precio_actual <= tp3:    → estado TP3
elif precio_actual <= tp2:  → estado TP2
elif precio_actual <= tp1:  → estado TP1
elif precio_actual >= sl:   → estado SL
```

---

## 13. Base de Datos — Turso

**Turso** es una base de datos SQLite cloud (HTTP API), accedida desde `db_manager.py`.

### URL de conexión

```
libsql://senales-alumno109192.aws-eu-west-1.turso.io
```

(El token se configura en `TURSO_AUTH_TOKEN` en `.env`)

### Estructura de tablas

```sql
CREATE TABLE senales (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp DATETIME NOT NULL,
    simbolo VARCHAR(20) NOT NULL,       -- Ej: BTCUSD_4H
    direccion VARCHAR(10) NOT NULL,     -- COMPRA o VENTA
    precio_entrada DECIMAL(12,2) NOT NULL,
    precio_actual  DECIMAL(12,2),
    tp1 DECIMAL(12,2) NOT NULL,
    tp2 DECIMAL(12,2) NOT NULL,
    tp3 DECIMAL(12,2) NOT NULL,
    sl  DECIMAL(12,2) NOT NULL,
    score INTEGER NOT NULL,
    indicadores TEXT,                   -- JSON
    patron_velas TEXT,
    estado VARCHAR(20) DEFAULT 'ACTIVA',
    tp1_alcanzado BOOLEAN DEFAULT FALSE,
    tp2_alcanzado BOOLEAN DEFAULT FALSE,
    tp3_alcanzado BOOLEAN DEFAULT FALSE,
    sl_alcanzado  BOOLEAN DEFAULT FALSE,
    fecha_tp1 DATETIME,
    fecha_tp2 DATETIME,
    fecha_tp3 DATETIME,
    fecha_sl  DATETIME,
    fecha_cierre DATETIME,
    max_beneficio_pct  DECIMAL(8,4),
    beneficio_final_pct DECIMAL(8,4),
    duracion_minutos INTEGER,
    notas TEXT,
    version_detector VARCHAR(20)
);
```

### Notas importantes sobre Turso

Todos los valores numéricos retornados desde Turso vienen como strings — requieren conversión explícita:

```python
precio_entrada = float(senal['precio_entrada'])
tp1 = float(senal['tp1'])
count = int(result.rows[0]['count'])
```

### Función anti-duplicados

```python
# Verifica si existe señal ACTIVA para mismo símbolo+dirección (sin límite de tiempo)
def existe_senal_reciente(simbolo, direccion):
    # Consulta: WHERE simbolo = ? AND direccion = ? AND estado = 'ACTIVA'
    # Nota: el símbolo debe incluir sufijo de timeframe: 'BTCUSD_4H'
```

---

## 14. Sistema de Tracking de Señales

### Archivos del sistema

| Archivo | Función |
|---------|---------|
| `db_manager.py` | CRUD completo sobre Turso |
| `signal_monitor.py` | Bucle de seguimiento de TP/SL |
| `stats_dashboard.py` | Métricas y estadísticas |
| `limpiar_duplicados.py` | Limpieza puntual de duplicados |
| `test_system.py` | Tests de verificación |

### Integración en detectores

```python
import json
from datetime import datetime, timezone
from db_manager import DatabaseManager

db = DatabaseManager()

# Al detectar señal:
if not db.existe_senal_reciente(f"{simbolo}_4H", 'VENTA'):
    senal_data = {
        'timestamp': datetime.now(timezone.utc),
        'simbolo': f"{simbolo}_4H",
        'direccion': 'VENTA',
        'precio_entrada': sell_limit,
        'tp1': tp1_v, 'tp2': tp2_v, 'tp3': tp3_v,
        'sl': sl_venta,
        'score': score_sell,
        'indicadores': json.dumps({'rsi': round(rsi, 1), ...}),
        'patron_velas': 'Shooting Star' if shooting_star else '',
        'version_detector': '2.0'
    }
    senal_id = db.guardar_senal(senal_data)
    enviar_telegram(msg)
```

### Controles del sistema

- Anti-duplicados: no guarda señales con mismo símbolo+dirección ACTIVA
- Señales con >7 días activas se cierran automáticamente como CANCELADAS
- Máximo 50 señales activas simultáneas

---

## 15. Dashboard de Estadísticas

`stats_dashboard.py` genera métricas del sistema:

```python
from stats_dashboard import StatsDashboard

dashboard = StatsDashboard()

# Reporte completo
print(dashboard.generar_reporte_completo())

# Win rate
print(f"Win Rate: {dashboard.calcular_win_rate('all'):.1f}%")

# Ranking por símbolo
print(dashboard.obtener_ranking_simbolos())

# Exportar a CSV
dashboard.exportar_csv('mis_senales.csv', periodo_dias=30)
```

### Métricas disponibles

- Win rate por símbolo y período
- Expectancy matemática: `(win_rate × avg_win) - (loss_rate × avg_loss)`
- Profit factor: `total_wins / total_losses`
- Mejores combinaciones de indicadores
- Análisis por hora del día
- Duración promedio de señales

### Consultas SQL útiles

```sql
-- Win rate por símbolo
SELECT simbolo,
       COUNT(*) as total,
       SUM(CASE WHEN estado IN ('TP1','TP2','TP3') THEN 1 ELSE 0 END) as wins,
       ROUND(100.0 * SUM(CASE WHEN estado IN ('TP1','TP2','TP3') THEN 1 ELSE 0 END) / COUNT(*), 2) as win_rate
FROM senales WHERE estado != 'ACTIVA'
GROUP BY simbolo;

-- Señales del día
SELECT * FROM senales
WHERE DATE(timestamp) = DATE('now')
ORDER BY timestamp DESC;

-- Señales activas
SELECT * FROM senales WHERE estado = 'ACTIVA';
```

---

## 16. Configuración de Intervalos

### Configuración actual (Balance Óptimo)

| Componente | Intervalo | Revisiones/vela |
|------------|-----------|-----------------|
| Detectores 1D | 10 minutos | ~144/día |
| Detectores 4H | 4 minutos | ~60/vela |
| Monitor Señales | 3 minutos | ~20/hora |
| Keep-alive | 1 minuto | — |

### Modificar intervalos

```python
# Detectores 1D:
detectors/bitcoin/detector_bitcoin_1d.py → CHECK_INTERVAL = X * 60
detectors/gold/detector_gold_1d.py      → CHECK_INTERVAL = X * 60
detectors/spx/detector_spx_1d.py        → CHECK_INTERVAL = X * 60

# Detectores 4H:
detectors/bitcoin/detector_bitcoin_4h.py → CHECK_INTERVAL = X * 60
# ... etc

# Monitor:
signal_monitor.py → time.sleep(X * 60)
```

Después: `git add . && git commit -m "feat: Ajustar intervalos" && git push origin main`

### Perfiles de configuración

| Perfil | 1D | 4H | Monitor | Uso |
|--------|----|----|---------|-----|
| **Conservador** | 15 min | 5 min | 5 min | Swing traders, Render gratuito |
| **Óptimo (actual)** ✅ | 10 min | 4 min | 3 min | Day traders |
| **Agresivo** | 5 min | 2 min | 2 min | Scalpers (riesgo rate limiting) |

### Consumo estimado (perfil óptimo)

- Calls Yahoo Finance: ~500/día
- CPU: Bajo-Medio
- RAM: <512MB
- Logs: ~50MB/día

### Señales de que los intervalos necesitan ajuste

**Demasiado lento:**
- Señales enviadas cuando precio ya se movió >2%
- TP detectado 10+ min después de alcanzarse

**Demasiado rápido:**
- Errores 429 de Yahoo Finance
- Logs >150MB/día
- CPU >50% constante

---

## 17. Despliegue en Render

### Variables de entorno en Render

Configurar en Dashboard → Service → Environment:

| Key | Descripción |
|-----|-------------|
| `TELEGRAM_TOKEN` | Token del bot |
| `TELEGRAM_CHAT_ID` | ID del chat |
| `TURSO_DATABASE_URL` | URL de la BD Turso |
| `TURSO_AUTH_TOKEN` | Token de autenticación |
| `PYTHONUNBUFFERED` | `1` — para ver logs en tiempo real |

### ¿Por qué PYTHONUNBUFFERED?

Render bufferiza la salida de Python por defecto. Sin esta variable, los logs no aparecen en tiempo real en el dashboard de Render.

Alternativa: cambiar el comando de inicio a:
```
PYTHONUNBUFFERED=1 python app.py
```

### Flujo de deploy

```bash
git add .
git commit -m "feat: descripción del cambio"
git push origin main
# Render despliega automáticamente en 2-3 minutos
```

### Verificación post-deploy

1. Ir al dashboard de Render → Logs
2. Verificar que aparecen los 8 threads iniciados
3. Llamar a `https://tu-app.onrender.com/status` → ver estado detectores
4. Llamar a `https://tu-app.onrender.com/cron` → ver logs de hilos

### Endpoints disponibles

- `/` o `/health` — health check (keep-alive externo)
- `/status` — estado de todos los detectores
- `/cron` — log de threads activos

---

## 18. Crear un Nuevo Detector (Guía)

### Paso 1: Parámetros del instrumento

```python
SIMBOLOS = {
    'XAUUSD': {
        'ticker_yf': 'GC=F',
        'zona_resist_high':   4900.0,
        'zona_resist_low':    4750.0,
        'zona_soporte_high':  4400.0,
        'zona_soporte_low':   4200.0,
        'tp1_venta':   4627.0,
        'tp2_venta':   4374.0,
        'tp3_venta':   4099.0,
        'tp1_compra':  4900.0,
        'tp2_compra':  5100.0,
        'tp3_compra':  5300.0,
        'tolerancia':        30.0,
        'limit_offset_pct':  0.3,
        'anticipar_velas':   3,
        'cancelar_dist':     1.0,
        'rsi_length':    14,
        'rsi_min_sell':  55.0,
        'rsi_max_buy':   45.0,
        'ema_fast_len':  9,
        'ema_slow_len':  21,
        'ema_trend_len': 200,
        'atr_length':    14,
        'atr_sl_mult':   1.5,
        'vol_mult':      1.2,
    }
}
```

### Paso 2: Función principal

```python
def analizar(simbolo, params):
    ticker = params['ticker_yf']
    df = yf.download(ticker, period='2y', interval='1d', progress=False)

    if len(df) < 200:
        print("Datos insuficientes")
        return

    # Calcular indicadores
    df['rsi']      = calcular_rsi(df['Close'], params['rsi_length'])
    df['ema_fast'] = calcular_ema(df['Close'], params['ema_fast_len'])
    df['ema_slow'] = calcular_ema(df['Close'], params['ema_slow_len'])
    df['atr']      = calcular_atr(df, params['atr_length'])
    # ... Bollinger, MACD, ADX, OBV ...

    row  = df.iloc[-2]   # Última vela CERRADA
    prev = df.iloc[-3]
    p2   = df.iloc[-4]

    # Calcular scores
    score_sell = 0
    score_buy  = 0
    # ... lógica de scoring ...

    # Sistema anti-spam: no enviar si ya se analizó esta vela con scores similares
    clave_vela = f"{simbolo}_{fecha}"
    if clave_vela in alertas_enviadas:
        return

    # Verificar anti-duplicados en BD
    if not db.existe_senal_reciente(f"{simbolo}_1D", 'VENTA'):
        if score_sell >= params.get('min_score', 7):
            enviar_telegram(mensaje_venta)
            db.guardar_senal(senal_data)
```

### Paso 3: Sistema anti-spam en memoria

```python
alertas_enviadas = {}
ultimo_analisis  = {}

clave_vela = f"{simbolo}_{fecha}"

def ya_enviada(tipo):
    return alertas_enviadas.get(f"{clave_vela}_{tipo}", False)

def marcar_enviada(tipo):
    alertas_enviadas[f"{clave_vela}_{tipo}"] = True
```

### Paso 4: Bucle principal

```python
def main():
    enviar_telegram("🚀 Detector XAUUSD iniciado")
    while True:
        for simbolo, params in SIMBOLOS.items():
            analizar(simbolo, params)
        time.sleep(CHECK_INTERVAL)

if __name__ == '__main__':
    main()
```

### Paso 5: Integrar en app.py

```python
from detectors.gold import detector_gold_1d

hilo_gold_1d = threading.Thread(
    target=ejecutar_detector,
    args=("DETECTOR GOLD 1D", detector_gold_1d, "gold_1d"),
    name="DetectorGold1D",
    daemon=True
)
hilos.append(hilo_gold_1d)
threads_detectores['gold_1d'] = hilo_gold_1d
```

---

## 19. Análisis Completo de Indicadores Faltantes

### Cobertura actual vs objetivo

| Categoría | Implementado | Faltante | Cobertura |
|-----------|-------------|---------|-----------|
| Velas japonesas | 8 patrones | 14 | 36% |
| Momentum | RSI | MACD, Stoch, CCI, W%R | 20% |
| Tendencia | EMA×3 | ADX, SAR, Ichimoku | 25% |
| Volumen | Vol Avg | OBV, VWAP, Profile, CMF | 20% |
| Volatilidad | ATR | Bollinger, Keltner, ATR% | 25% |
| Estructura | 4 items | Fibo, Pivots, FVG, OB, BB | 44% |

**Nota:** Con los 5 indicadores de alta prioridad ya implementados (Bollinger, MACD, Evening/Morning Star, OBV, ADX), la cobertura real es mayor que la tabla anterior.

### Indicadores de alta prioridad (ya implementados)

1. **Bandas de Bollinger** — extremos de volatilidad, squeeze
2. **MACD** — momentum, cruces, divergencias
3. **Evening/Morning Star** — reversiones de 3 velas
4. **OBV** — acumulación/distribución institucional
5. **ADX** — filtro de mercados laterales (penalización -3 pts)

### Indicadores de prioridad media (por implementar)

6. **Fibonacci Retracements** — TP dinámicos (38.2%, 61.8%)
7. **Stochastic Oscillator** — extremos precisos, complementa RSI
8. **Three Black Crows / Three White Soldiers** — tendencia fuerte continuada
9. **Pivot Points** — niveles intradía objetivos
10. **Higher Timeframe Bias** — contexto macro (semanal confirma diario)

### Indicadores de prioridad baja

11. **Ichimoku Cloud** — sistema completo multi-señal
12. **Fair Value Gaps (FVG)** — desequilibrios de precio
13. **Order Blocks** — zonas institucionales
14. **VWAP** — precio justo del día
15. **Correlaciones** — DXY vs Gold, VIX vs SPX

### Patrones multi-vela faltantes

- Head & Shoulders / Inverse H&S
- Double Top / Bottom
- Triple Top / Bottom
- Triángulos (ascendente, descendente, simétrico)
- Wedges (cuñas)
- Flags & Pennants

### Ejemplo de señal con confluencia completa

```
🔴 VENTA Score: 18/24

✅ En zona resistencia (2 pts)
✅ Evening Star confirmado (2 pts)
✅ BB toca superior (2 pts)
✅ RSI sobrecompra (1 pt)
✅ MACD cruce bajista (2 pts)
✅ MACD divergencia bajista (1 pt)
✅ ADX bajista fuerte (2 pts)
✅ OBV divergencia bajista (1 pt)
✅ OBV decreciente (1 pt)
✅ EMAs bajistas (1 pt)
✅ Estructura bajista (1 pt)
✅ Shooting star + vol alto (1 pt)
✅ Bajo EMA200 (1 pt)
```

---

## 20. Próximos Pasos y Roadmap

### Corto plazo (1-2 semanas)

- [ ] Monitorear señales y acumular datos en BD
- [ ] Crear hoja de seguimiento de señales (Excel/Sheets):
  `| Fecha | Activo | Tipo | Score | Precio Entrada | SL | TP1 | Resultado |`
- [ ] Calcular win rate preliminary por símbolo
- [ ] Analizar efectividad del filtro ADX (¿cuántas señales falsas evitó?)

### Medio plazo (1 mes)

- [ ] Implementar Fibonacci Retracements para TPs dinámicos
- [ ] Implementar Stochastic como complemento al RSI
- [ ] Ajustar umbrales si señales tienen demasiado ruido:
  ```python
  # Opcional, más conservador:
  senal_sell_maxima = score_sell >= 14  # Era 10/13
  senal_sell_fuerte = score_sell >= 11  # Era 8/10
  ```
- [ ] Implementar Higher Timeframe Bias (semanal confirma diario)
- [ ] Reportes automáticos diarios por Telegram

### Largo plazo (2+ meses)

- [ ] Backtesting completo con datos históricos
- [ ] Dashboard web con Flask/Streamlit + Plotly
- [ ] Machine Learning para predecir probabilidad de éxito de señales
- [ ] Integración con broker para ejecución automática
- [ ] Gestión de riesgo automática (position sizing)

### Cálculo de Position Sizing

```python
# Fórmula recomendada:
riesgo_por_trade = capital * 0.01  # 1% del capital
size = riesgo_por_trade / abs(entry - stop_loss)

# Ejemplo: Capital $10,000, entry gold $2,700, SL $2,650
# size = $100 / $50 = 2 contratos
```

### Sesiones con mayor probabilidad de señales válidas

| Sesión | Horario GMT | Activo recomendado |
|--------|------------|-------------------|
| London Open | 08:00-10:00 | XAUUSD |
| NY Open | 13:00-15:00 | BTCUSD, SPX500 |
| Overlap L/NY | 13:00-16:00 | Todos |
| Asiática | 00:00-08:00 | Evitar (baja liquidez) |

---

## 📁 Mapa de Archivos del Proyecto

```
BotTrading/
├── app.py                        # Flask + orquestador de threads
├── db_manager.py                 # CRUD Turso DB
├── signal_monitor.py             # Monitor de TP/SL (cada 3 min)
├── stats_dashboard.py            # Estadísticas y métricas
├── run_detectors.py              # Ejecutor alternativo (sin Flask)
├── run_scalping_15m.py           # Ejecutor solo scalping
├── limpiar_duplicados.py         # Utilidad limpieza de duplicados
├── test_system.py                # Tests de verificación
├── test_db_simple.py             # Test conexión BD
├── test_detector_4h.py           # Test detector 4H
├── test_telegram.py              # Test envío Telegram
├── requirements.txt
├── .env                          # Credenciales (NO en git)
├── .env.example                  # Plantilla de variables
├── detectors/
│   ├── bitcoin/
│   │   ├── detector_bitcoin_1d.py
│   │   └── detector_bitcoin_4h.py
│   ├── gold/
│   │   ├── detector_gold_15m.py
│   │   ├── detector_gold_1d.py
│   │   └── detector_gold_4h.py
│   └── spx/
│       ├── detector_spx_1d.py
│       └── detector_spx_4h.py
└── WIKI.md                       # Esta documentación
```

---

*Última actualización: Abril 2026 — v3.0*
