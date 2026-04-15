# PENDIENTES — Issues y Mejoras del Bot
> Creado: 15 abril 2026 | Precio de referencia: ~$4.833 XAUUSD
> Última actualización: 15 abril 2026 (implementación gaps críticos) | Foco: XAUUSD

---

## ✅ COMPLETADO — 15 abril 2026

| # | Issue | Commit |
|---|---|---|
| ~~Zonas 1D/4H/1H (bot parado — precio entre zonas)~~ | ✅ `282d845` |
| ~~Bug anti-spam BUY 4H buscaba "VENTA" en lugar de "COMPRA"~~ | ✅ `282d845` |
| ~~TPs 4H compra actualizados (4860/4960/5200)~~ | ✅ `282d845` |
| ~~.gitignore: logfile.txt, .claude/, _get_thread_ids.py~~ | ✅ `282d845` |
| ~~Mejoras sesiones anteriores: retry backoff, OBV vectorizado, session filter 1H~~ | ✅ `282d845` |
| ~~P1: TP1 compra 1D encima de resistencia~~ | ✅ `b17aef9` |
| ~~P2: IndentationError en bloques SELL/BUY de 1D/4H/1H (else: vacío)~~ | ✅ `b17aef9` |
| ~~P4: Score mínimo 1H subido de 4 a 5~~ | ✅ `b17aef9` |
| ~~P5: TPs ATR en 1D (×3.0/×5.0/×8.0) y 4H (×2.0/×3.5/×5.5)~~ | ✅ `b17aef9` |
| ~~P8: Score alerta 1D subido de 4 a 6 (ruta técnica) y de 2 a 3 (ruta sentimiento)~~ | ✅ `6e330cb` |
| ~~P9: `perdidas_consecutivas` 15M ahora se consulta desde BD (era código muerto)~~ | ✅ `6e330cb` |
| ~~P10: Filtro horario 4H (06:00-22:00 UTC) — evita señales en sesión asiática de baja liquidez~~ | ✅ `6e330cb` |

---

## 📅 PRÓXIMA SEMANA — Mejoras críticas a aplicar

### ~~🔴 P1 — TP1 compra en 1D queda por encima de la resistencia~~ ✅ COMPLETADO b17aef9

**Archivo:** `detectors/gold/detector_gold_1d.py`

Situación actual tras el fix de hoy:
- `zona_resist_high: 4980` — el precio no debería superar esta zona
- `tp1_compra: 5000` — está **20 puntos por encima de la resistencia**

Un trade BUY que entra desde soporte (~$4756) tiene que atravesar la resistencia (4900-4980) para llegar a TP1. El bot calculará un R:R aparentemente bueno pero la señal casi nunca llegará a TP1 en la realidad porque la resistencia la frena.

**Corrección:**
```python
# ACTUAL (mal):
'tp1_compra': 5000.0   # por encima de zona_resist_high=4980

# CORRECTO (TP1 justo bajo resistencia):
'tp1_compra': 4850.0   # alcanzable antes de tocar resistencia
'tp2_compra': 5050.0   # ya implica ruptura de resistencia
'tp3_compra': 5200.0   # objetivo swing amplio
```

---

### ~~🔴 P2 — Anti-spam 1D/4H/1H usa dict en memoria~~ ✅ COMPLETADO b17aef9

> También corregido en este commit: `IndentationError` en los bloques de señal de los tres detectores — el `else:` vacío que impedía que el bot arrancara.

**Detectors afectados:** `detector_gold_1d.py`, `detector_gold_4h.py`, `detector_gold_1h.py`

Los tres detectores de swing/intraday usan:
```python
alertas_enviadas = {}  # GLOBAL — se vacía en cada restart
```

Los detectores 15M y 5M ya están migrados a `db.existe_senal_activa_tf()` que persiste en BD. Render (free tier) reinicia el servicio en cada deploy y periódicamente — cada restart vacía el dict y puede generar señales duplicadas.

