# 📊 Indicadores Técnicos

Guía completa de indicadores implementados, fórmulas, patrones de velas y cómo se utilizan en la detección de señales.

---

## 📋 Indicadores Implementados

### 1. **RSI — Relative Strength Index**

**Período:** 14, 28, 56 (escalado por timeframe)

**Fórmula:**
```
RSI = 100 - (100 / (1 + RS))
RS = Ganancia promedio / Pérdida promedio
```

**Implementación:** Wilder's Smoothing (EWM en pandas)

**Umbrales por Activo:**

| Activo | Venta (≥) | Compra (≤) |
|--------|-----------|-----------|
| XAUUSD | 70 | 30 |
| EURUSD | 70 | 30 |
| BTCUSD | 70 | 30 |

**Señales Generadas:**

| Condición | Señal | Puntos |
|-----------|-------|--------|
| RSI ≥ 70 | Sobrecompra → VENTA | +2 |
| RSI ≤ 30 | Sobreventa → COMPRA | +2 |
| Divergencia bajista | RSI sube, precio baja | +1 |
| Divergencia alcista | RSI baja, precio sube | +1 |

**Ejemplo:**
```
Precio: $3,320 (máximo histórico)
RSI:    65 (debajo de 70, pero en zona alta)
→ Posible reversión → monitorear divergencia
```

---

### 2. **EMA — Exponential Moving Average**

**Períodos:** Fast (9/18/36), Slow (21/42/84), Trend (200/400)

**Fórmula:**
```
EMA_hoy = Precio_hoy × multiplicador + EMA_ayer × (1 - multiplicador)
multiplicador = 2 / (periodo + 1)
```

**Señales Generadas:**

| Condición | Señal | Puntos |
|-----------|-------|--------|
| EMA_fast > EMA_slow | Alcista | +1-2 |
| EMA_fast < EMA_slow | Bajista | +1-2 |
| Cruce reciente | Cambio de tendencia | +2 |
| Precio > EMA_trend | Tendencia alcista | +1 |
| Precio < EMA_trend | Tendencia bajista | +1 |

**Uso en Arquitectura:**
- Confirmación de dirección
- Filtro de tendencia a largo plazo
- Base para MACD

---

### 3. **ATR — Average True Range**

**Período:** 14, 28 (escalado)

**Fórmula:**
```
TR = MAX(High - Low, |High - Close_ayer|, |Low - Close_ayer|)
ATR = SMA(TR, 14)  [en realidad Wilder's EWM]
```

**Uso Principal:**
- Cálculo de Stop Loss: `SL = Entrada ± ATR × multiplicador`
- Multiplicadores por activo:
  - XAUUSD: 1.5× (1D), 1.2× (4H), 1.5× (15M/5M)
  - EURUSD: 1.5× (1D), 1.2× (4H), 1.5× (15M)
  - BTCUSD: 2.5× (1D), 2.0× (4H)

**Ejemplo:**
```
Precio entrada: $3,320
ATR: $45
SL (1.2×): $3,320 + (45 × 1.2) = $3,374
```

**No suma puntos directamente, pero es crítico para riesgo/recompensa.**

---

### 4. **Bollinger Bands**

**Parámetros:** 20 períodos, 2 desviaciones estándar

**Fórmula:**
```
BB_mid = SMA(close, 20)
BB_std = StdDev(close, 20)
BB_upper = BB_mid + (BB_std × 2)
BB_lower = BB_mid - (BB_std × 2)
BB_width = (BB_upper - BB_lower) / BB_mid  [normalizado]
```

**Señales Generadas:**

| Condición | Señal | Puntos |
|-----------|-------|--------|
| Precio toca BB_upper | Sobrecompra → VENTA | +2 |
| Precio toca BB_lower | Sobreventa → COMPRA | +2 |
| BB_width < 5% | Squeeze detectado | 0 (alerta) |
| Rebote de BB | Reversión zona extrema | +2 |

**Ejemplo:**
```
Precio: $3,340 (toca band superior)
RSI: 72 (sobrecompra)
→ Señal VENTA (confluencia)
```

---

### 5. **MACD — Moving Average Convergence Divergence**

**Parámetros:** 12/26/9 (escalado a 24/52/18 en 4H)

**Fórmula:**
```
MACD = EMA12 - EMA26
Signal = EMA9(MACD)
Histogram = MACD - Signal
```

**Señales Generadas:**

| Condición | Señal | Puntos |
|-----------|-------|--------|
| MACD cruza Signal (alcista) | Compra confirmada | +2 |
| MACD cruza Signal (bajista) | Venta confirmada | +2 |
| MACD > 0 | Momentum alcista | +1 |
| MACD < 0 | Momentum bajista | +1 |
| MACD divergencia bajista | Precio sube, MACD baja | +1 |
| MACD divergencia alcista | Precio baja, MACD sube | +1 |

