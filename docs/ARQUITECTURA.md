# 🏗️ Arquitectura del Sistema

Descripción detallada de cómo funciona el bot trading: threads, flujo de datos, indicadores, base de datos.

---

## 📋 Contenido

1. [Descripción General](#descripción-general)
2. [Arquitectura de Threads](#arquitectura-de-threads)
3. [Flujo de Datos](#flujo-de-datos)
4. [Sistema Multi-Timeframe](#sistema-multi-timeframe)
5. [Indicadores Técnicos](#indicadores-técnicos)
6. [Sistema de Scoring](#sistema-de-scoring)
7. [Base de Datos](#base-de-datos)
8. [Integración Telegram](#integración-telegram)

---

## Descripción General

**Bot Trading** es un sistema automatizado de detección de señales de trading desplegado como servicio Flask en Render. 

### Qué hace

1. Descarga datos OHLCV en tiempo real vía **yfinance**
2. Aplica 11+ indicadores técnicos en cada activo/timeframe
3. Puntúa cada vela con un sistema de scoring (0-24 puntos)
4. Emite alertas formateadas a Telegram con routing por tópico
5. Almacena señales en base de datos Turso (SQLite cloud)
6. Monitoriza TP/SL y notifica cuando se alcanzan

### Activos Soportados

| Activo | Ticker | Timeframes | Estado |
|--------|--------|-----------|--------|
| XAUUSD | GC=F | 1D, 4H, 1H, 15M, 5M | ✅ Activo |
| EURUSD | EURUSD=X | 1D, 4H, 15M | ✅ Activo |
| BTCUSD | BTC-USD | 1D, 4H | ⏸️ Pausado |
| SPX500 | ^GSPC | 1D, 4H, 15M | ⏸️ Pausado |
| NASDAQ | NQ=F | 1D, 4H | ⏸️ Pausado |
| WTIUSD | CL=F | 1D, 4H | ⏸️ Pausado |
| Silver | SI=F | 1D, 4H | ⏸️ Pausado |

---

## Arquitectura de Threads

### Diagrama del Sistema

```
┌──────────────────────────────────────────────────────────┐
│                  Flask App (app.py)                      │
│                                                          │
│  ┌────────────────────────────────────────────────────┐ │
│  │              Background Threads                    │ │
│  │                                                   │ │
│  │  Gold 1D  ──┐                                     │ │
│  │  Gold 4H  ──│  Cada 4-10 minutos:                │ │
│  │  Gold 1H  ──├─ yfinance download                 │ │
│  │  Gold 15M ──│─ Calcular indicadores              │ │
│  │  Gold 5M   ─┤─ Scoring + confluencia              │ │
│  │  EUR 1D   ──│─ Telegram + DB                      │ │
│  │  EUR 4H   ──│                                     │ │
│  │  EUR 15M ──┘                                     │ │
│  │                                                   │ │
│  │  signal_monitor ───── Cada 3 minutos (TP/SL)    │ │
│  │  keep_alive ────────── Cada 1 minuto (/health)  │ │
│  └────────────────────────────────────────────────────┘ │
│                                                          │
│  Endpoints Flask:                                        │
│    GET  /              → Health básico                  │
│    GET  /health        → Health para Render             │
│    GET  /status        → Estado detallado (auth req)    │
│    GET  /cron          → Log threads activos            │
│    POST /webhook       → Recibe señales externas        │
└──────────────────────────────────────────────────────────┘
          │                                    │
          ▼                                    ▼
    Telegram API                        Turso (SQLite cloud)
    (Foro con tópicos)                  (libsql://db.turso.io)
```

### Threads Activos en Producción

| # | Thread | Activo | TF | Intervalo | Rol |
|---|--------|--------|----|-----------|----|
| 1 | `detector_gold_1d` | XAUUSD | 1D | 10 min | Swing |
| 2 | `detector_gold_4h` | XAUUSD | 4H | 4 min | Swing |
| 3 | `detector_gold_1h` | XAUUSD | 1H | 4 min | Intraday |
| 4 | `detector_gold_15m` | XAUUSD | 15M | 2 min | Scalping |
| 5 | `detector_gold_5m` | XAUUSD | 5M | 2 min | Scalping |
| 6 | `detector_eurusd_1d` | EURUSD | 1D | 10 min | Swing |
| 7 | `detector_eurusd_4h` | EURUSD | 4H | 4 min | Swing |
| 8 | `detector_eurusd_15m` | EURUSD | 15M | 2 min | Scalping |
| 9 | `signal_monitor` | Todos | — | 3 min | Monitor |
| 10 | `keep_alive` | — | — | 1 min | Keep-alive |

---

## Flujo de Datos

### A. Generación de Señal (Detector)

```
1. Thread despierta (cada X minutos)
   │
2. Descargar OHLCV de yfinance
   → Thread-safe: usa _yf_lock (double-check locking)
   │
3. Calcular indicadores sobre DataFrame:
   → RSI, EMA (fast/slow/trend)
   → ATR (Average True Range)
   → Bollinger Bands
   → MACD (Moving Average Convergence Divergence)
   → ADX (Average Directional Index)
   → OBV (On-Balance Volume)
   │
4. Analizar la última vela CERRADA (df.iloc[-2])
   → score_sell = 0 | score_buy = 0
   → Cada indicador suma/resta puntos
   │
5. Aplicar filtros de bloqueo:
   ✗ Anti-duplicado: ¿existe señal reciente?
   ✗ Precio lejos de zona S/R: ¿distancia > cancelar_dist?
   ✗ Liquidez insuficiente: ¿volumen < vol_avg × 0.5?
   │
6. Verificar confluencia multi-TF:
   → tf_bias.verificar_confluencia(símbolo, TF, dirección)
   → Si TF superiores contradicen → SEÑAL BLOQUEADA
   │
7. Si score ≥ umbral + filtros OK:
   a. Calcular niveles (SL, TP1, TP2, TP3)
   b. Formatear mensaje para Telegram
   c. Enviar a Telegram API (con THREAD_ID correcto)
   d. db_manager.guardar_senal()
   e. tf_bias.publicar_sesgo(símbolo, TF, dirección, score)
   │
8. Sleep(CHECK_INTERVAL) → repetir
```

### B. Monitorización de Señales (signal_monitor)

```
1. SELECT * FROM senales WHERE estado='ACTIVA'
   │
2. Para cada señal:
   → Obtener precio actual de yfinance
   │
3. Comparar precio con niveles:
   COMPRA: precio >= tp3 → TP3 alcanzado
           precio >= tp2 → TP2 alcanzado
           precio >= tp1 → TP1 alcanzado
           precio <= sl  → STOP LOSS
   │
4. Si nivel alcanzado:
   → Calcular beneficio %
   → Actualizar BD: estado='CLOSED', profit_pct
   → Enviar notificación Telegram
   │
5. Cerrar señales > 7 días como CANCELADAS
   │
6. Sleep(3 min) → repetir
```

---

## Sistema Multi-Timeframe

### El Problema

Los detectores tradicionales analizan cada timeframe **aisladamente**. Resultado: una señal válida en 15M puede ir contra la tendencia en 4H → pérdidas innecesarias.

**Ejemplo:**
- 1D: BEARISH (en resistencia)
- 15M: BULLISH (rebote momentáneo)
- **Sin sesgo:** se envía compra en plena resistencia diaria ❌

### La Solución: Cascada Multi-TF

Implementado en `tf_bias.py` — cada detector publica su sesgo (dirección + confianza) y los TF menores **verifican confluencia** antes de emitir señal.

```
1W → analiza y publica sesgo (BEARISH/BULLISH/NEUTRAL)
 │
 └─ 1D → verifica sesgo 1W
        → si coincide, publica su propio sesgo
           │
           └─ 4H → verifica sesgo 1D
                  → si coincide, publica sesgo
                     │
                     └─ 1H → verifica sesgo 4H
                            → si coincide, publica sesgo
                               │
                               └─ 15M → verifica 1D (árbitro final)
                                       → si coincide → DISPARA SEÑAL ✅
```

### Regla de Oro

**El 1D nunca puede ser ignorado.** Si 1D es BEARISH:
- No se envían señales COMPRA en 4H, 1H, 15M
- Solo se permiten señales VENTA (a favor de la tendencia 1D)

### Estructura de Datos

```python
bias_store = {
    'XAUUSD': {
        '1D':  {'bias': 'BEARISH',  'score': 11, 'ts': datetime},
        '4H':  {'bias': 'BEARISH',  'score': 8,  'ts': datetime},
        '15M': {'bias': 'BULLISH',  'score': 6,  'ts': datetime},
    },
    'EURUSD': { ... },
}
```

### Mensaje Telegram Enriquecido

```
🔴 SELL FUERTE — GOLD (4H)
━━━━━━━━━━━━━━━━━━━━
💰 Precio:     $3,320.00
📌 SELL LIMIT: $3,325.00
🛑 Stop Loss:  $3,348.00
🎯 TP1: $3,272  R:R 2.0:1
━━━━━━━━━━━━━━━━━━━━
📊 Score: 14/21  RSI: 67.2
━━━━━━━━━━━━━━━━━━━━
🔗 CONFLUENCIA MULTI-TF (4/5):
  ✅ 1D  → BEARISH  (score 11)
  ✅ 4H  → BEARISH  (score 14)  ← este TF
  ✅ 1H  → BEARISH  (score 8)
  ❌ 15M → BULLISH  (score 6)
```

---

## Indicadores Técnicos

Ver [INDICADORES.md](INDICADORES.md) para detalle técnico de fórmulas.

### Indicadores Soportados

| Indicador | Cálculo | Uso |
|-----------|---------|-----|
| **RSI** | Wilder's (período 14/28/56) | Sobrecompra/sobreventa, divergencias |
| **EMA** | Exponential Moving Average | Tendencia (fast/slow/trend) |
| **ATR** | Average True Range (Wilder) | Volatilidad, tamaño SL |
| **Bollinger Bands** | SMA ± (StdDev × 2) | Volatilidad extrema |
| **MACD** | 12/26/9 cruce exponencial | Confirmación de giro |
| **ADX** | Average Directional Index | Fuerza de tendencia (>25 fuerte) |
| **OBV** | On-Balance Volume | Confirmación por volumen |
| **Patrones Velas** | Envolvente, Doji | Reversión local |

### Parámetros Escalados por Timeframe

| Parámetro | 1D | 4H | 1H | 15M | 5M |
|-----------|----|----|----|----|---|
| **Período**| 2y | 60d | 30d | 5d | 2d |
| **EMA Fast** | 9 | 18 | 36 | 5 | 5 |
| **EMA Slow** | 21 | 42 | 84 | 13 | 13 |
| **EMA Trend** | 200 | 400 | — | 50 | 50 |
| **RSI** | 14 | 28 | 56 | 9 | 9 |
| **ATR** | 14 | 28 | 28 | 10 | 10 |
| **Bollinger** | 20 | 40 | 80 | — | — |
| **MACD** | 12/26/9 | 24/52/18 | — | — | — |
| **ADX** | 14 | 28 | — | 14 | 14 |

---

## Sistema de Scoring

### Cálculo de Puntos

Cada vela se analiza en múltiples dimensiones. El score total determina la fuerza de la señal.

```
SCORE TOTAL = suma de puntos de todos los indicadores
              - penalizaciones (mercado lateral, etc.)

Niveles:
  4-5:   👀 ALERTA (observar, posible oportunidad)
  6-8:   ⚠️  MEDIA (probabilidad moderada)
  9-11:  🔴🟢 FUERTE (alta probabilidad)
  12+:   ⚡ MÁXIMA (confluencia múltiple fuerte)
```

### Tabla de Puntuación

| Factor | Condición | Puntos |
|--------|-----------|--------|
| **Zona S/R** | Precio en zona soporte/resistencia | +2 |
| **Patrón Vela** | Rechazo o rebote confirmado | +2 |
| **Volumen** | Vol > vol_avg × vol_mult | +2 |
| **RSI** | Sobrecompra/sobreventa | +1-2 |
| **Cruce EMA** | Cruce rápida/lenta reciente | +1-2 |
| **Bollinger** | Toca banda extrema | +2 |
| **MACD** | Cruce alcista/bajista | +2 |
| **ADX** | Tendencia fuerte (>25) | +2 |
| **Evening/Morning Star** | Patrón 3-vela | +2 |
| **OBV** | Divergencia volumen | +1 |
| **Divergencia RSI** | RSI/precio divergencia | +1 |
| **Penalización ADX** | Mercado lateral (ADX < 20) | **-3** |

---

## Base de Datos

### Tabla: signals

Almacena todas las señales generadas.

| Campo | Tipo | Descripción |
|-------|------|-------------|
| `id` | INTEGER PK | ID único |
| `timestamp` | TEXT | Fecha/hora de generación |
| `symbol` | TEXT | XAUUSD, EURUSD, etc. |
| `timeframe` | TEXT | 1D, 4H, 1H, 15M, 5M |
| `direction` | TEXT | BUY, SELL |
| `signal_type` | TEXT | FUERTE, MEDIA, ALERTA, MÁXIMA |
| `entry_price` | REAL | Precio entrada |
| `sell_limit` | REAL | Precio límite SELL (solo SELL) |
| `stop_loss` | REAL | Stop loss |
| `tp1, tp2, tp3` | REAL | Objetivos de beneficio |
| `rr_tp1` | REAL | Ratio riesgo:recompensa |
| `score` | INTEGER | Score total (0-24) |
| `estado` | TEXT | ACTIVA, CLOSED, CANCELADA |
| `profit_pct` | REAL | Beneficio % (si CLOSED) |

### Tabla: price_history

Almacena historial de precios para seguimiento.

| Campo | Tipo | Descripción |
|-------|------|-------------|
| `id` | INTEGER PK | ID único |
| `signal_id` | INTEGER FK | FK signals |
| `price` | REAL | Precio actual |
| `timestamp` | TEXT | Fecha/hora |

---

## Integración Telegram

### Canales (Tópicos del Foro)

| Tópico | ID | Recibe |
|--------|----|----|
| 📈 SWING | 304 | Señales 1D, 4H, 1H |
| 🔄 INTRADAY | 303 | Señales 1H |
| ⚡ SCALPING | 302 | Señales 15M, 5M |

### Estructura de Mensajes

```html
🔴 SELL FUERTE — GOLD (4H)
━━━━━━━━━━━━━━━━━━━━
💰 Precio:     $3,320.00
📌 SELL LIMIT: $3,325.00
🛑 Stop Loss:  $3,348.00
🎯 TP1: $3,272  R:R 2.0:1
🎯 TP2: $3,200  R:R 3.5:1
🎯 TP3: $3,100  R:R 5.8:1
━━━━━━━━━━━━━━━━━━━━
📊 Score: 14/21  📉 RSI: 67.2
⏱️ TF: 4H  📅 2026-04-18
━━━━━━━━━━━━━━━━━━━━
🔗 Confluencia Multi-TF (4/5):
  ✅ 1D  → BEARISH
  ✅ 4H  → BEARISH
  ✅ 1H  → BEARISH
  ❌ 15M → BULLISH
```

### Notificaciones de TP/SL

```
🎯 TP1 ALCANZADO — GOLD (4H)
━━━━━━━━━━━━━━━
Precio: $3,272 ✅
Profit: +2.0% (R:R 1.0:1)

💡 Acción recomendada:
   • Cerrar 33% + mover SL a breakeven
   • Dejar trailing stop en TP1
```

---

## Sincronización y Thread-Safety

### Locks y Sincronización

1. **_yf_lock** — Global lock para yfinance (previene rate-limiting)
2. **_singleton_lock** — Lock para DatabaseManager singleton
3. **bias_store_lock** — Lock para tf_bias (read/write seguro)

### Patrón Double-Check Locking

```python
if condition:
    with lock:
        if condition:
            # Do work
```

Previene condiciones de carrera en inicialización.

---

## Arquivos Clave

| Archivo | Líneas | Rol |
|---------|--------|-----|
| `app.py` | 500+ | Flask + orquestador threads |
| `signal_monitor.py` | 400+ | Monitor TP/SL |
| `db_manager.py` | 500+ | CRUD Turso |
| `tf_bias.py` | 130+ | Cascada multi-TF |
| `shared_indicators.py` | 400+ | Indicadores compartidos |
| `telegram_utils.py` | 50+ | Envío Telegram centralizado |

---

*Última actualización: 2026-04-18*