**Corrección:** En los bloques de envío de señal de 1D, 4H y 1H, reemplazar `ya_enviada(tipo)` por una consulta a BD similar a cómo lo hacen 15M/5M:
```python
# ACTUAL (volátil):
if not ya_enviada("SELL_MAX"):
    ...

# CORRECTO (persistente):
if db and db.existe_senal_activa_tf(simbolo_db):
    print(f"  ℹ️  SELL 1D bloqueada: ya existe señal activa")
    return
```
`existe_senal_activa_tf()` ya está implementada en `db_manager.py`.

---

### 🔴 P3 — Las zonas requieren revisión semanal (Gold es muy volátil)

Gold se movió de ~$3.300 a ~$4.833 en pocos meses. Las zonas configuradas hoy (15 abril) pueden estar completamente equivocadas la próxima semana si el precio se mueve $150+.

**Protocolo a establecer:**
- Cada lunes antes de que abra el mercado europeo (07:00 UTC), revisar:
  1. ¿El precio actual sigue siendo exterior a las zonas resist/soporte?
  2. Si el precio está a menos de `tolerancia` de una zona → revisar todo
  3. Si el precio ya superó `zona_resist_high` → subir todas las zonas
  4. Si el precio cayó por debajo de `zona_soporte_low` → bajar todas las zonas

**Referencia de zonas actuales (15-abr-2026, precio ~$4833):**

| TF | resist_low | resist_high | soporte_low | soporte_high |
|---|---|---|---|---|
| 1D | 4900 | 4980 | 4650 | 4760 |
| 4H | 4870 | 4940 | 4700 | 4780 |
| 1H | 4880 | 4920 | 4750 | 4790 |
| 15M | 4880 | 4910 | 4760 | 4790 |
| 5M | 4850 | 4870 | 4800 | 4815 |

---

### ~~🟡 P4 — Score mínimo ALERTA en 1H es 4 (debería ser al menos 5)~~ ✅ COMPLETADO b17aef9

El detector 1H usa `senal_sell_alerta = score_sell >= 4` mientras 4H ya usa `>= 5`. Un score de 4 sobre ~21 posibles (~19%) genera demasiadas señales de baja calidad en el timeframe más activo.

**Corrección en `detector_gold_1h.py`:**
```python
# ACTUAL:
senal_sell_alerta = score_sell >= 4
senal_buy_alerta  = score_buy  >= 4

# CORRECTO (alinear con 4H):
senal_sell_alerta = score_sell >= 5
senal_buy_alerta  = score_buy  >= 5
```

---

### ~~🟡 P5 — TPs fijos en 1D y 4H → migrar a ATR para mantenimiento cero~~ ✅ COMPLETADO b17aef9

El problema de zonas/TPs desactualizados que paró el bot durante semanas se repite si los TPs son fijos. El detector 1H ya usa `atr_tp1_mult / atr_tp2_mult / atr_tp3_mult` y se adapta automáticamente.

**Multiplicadores sugeridos:**
- 1D: `atr_tp1_mult: 3.0`, `atr_tp2_mult: 5.0`, `atr_tp3_mult: 8.0` (velas diarias tienen ATR ~$50-80)
- 4H: `atr_tp1_mult: 2.0`, `atr_tp2_mult: 3.5`, `atr_tp3_mult: 5.5` (ATR 4H ~$30-50)

**Impacto:** Elimina la necesidad de actualizar TPs manualmente cuando el precio cambia de rango.

---

### 🟡 P6 — ~~Limpiar archivos huérfanos del repositorio~~ ✅ COMPLETADO

**Verificado:** Los archivos `detector_gold_copy.py` (raíz y en detectors/gold/) **no existen** en el repositorio actual.
El `.gitignore` ya tenía todos los items necesarios: `CONTEXTO_PROYECTO.md`, `PLAN_MEJORA_Y_APP.md`, `.claude/`, `_get_thread_ids.py`, `logfile.txt`.

**Estado: sin acción requerida — P6 resuelto.**

---

### 🟡 P7 — ~~Verificar Thread IDs de Telegram~~ ✅ Script creado