**Ejemplo:**
```
MACD line: 0.002
Signal line: -0.001
Histogram: 0.003
→ Cruce alcista reciente → COMPRA (+2 puntos)
```

---

### 6. **ADX — Average Directional Index**

**Período:** 14, 28

**Fórmula:**
```
+DI = SMA(+DM, 14) / ATR
-DI = SMA(-DM, 14) / ATR
ADX = SMA(|+DI - -DI| / (+DI + -DI), 14)
```

**Interpretación:**
- ADX < 20: Mercado lateral (débil)
- ADX 20-40: Tendencia moderada
- ADX > 40: Tendencia muy fuerte

**Señales Generadas:**

| Condición | Señal | Puntos |
|-----------|-------|--------|
| ADX > 25 + DI alineado | Tendencia fuerte | +2 |
| ADX < 20 | Mercado lateral | **-3** (penaliza) |
| +DI > -DI | Tendencia alcista | +1 |
| -DI > +DI | Tendencia bajista | +1 |

**Uso:** Previene falsas señales en laterales.

**Ejemplo:**
```
ADX: 35
+DI: 28, -DI: 15
→ Tendencia alcista muy fuerte → suma +2
```

---

### 7. **OBV — On-Balance Volume**

**Fórmula:**
```
Si close > close_anterior:    OBV += volumen
Si close < close_anterior:    OBV -= volumen
Si close == close_anterior:   OBV sin cambio
```

**También se calcula:** OBV_EMA (EMA 20 del OBV)

**Señales Generadas:**

| Condición | Señal | Puntos |
|-----------|-------|--------|
| OBV divergencia bajista | Precio sube, OBV baja | +1 |
| OBV divergencia alcista | Precio baja, OBV sube | +1 |
| OBV > OBV_EMA | Volumen creciente (alcista) | +1 |
| OBV < OBV_EMA | Volumen decreciente (bajista) | +1 |

**Confirmación por volumen** — Previene "bombeos" sin volumen real.

---

### 8. **Patrones de Velas**

#### Evening Star (Patrón Bajista)

**Estructura:**
```
Vela 1: Alcista grande (cuerpo > 60% del rango)
Vela 2: Pequeña indecisa (cuerpo < 30%) con gap al alza
Vela 3: Bajista grande que cierra dentro de vela 1

Ejemplo:
[Vela Grande Alcista] [Pequeña Indecisa] [Vela Grande Bajista]
       ↑                    ↑                    ↓
```

**Señal:** VENTA reversión bajista (+2 puntos)

**Tasa de éxito:** ~72% (según análisis técnico)

---

#### Morning Star (Patrón Alcista)

**Estructura:**
```
Vela 1: Bajista grande (cuerpo > 60%)
Vela 2: Pequeña indecisa (cuerpo < 30%) con gap a la baja
Vela 3: Alcista grande que cierra dentro de vela 1

Ejemplo:
[Vela Grande Bajista] [Pequeña Indecisa] [Vela Grande Alcista]
       ↓                    ↓                    ↑
```

**Señal:** COMPRA reversión alcista (+2 puntos)

**Tasa de éxito:** ~70%

---

#### Envolvente Alcista (Bullish Engulfing)

**Estructura:**
```
Vela 1: Bajista pequeña
Vela 2: Alcista que envuelve completamente a vela 1
        (low2 < low1 Y high2 > high1)
```

**Señal:** COMPRA en zona de soporte (+2 puntos)

---

#### Envolvente Bajista (Bearish Engulfing)

**Estructura:**
```
Vela 1: Alcista pequeña
Vela 2: Bajista que envuelve completamente a vela 1
        (high2 > high1 Y low2 < low1)
```

**Señal:** VENTA en zona de resistencia (+2 puntos)

---

#### Doji

**Estructura:**
```
open ≈ close (diferencia < 1% del rango)
long upper/lower shadows (at least 2× el cuerpo)

Ejemplo:
    ═════════ (sombra superior)
       ▮  (cuerpo pequeño)
    ═════════ (sombra inferior)
```

**Señal:** Indecisión en zona clave (neutral, alerta)

---

## 📈 Sistema de Scoring Completo

### Tabla de Puntuación

| Factor | Condición | Puntos | Uso |
|--------|-----------|--------|-----|
| **Zona S/R** | Precio en zona soporte/resistencia | +2 | Confluencia |
| **Patrón Vela** | Evening/Morning Star | +2 | Reversión |
| **Envolvente** | Alcista/Bajista en zona | +2 | Reversión |
| **Volumen** | Vol > vol_avg × 1.5 | +2 | Confirmación |
| **RSI Extremo** | RSI ≥70 o ≤30 | +1-2 | Sobrecompra/venta |
| **RSI Divergencia** | RSI/precio divergencia | +1 | Reversión |
| **EMA Cruce** | Cruce rápida/lenta reciente | +1-2 | Giro tendencia |
| **Bollinger Upper** | Toca banda superior | +2 | Sobrecompra |
| **Bollinger Lower** | Toca banda inferior | +2 | Sobreventa |
| **MACD Cruce** | Cruce línea de señal | +2 | Confirmación |
| **MACD Divergencia** | Divergencia MACD/precio | +1 | Reversión |
| **ADX Fuerte** | ADX > 25 | +2 | Tendencia confirmada |
| **OBV Divergencia** | Divergencia OBV/precio | +1 | Confirmación |
| **OBV Creciente** | OBV > OBV_EMA | +1 | Momentum |
| **Penalización ADX** | ADX < 20 (lateral) | **-3** | Evita falsas en laterales |

**Score Máximo:** ~24 puntos

### Niveles de Señal

```
Score 4-5    👀 ALERTA
             Observar, posible oportunidad
             Envío: SÍ (informativo)

Score 6-8    ⚠️ MEDIA
             Probabilidad moderada
             Envío: SÍ (estándar)

Score 9-11   🔴🟢 FUERTE
             Alta probabilidad
             Envío: SÍ (prioritaria)

Score 12+    ⚡ MÁXIMA
             Confluencia múltiple muy fuerte
             Envío: SÍ (urgente)
```

---

## 🔬 Parámetros Ajustados por Timeframe

| Parámetro | 1D | 4H | 1H | 15M | 5M |
|-----------|-----|-----|-----|-----|-----|
| RSI | 14 | 28 | 56 | 9 | 9 |
| EMA Fast | 9 | 18 | 36 | 5 | 5 |
| EMA Slow | 21 | 42 | 84 | 13 | 13 |
| EMA Trend | 200 | 400 | — | 50 | 50 |
| ATR | 14 | 28 | 28 | 10 | 10 |
| Bollinger | 20 | 40 | 80 | — | — |
| MACD | 12/26/9 | 24/52/18 | — | — | — |
| ADX | 14 | 28 | — | 14 | 14 |
| Datos | 2 años | 60 días | 30 días | 5 días | 2 días |

---

## 🎯 Ejemplo Completo de Scoring

**Escenario:** XAUUSD 4H, Precio $3,320

```
ANÁLISIS:
─────────
✅ Zona resistencia           → +2
✅ Evening Star patrón        → +2
✅ Volumen alto (2.5× media)  → +2
✅ RSI = 72 (sobrecompra)      → +2
✅ MACD cruce bajista          → +2
✅ ADX = 35 (tendencia fuerte) → +2
✅ OBV > OBV_EMA (volumen sube)→ +1
❌ Penalización ADX lateral    → 0 (ADX > 20)
─────────────────────────────────
📊 TOTAL SCORE: 13/15 puntos

CLASIFICACIÓN: ⚡ MÁXIMA CONFLUENCIA

SEÑAL: 🔴 SELL FUERTE
       Precio: $3,320
       SL: $3,374 (ATR × 1.2)
       TP1: $3,272 (R:R 2.0:1)
```

---

## 🧮 Implementación en shared_indicators.py

Todos los indicadores están centralizados en `shared_indicators.py`:

```python
from shared_indicators import (
    calcular_rsi,
    calcular_ema,
    calcular_atr,
    calcular_bollinger_bands,
    calcular_macd,
    calcular_adx,
    calcular_obv,
    patron_evening_star,
    patron_morning_star,
    patron_envolvente_alcista,
    patron_envolvente_bajista,
    patron_doji,
)
```

**Uso típico:**
```python
rsi = calcular_rsi(df, periodo=14)
ema_fast, ema_slow = calcular_ema(df, 9, 21)
atr = calcular_atr(df, periodo=14)
bb_upper, bb_mid, bb_lower, bb_width = calcular_bollinger_bands(df, 20, 2)
```

---

## 📚 Referencias

| Indicador | Autor | Libro |
|-----------|-------|-------|
| RSI | J. Welles Wilder | New Concepts in Technical Trading Systems |
| EMA | Exponencial | Standard TA |
| ATR | J. Welles Wilder | New Concepts in Technical Trading Systems |
| Bollinger | John Bollinger | Bollinger on Bollinger Bands |
| MACD | Gerald Appel | Technical Analysis: Power Tools for Active Investors |
| ADX | J. Welles Wilder | New Concepts in Technical Trading Systems |
| OBV | Joseph Granville | On Balance Volume |
| Patrones | Steve Nison | Japanese Candlestick Charting Techniques |

---

*Última actualización: 2026-04-18*