**Nuevo archivo:** `test_thread_ids.py`

Ejecutar para enviar un mensaje de prueba a cada thread configurado:
```bash
.\venv\Scripts\python.exe test_thread_ids.py
```
El script envía un mensaje identificado a cada thread (SWING, SCALPING, INTRADAY).
Si el mensaje aparece en el tópico equivocado → actualizar el `.env` correspondiente.

**Acción pendiente del usuario:** ejecutar el script y confirmar visualmente en Telegram.

---

## 🟡 ALTO — Anti-spam `alertas_enviadas` se pierde en cada restart

> **→ Ver P2 arriba** (ahora en sección Próxima Semana como 🔴 P2)

---

## 🟡 ALTO — yfinance tiene 15 min de delay en datos intraday (plan free)

Los detectores 15M y 5M descargan datos con `yfinance` (plan gratuito), que introduce un retraso de **15 minutos** en datos intraday.

- `detector_gold_15m.py`: `period='5d', interval='15m'`
- `detector_gold_5m.py`: `period='2d', interval='5m'`

**Consecuencia:** Las señales scalping siempre llegarán con al menos 15 min de retraso respecto al precio real. Para scalping esto es crítico — el nivel de entrada podría ya no ser válido.

**Opciones:**
1. Pagar plan Yahoo Finance API ($280/año aprox.)
2. Migrar a otra fuente: `ccxt` (Binance para synthetics), `MetaTrader 5 API`, `Polygon.io` (plan básico ~$29/mes con datos en tiempo real)
3. Aceptar la limitación y usar 15M/5M solo como indicadores tendenciales, no como señales de entrada exactas

---

## 🟡 ALTO — Scores de 4/21 generan señales de ruido en 1H
> Ver también P4 en sección Próxima Semana

El umbral mínimo para señal "ALERTA" es `score >= 4` sobre un máximo de 21 condiciones. Esto equivale a que solo 2-3 indicadores confirmen la dirección — no es suficiente evidencia técnica.

**Observación:**
- `score_sell >= 4` → SELL ALERTA
- `score_sell >= 8` → SELL MEDIA
- `score_sell >= 12` → SELL FUERTE
- `score_sell >= 16` → SELL MÁXIMA

Con 21 condiciones, 4/21 (~19%) como mínimo produce muchas señales de baja calidad, especialmente en mercados laterales.

**Candidato a ajuste:** Subir mínimo de ALERTA a 6 (pero solo si hay datos históricos que lo validen — ver punto de backtesting).

---

## 🟡 MEDIO — Parámetros definidos sin backtesting

Todos los parámetros actuales (zonas, TPs, R:R, pesos de indicadores, umbrales de score) fueron definidos manualmente sin validación histórica:

- R:R mínimo 1.2 — fue elegido arbitrariamente, no se conoce el win rate real
- Pesos de indicadores (+1 por condición) no ponderan la fiabilidad real de cada indicador
- Zonas S/R fijadas manualmente en código (no calculadas dinámicamente)

**Sin backtesting es imposible saber si el sistema es rentable.**

**Opción mínima viable:** Exportar últimas N señales de la BD con sus TPs y analizar cuántas llegaron a TP1/TP2 vs cuántas tocaron SL primero.

---

## 🟡 MEDIO — TPs dinámicos (ATR) solo en 1H, no en 4H/1D
> Ver también P5 en sección Próxima Semana

El detector 1H ya usa TPs basados en ATR:
```python
'atr_tp1_mult': 1.5
'atr_tp2_mult': 2.5
'atr_tp3_mult': 4.0
```
Esto es superior a TPs fijos porque se adaptan automáticamente al rango de volatilidad actual.

Los detectores 1D y 4H usan TPs fijos que hay que actualizar manualmente cada vez que el precio se mueve significativamente. Con Gold pasando de $3.300 a $4.833 en pocos meses, este mantenimiento manual ya causó que el bot dejara de funcionar.

**Recomendación:** Migrar 1D y 4H a TPs basados en ATR con multiplicadores apropiados para cada timeframe.

---

## 🟡 MEDIO — Thread IDs de Telegram pendientes de confirmar

El archivo `.env.example` tiene:
```
THREAD_ID_SWING=304
THREAD_ID_INTRADAY=???
THREAD_ID_SCALPING=535  # Este sí está confirmado
```

No se ha confirmado si `THREAD_ID_SWING=304` es el valor real en producción. Las señales 1D/4H/1H van al thread 304. Si es incorrecto, las señales llegan al chat equivocado.

**Acción:** Verificar en Telegram que el thread_id 304 corresponde al tópico "Swing" del grupo.

---

## 🟢 BAJO — Archivos huérfanos / temporales en el repositorio
> Ver también P6 en sección Próxima Semana

| Archivo | Estado | Acción sugerida |
|---|---|---|
| `detectors/gold/detector_gold_copy.py` | Copia antigua sin uso claro | Eliminar o documentar propósito |
| `detector_gold_copy.py` (raíz) | Idem | Eliminar |
| `CONTEXTO_PROYECTO.md` | No trackeado por git | Decidir: commit o .gitignore |
| `PLAN_MEJORA_Y_APP.md` | No trackeado por git | Decidir: commit o .gitignore |
| `.claude/` | Directorio Claude-web | Añadir a .gitignore |
| `_get_thread_ids.py` | Script temporal | Eliminar |
| `logfile.txt` | Log de ejecución | Añadir a .gitignore |

---

## ✅ COMPLETADO — Cambios locales commiteados (282d845)

~~Commit pendiente tras revert del 15/04~~

---

## Resumen de Prioridades

| # | Issue | Impacto | Estado |
|---|---|---|---|
| ~~P1~~ | ~~TP1 compra 1D encima de resistencia~~ | ✅ `b17aef9` |
| ~~P2~~ | ~~Anti-spam persistente vía BD (1D/4H/1H) + IndentationError~~ | ✅ `b17aef9` |
| P3 | Protocolo revisión semanal de zonas | 🔴 Bot puede pararse de nuevo | Pendiente |
| ~~P4~~ | ~~Score mínimo 1H: 4→5~~ | ✅ `b17aef9` |
| ~~P5~~ | ~~TPs ATR en 4H/1D~~ | ✅ `b17aef9` |
| ~~P6~~ | ~~Limpiar archivos huérfanos~~ | ✅ Archivos no existen / .gitignore ok |
| ~~P7~~ | ~~Script test Thread IDs~~ | ✅ `test_thread_ids.py` creado |
| P8 (SPX) | Filtro horario SPX (14:30-21:00 UTC) | 🔴 Señales en mercado cerrado | ⏸️ Aplazado — foco XAUUSD |
| P9 (BTC) | R:R mínimo Bitcoin ≥2.0 | 🔴 Acepta setups negativos | ⏸️ Aplazado — foco XAUUSD |
| ~~P10~~ | ~~yfinance delay 15M/5M~~ | ✅ `data_provider.py` (Polygon.io + fallback) |
| ~~P11~~ | ~~Backtesting básico~~ | ✅ `backtest_signals.py` creado |

---

---

# 🔍 ANÁLISIS TÉCNICO — Gaps para XAUUSD perfecto
> Añadido: 15 abril 2026 | Basado en análisis completo del código

---

## ~~🔴 CRÍTICO — Fuente de datos en tiempo real para 15M/5M~~ ✅ IMPLEMENTADO

**Nuevo archivo:** `data_provider.py`

Prioridad de fuentes (automática, sin cambiar código):
1. **Twelve Data** (gratuito, 800 req/día) → añadir `TWELVE_DATA_API_KEY` al `.env`
2. **Polygon.io** (de pago, ~$29/mes) → añadir `POLYGON_API_KEY` al `.env`
3. **yfinance fallback** (15 min delay, siempre disponible)

**Activación gratuita — Twelve Data:**
```bash
# 1. Registrarse en https://twelvedata.com/apikey (plan Free, sin tarjeta)
# 2. Añadir al .env:
TWELVE_DATA_API_KEY=tu_clave_aqui
```
800 req/día gratuitas cubre perfectamente los detectores 5M y 15M (~600 req/día en sesión).

**Detectors modificados:** `detector_gold_15m.py`, `detector_gold_5m.py`

---

## ~~🔴 CRÍTICO — Correlación con DXY (USD Index) — Ausente en todos los detectores~~ ✅ IMPLEMENTADO

**Nuevo archivo:** `dxy_bias.py`

Descarga `DX-Y.NYB` (disponible en yfinance sin delay) en 1H, calcula sesgo EMA9/EMA21.
Resultado cacheado 30 minutos. Ajuste automático de scores:
- DXY BULLISH → `score_buy -= 2` / `score_sell += 1`
- DXY BEARISH → `score_buy += 1` / `score_sell -= 2`
- DXY NEUTRAL  → sin cambio

**Detectors modificados:** todos los gold (1D, 4H, 1H, 15M, 5M)

---

## ~~🔴 CRÍTICO — Filtro de Calendario Económico — Ausente~~ ✅ IMPLEMENTADO

**Nuevo archivo:** `economic_calendar.py`

Contiene lista hardcoded de eventos USD de alto impacto (FOMC, NFP, CPI, PIB, PIB, Powell…)
con cobertura hasta junio 2026. Función `hay_evento_impacto(ventana_minutos)` integrada en
todos los detectores gold.

- **1D / 4H / 1H**: ventana ±60 minutos
- **15M**: ventana ±45 minutos
- **5M**: ventana ±30 minutos

**Mantenimiento requerido:** revisar y actualizar `EVENTOS_ALTO_IMPACTO` el primer lunes de cada mes.
Referencia: https://www.forexfactory.com/calendar (filtrar: USD, Impact=High)

**Detectors modificados:** todos los gold (1D, 4H, 1H, 15M, 5M)

---

## 🟡 ALTO — VWAP — Ausente en detectores intraday (1H, 15M, 5M)

VWAP (Volume Weighted Average Price) es el indicador de referencia para intraday en todos los mercados institucionales. Distingue si el precio está caro o barato respecto al volumen del día.

- **Precio > VWAP** → mercado en zona cara → favorecer SELL intraday
- **Precio < VWAP** → mercado en zona barata → favorecer BUY intraday
- **Precio en VWAP** → indecisión → reducir confianza de señal

**Está en 0 de 5 detectores actualmente.**

```python
def calcular_vwap(df):
    """VWAP diario — se reinicia cada sesión"""
    df = df.copy()
    df['fecha'] = df.index.date
    df['tp'] = (df['High'] + df['Low'] + df['Close']) / 3
    df['tp_vol'] = df['tp'] * df['Volume']
    df['cum_tp_vol'] = df.groupby('fecha')['tp_vol'].cumsum()
    df['cum_vol']    = df.groupby('fecha')['Volume'].cumsum()
    df['vwap'] = df['cum_tp_vol'] / df['cum_vol']
    return df['vwap']
```

**Impacto en score:**
- SELL: precio > VWAP → `+1`
- BUY:  precio < VWAP → `+1`

---

## 🟡 ALTO — Market Regime Detection — ADX presente pero sin usar para clasificar

ADX ya está en los detectores pero solo se usa para confirmar la fuerza de tendencia. No se clasifica el régimen de mercado, lo cual es crítico porque:

- **Mercado trending** (ADX > 25): señales de momentum funcionan → MACD cross + EMA breakout
- **Mercado ranging** (ADX < 20): señales de reversión funcionan → zonas S/R + RSI extremos

El bot aplica la misma lógica en ambos regímenes, generando señales de baja calidad en mercados laterales.

**Implementación:**
```python
# En cada detector, después de calcular ADX:
if adx_actual > 25:
    regimen = "TRENDING"
    # Priorizar: MACD, EMA alignment, momentum
    # Penalizar: señales contra-tendencia en zonas
elif adx_actual < 20:
    regimen = "RANGING"
    # Priorizar: zona S/R, RSI extremos, patrones vela
    # Penalizar: señales de ruptura (breakout)
else:
    regimen = "TRANSICION"
```

---

## 🟡 ALTO — Pivot Points Diarios/Semanales — Ausentes

R1, R2, S1, S2 son niveles usados por traders institucionales como zonas de referencia. Una señal near R1 diario tiene mucho más peso que sin esa confluencia.

**Cálculo (pivot clásico):**
```python
def calcular_pivots(high_prev, low_prev, close_prev):
    pp = (high_prev + low_prev + close_prev) / 3
    r1 = 2 * pp - low_prev
    r2 = pp + (high_prev - low_prev)
    s1 = 2 * pp - high_prev
    s2 = pp - (high_prev - low_prev)
    return {'PP': pp, 'R1': r1, 'R2': r2, 'S1': s1, 'S2': s2}
```

**Uso en señales:** Si el precio está cerca de S1/S2 al generar señal BUY → `score_buy += 1`. Si cerca de R1/R2 al generar SELL → `score_sell += 1`.

---

## 🟡 ALTO — Market Structure (HH/HL/LH/LL) — Ausente

El bot usa EMAs para tendencia pero no analiza estructura de mercado real:
- **HH + HL** (Higher Highs + Higher Lows) = uptrend → solo BUY
- **LH + LL** (Lower Highs + Lower Lows) = downtrend → solo SELL
- **Quiebre de estructura (BOS)** = reversión potencial

En gold que hace movimientos impulsivos grandes seguidos de consolidación, el BOS es una señal de alta fiabilidad.

**Impacto:** Permitiría bloquear señales contra la estructura aunque las EMAs aún no hayan girado.

---

## 🟡 MEDIO — Sizing de Posición — No incluido en mensajes Telegram

El mensaje de señal incluye precio entrada, SL y TPs pero **no sugiere tamaño de posición**. Para un trader con cuenta de $10.000 y SL de $120 en 1 lote, el riesgo es del 1.2%. Para uno con $50.000 el riesgo sería del 0.24%.

**Adición sugerida al mensaje:**
```
💰 Gestión (riesgo 1%):
  Cuenta $10k → 0.08 lotes ($8 riesgo/pip)
  Cuenta $25k → 0.21 lotes
  Cuenta $50k → 0.42 lotes
```

Implementación: calcular lotes para distintos tamaños de cuenta asumiendo riesgo del 1% y el SL emitido.

---

## 🟡 MEDIO — Trailing Stop en signal_monitor.py — Ausente

`signal_monitor.py` rastrea TP1/TP2/TP3 fijos. Cuando gold hace un movimiento limpio de $200+ el bot no aprovecha la extensión.

**Propuesta:**
- Tras alcanzar TP1 → SL se mueve a breakeven (ya recomendado en mensaje)
- Tras alcanzar TP2 → activar trailing stop de 1×ATR del TF origen
- El trailing sigue el precio hasta cierre definitivo

**Archivos afectados:** `signal_monitor.py`, `db_manager.py` (nueva columna `trailing_active`)

---

## 🟡 MEDIO — Stochastic RSI — Ausente en todos los TFs

RSI tiene lag en oro. Stochastic RSI (más sensible) detecta puntos de inflexión más rápido, especialmente en intraday:

```python
from ta.momentum import StochRSIIndicator

stoch_rsi = StochRSIIndicator(close=df['Close'], window=14, smooth1=3, smooth2=3)
df['stochrsi_k'] = stoch_rsi.stochrsi_k()
df['stochrsi_d'] = stoch_rsi.stochrsi_d()

# Señal: K cruza D desde abajo en zona oversold (<0.2) → BUY
# Señal: K cruza D desde arriba en zona overbought (>0.8) → SELL
```

**Candidato a añadir en:** 1H, 15M, 5M (donde el lag de RSI impacta más).

---

## 🟡 MEDIO — Reporte de Performance Semanal — Ausente

La BD acumula señales pero nunca se genera un reporte automático. Sin métricas objetivas, es imposible saber si el sistema es rentable o ajustar parámetros.

**Reporte mínimo (enviar a Telegram cada lunes 07:00 UTC):**
```
📊 Semana 15-19 abr | XAUUSD
Señales emitidas: 12
├─ BUY: 7  |  SELL: 5
Resultados:
├─ TP1 alcanzado: 6 (50%) 
├─ TP2 alcanzado: 3 (25%)
├─ TP3 alcanzado: 1 (8%)
├─ SL tocado: 4 (33%)
├─ Activas: 2
PnL estimado (1 lote): +$340
Win rate TP1+: 67%
```

**Archivos afectados:** `db_manager.py` (nuevo método `get_stats_semana()`), `signal_monitor.py` o nuevo `weekly_report.py`

---

## 🟢 BAJO — Niveles Fibonacci — Ausentes

Los retrocesos 38.2%, 50%, 61.8% son zonas de entrada clásicas en gold. Un BUY en zona soporte **coincidente con Fibonacci 61.8%** del último swing tiene probabilidad de éxito superior.

**Cálculo automático:**
```python
def calcular_fibonacci(swing_high, swing_low, direccion="RETROCESO"):
    rango = swing_high - swing_low
    niveles = {
        '23.6%': swing_high - 0.236 * rango,
        '38.2%': swing_high - 0.382 * rango,
        '50.0%': swing_high - 0.500 * rango,
        '61.8%': swing_high - 0.618 * rango,
        '78.6%': swing_high - 0.786 * rango,
    }
    return niveles
```

**Uso:** Detectar si el precio actual está near (±0.5 ATR) de un nivel Fibonacci → `score += 1`.

---

## ~~🟢 BAJO — Backtesting básico sobre señales históricas~~ ✅ IMPLEMENTADO

**Nuevo archivo:** `backtest_signals.py`

Lee la BD (Turso) y calcula métricas de rendimiento:

```bash
# Todas las señales
python backtest_signals.py

# Filtrar por TF
python backtest_signals.py --simbolo XAUUSD_1H

# Exportar CSV para análisis externo
python backtest_signals.py --export resultados.csv
```

**Métricas generadas:**
- Win rate TP1/TP2/TP3 y loss rate global
- Desglose por símbolo/TF, por dirección (COMPRA/VENTA)
- Tabla de optimización de score mínimo (cuál threshold maximiza win rate)
- Señales activas en curso

---

## Resumen de Gaps por Prioridad

| Prioridad | Gap | Dificultad de implementación | Estado |
|---|---|---|---|
| ~~🔴 1~~ | ~~Datos tiempo real 15M/5M (Polygon.io)~~ | Alta | ✅ `data_provider.py` — activar con `POLYGON_API_KEY` |
| ~~🔴 2~~ | ~~Filtro calendario económico~~ | Media | ✅ `economic_calendar.py` integrado en 5 detectores |
| ~~🔴 3~~ | ~~Correlación DXY~~ | Baja | ✅ `dxy_bias.py` integrado en 5 detectores |
| 🟡 4 | VWAP en detectores intraday | Baja | Pendiente |
| 🟡 5 | Market regime detection con ADX | Baja — ya está ADX en código | Pendiente |
| 🟡 6 | Pivot Points diarios | Baja | Pendiente |
| 🟡 7 | Reporte performance semanal | Media | Pendiente |
| 🟡 8 | Trailing stop en monitor | Media | Pendiente |
| 🟡 9 | Stochastic RSI en 1H/15M/5M | Baja | Pendiente |
| 🟡 10 | Sizing posición en mensajes | Baja | Pendiente |
| 🟢 11 | Market structure HH/HL/LH/LL | Alta | Pendiente |
| 🟢 12 | Niveles Fibonacci | Media | Pendiente |
| ~~🟢 13~~ | ~~Backtesting sobre BD histórica~~ | Media | ✅ `backtest_signals.py` |
