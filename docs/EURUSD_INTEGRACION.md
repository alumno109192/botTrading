# Integración EUR/USD — Plan Completo

> **Estado:** Planificación  
> **Fecha:** Mayo 2026  
> **Objetivo:** Añadir el par EUR/USD al bot con detectores 5M, 15M, 1H y 4H, reutilizando toda la infraestructura existente.

---

## 1. Por qué EUR/USD y diferencias clave vs XAU/USD

| Característica | XAU/USD (Gold) | EUR/USD |
|----------------|----------------|---------|
| Precio típico | $4.500-4.800 | 1.0500-1.1500 |
| ATR diario | $30-80 | 70-100 pips (0.0070-0.0100) |
| Spread broker | 2-4 pips | 0.1-0.5 pips (ECN) |
| GAP risk | **Muy alto** (geopolítica, metales) | **Bajo** en días laborables |
| Sesión óptima | 08-21 UTC | **07-21 UTC** (Londres + NY) |
| Sensibilidad macro | Oro, DXY, guerras | NFP, CPI, BCE, Fed |
| Liquidez | Alta | **Máxima** (par más líquido del mundo) |
| Predictibilidad técnica | Media | **Alta** en sesión normal |

**Ventaja principal:** spreads mínimos y sin los GAPs brutales del oro.  
**Riesgo principal:** NFP y decisiones del BCE/Fed generan movimientos bruscos (gestionar con `economic_calendar.py` igual que el oro).

---

## 2. Archivos a modificar (existentes)

### 2.1 `adapters/data_provider.py`

Añadir EUR/USD a los mapeos de tickers:

```python
# En _TICKER_MAP_TWELVE (línea ~50)
_TICKER_MAP_TWELVE = {
    'GC=F':    'XAU/USD',
    'SI=F':    'XAG/USD',
    'EURUSD=X': 'EUR/USD',   # ← AÑADIR
}

# En _TICKER_MAP_POLYGON (si se usa como backup)
_TICKER_MAP_POLYGON = {
    'GC=F':    'C:XAUUSD',
    'SI=F':    'C:XAGUSD',
    'EURUSD=X': 'C:EURUSD',  # ← AÑADIR
}
```

> **Nota:** El ticker yfinance de EUR/USD es `EURUSD=X`. En Twelve Data se llama `EUR/USD`.

---

### 2.2 `services/ohlcv_poller.py`

Añadir los intervalos de EUR/USD a `POLL_TARGETS`:

```python
POLL_TARGETS = [
    # ── Gold (existente) ─────────────────────────────────────────
    {'ticker_yf': 'GC=F', 'interval': '1m',  'poll_secs':   60, 'max_dias_bd':   1},
    {'ticker_yf': 'GC=F', 'interval': '5m',  'poll_secs':   60, 'max_dias_bd':   8},
    {'ticker_yf': 'GC=F', 'interval': '4h',  'poll_secs': 1800, 'max_dias_bd':  95},
    {'ticker_yf': 'GC=F', 'interval': '1d',  'poll_secs': 3600, 'max_dias_bd': 400},

    # ── EUR/USD (AÑADIR) ─────────────────────────────────────────
    {'ticker_yf': 'EURUSD=X', 'interval': '5m',  'poll_secs':   60, 'max_dias_bd':   8},
    {'ticker_yf': 'EURUSD=X', 'interval': '4h',  'poll_secs': 1800, 'max_dias_bd':  95},
    {'ticker_yf': 'EURUSD=X', 'interval': '1d',  'poll_secs': 3600, 'max_dias_bd': 400},
]
```

> El detector 1H de EUR/USD, igual que el de Gold, hará resample de 5M→1H para mayor fiabilidad.

---

### 2.3 `services/orchestrator.py`

Añadir los detectores EUR/USD al `DETECTOR_REGISTRY`:

```python
DETECTOR_REGISTRY = {
    # ── Gold (existente) ─────────────────────────────────────────
    'gold_1d':  {'module': 'detectors.gold.detector_gold_1d',  'label': 'DETECTOR GOLD 1D',  'enabled': True},
    'gold_4h':  {'module': 'detectors.gold.detector_gold_4h',  'label': 'DETECTOR GOLD 4H',  'enabled': True},
    'gold_1h':  {'module': 'detectors.gold.detector_gold_1h',  'label': 'DETECTOR GOLD 1H',  'enabled': True},
    'gold_15m': {'module': 'detectors.gold.detector_gold_15m', 'label': 'DETECTOR GOLD 15M', 'enabled': True},
    'gold_5m':  {'module': 'detectors.gold.detector_gold_5m',  'label': 'DETECTOR GOLD 5M',  'enabled': True},

    # ── EUR/USD (AÑADIR) ─────────────────────────────────────────
    'eurusd_4h':  {'module': 'detectors.eurusd.detector_eurusd_4h',  'label': 'DETECTOR EURUSD 4H',  'enabled': True},
    'eurusd_1h':  {'module': 'detectors.eurusd.detector_eurusd_1h',  'label': 'DETECTOR EURUSD 1H',  'enabled': True},
    'eurusd_15m': {'module': 'detectors.eurusd.detector_eurusd_15m', 'label': 'DETECTOR EURUSD 15M', 'enabled': True},
    'eurusd_5m':  {'module': 'detectors.eurusd.detector_eurusd_5m',  'label': 'DETECTOR EURUSD 5M',  'enabled': True},
}
```

---

### 2.4 `.env` — Variables nuevas

```env
# ── Telegram Topics para EUR/USD ──────────────────────────────────
# Crear topics nuevos en el grupo de Telegram o reutilizar los existentes
THREAD_ID_EURUSD_SWING=XXX       # Para 4H (crear topic "EURUSD Swing")
THREAD_ID_EURUSD_INTRADAY=XXX    # Para 1H (crear topic "EURUSD Intraday")
THREAD_ID_EURUSD_SCALPING=XXX    # Para 15M + 5M (crear topic "EURUSD Scalping")
```

> Para obtener los IDs: enviar un mensaje al topic correspondiente y el bot los detecta automáticamente, o usar el script `scripts/_get_thread_ids.py`.

---

## 3. Archivos a crear (nuevos)

### Estructura de directorio

```
detectors/
└── eurusd/
    ├── __init__.py
    ├── detector_eurusd_5m.py
    ├── detector_eurusd_15m.py
    ├── detector_eurusd_1h.py
    └── detector_eurusd_4h.py
```

---

## 4. Calibración de parámetros EUR/USD

Los parámetros del oro **no son directamente trasladables** porque el ATR del EUR/USD es radicalmente distinto (~0.0008 vs ~$15). Todos los multiplicadores ATR están calibrados para eso.

### 4.1 Parámetros por timeframe

| Parámetro | 5M | 15M | 1H | 4H |
|-----------|----|----|----|----|
| `rsi_length` | 7 | 9 | 14 | 14 |
| `rsi_min_sell` | 65 | 62 | 58 | 55 |
| `rsi_max_buy` | 35 | 38 | 42 | 45 |
| `ema_fast_len` | 3 | 5 | 9 | 12 |
| `ema_slow_len` | 8 | 13 | 21 | 26 |
| `ema_trend_len` | 21 | 50 | 200 | 200 |
| `atr_length` | 7 | 10 | 14 | 14 |
| `atr_sl_mult` | 1.2 | 1.3 | 1.0 | 1.0 |
| `atr_tp1_mult` | 1.5 | 1.5 | 1.5 | 2.0 |
| `atr_tp2_mult` | 2.5 | 2.5 | 2.5 | 3.0 |
| `atr_tp3_mult` | 3.5 | 3.5 | 3.5 | 4.5 |
| `sr_lookback` | 80 | 150 | 120 | 100 |
| `sr_zone_mult` | 0.6 | 0.8 | 0.8 | 0.8 |
| `vol_mult` | 1.2 | 1.2 | 1.1 | 1.0 |
| `min_score` | 3 | 4 | 5 | 5 |
| `CHECK_INTERVAL` | 60s | 120s | 60s | 3600s |

> **Por qué SL más ajustado:** EUR/USD tiene spreads y slippage mínimos, el precio respeta mejor los niveles técnicos → SL más cercano = mejor R:R sin sacrificar seguridad.

### 4.2 Sesión óptima EUR/USD

```python
# En cada detector eurusd, cambiar el filtro de sesión:
# Gold:    06-22 UTC
# EURUSD:  07-21 UTC  (Londres 08:00-17:00 BST = 07:00-16:00 UTC)
#                     (Nueva York 09:30-16:00 EST = 14:30-21:00 UTC)
# Solapamiento óptimo: 13:00-17:00 UTC (máxima liquidez)
```

### 4.3 Diferencias en la lógica de señales

**Mantener igual que Gold:**
- Zonas S/R dinámicas (swing highs/lows)
- Anti-spam (48h TTL)
- Bloqueo por calendario económico (NFP, BCE, CPI)
- Confluencia multi-TF
- Scoring por RSI, ADX, envolventes, stop-hunt

**Ajustar para EUR/USD:**
- **Sin sesgo DXY separado:** EUR/USD ya incorpora DXY inversamente → el `dxy_bias.py` puede usarse directamente (DXY fuerte = sesgo SELL EUR/USD)
- **Sin COT de metales:** usar COT de EUR (código CFTC: 099741)
- **Pivots diarios:** misma lógica, escala en pips (~50-100 pips vs $50-100 gold)
- **Stop-hunt en EUR/USD:** muy común en aperturas de Londres y NY → patrón muy útil

---

## 5. Plantilla de detector (estructura base)

Cada detector EUR/USD sigue exactamente la misma estructura que los de Gold, heredando de `BaseDetector`. Solo cambian los parámetros y la lógica específica de scoring.

### `detectors/eurusd/__init__.py`
```python
# vacío
```

### `detectors/eurusd/detector_eurusd_5m.py` (esqueleto)

```python
"""
detectors/eurusd/detector_eurusd_5m.py — Detector micro-scalping EUR/USD 5M.

Hereda de BaseDetector. Misma arquitectura que detector_gold_5m pero calibrado
para la escala de pips del EUR/USD (~0.0001 por pip).

Sesión activa: 07:00-21:00 UTC (Londres + NY)
Intervalo de análisis: 60 segundos
"""

import os
import time
import logging
import pandas as pd
from dotenv import load_dotenv

from core.base_detector import BaseDetector
from adapters.data_provider import obtener_ohlcv

load_dotenv()
logger = logging.getLogger('bottrading.eurusd_5m')

TELEGRAM_THREAD_ID = int(os.getenv('THREAD_ID_EURUSD_SCALPING', '0') or 0)

PARAMS = {
    'EURUSD': {
        'ticker_yf':      'EURUSD=X',
        'rsi_length':     7,
        'rsi_min_sell':   65.0,
        'rsi_max_buy':    35.0,
        'ema_fast_len':   3,
        'ema_slow_len':   8,
        'ema_trend_len':  21,
        'atr_length':     7,
        'atr_sl_mult':    1.2,
        'atr_tp1_mult':   1.5,
        'atr_tp2_mult':   2.5,
        'atr_tp3_mult':   3.5,
        'sr_lookback':    80,
        'sr_zone_mult':   0.6,
        'vol_mult':       1.2,
        'min_score_scalping': 3,
    }
}

CHECK_INTERVAL = 60   # segundos entre análisis
SIMBOLO        = 'EURUSD'
TF_LABEL       = '5M'
INTERVAL       = '5m'


class DetectorEURUSD5M(BaseDetector):

    def __init__(self):
        super().__init__(
            simbolo=SIMBOLO,
            tf_label=TF_LABEL,
            params=PARAMS[SIMBOLO],
            telegram_thread_id=TELEGRAM_THREAD_ID,
        )

    def analizar(self):
        p   = self.params
        df  = obtener_ohlcv(p['ticker_yf'], INTERVAL)
        if df is None or len(df) < 50:
            return

        self._current_candle_ts = df.index[-1]
        df  = self.calcular_indicadores(df, p)
        atr = df['ATR'].iloc[-1]
        close = df['Close'].iloc[-1]

        # Zonas S/R
        zrl, zrh, zsl, zsh = self.calcular_zonas_sr(df, atr, p['sr_lookback'], p['sr_zone_mult'])

        # Niveles SL/TP
        sl_v, sl_c, tp1_v, tp2_v, tp3_v, tp1_c, tp2_c, tp3_c = self.calcular_niveles(
            df, atr, p, sell_limit=close, buy_limit=close
        )

        rsi = df['RSI'].iloc[-1]
        adx = df['ADX'].iloc[-1]

        score_sell = 0
        score_buy  = 0

        # ── RSI extremo ──────────────────────────────────────────────────
        if pd.notna(rsi) and rsi > p['rsi_min_sell']:
            score_sell += 1
        if pd.notna(rsi) and rsi < p['rsi_max_buy']:
            score_buy += 1

        # ── Zona S/R ─────────────────────────────────────────────────────
        tol = atr * 0.4
        en_resist = zrl - tol <= df['High'].iloc[-1] <= zrh + tol
        en_sop    = zsl - tol <= df['Low'].iloc[-1]  <= zsh + tol
        if en_resist:
            score_sell += 2
        if en_sop:
            score_buy += 2

        # ── Vela envolvente ───────────────────────────────────────────────
        if len(df) >= 2:
            c0, c1 = df.iloc[-1], df.iloc[-2]
            if c0['Close'] < c0['Open'] and c0['Open'] > c1['Close'] and c0['Close'] < c1['Open']:
                score_sell += 1
            if c0['Close'] > c0['Open'] and c0['Open'] < c1['Close'] and c0['Close'] > c1['Open']:
                score_buy += 1

        # ── ADX: tendencia fuerte ─────────────────────────────────────────
        if pd.notna(adx) and adx > 25:
            di_plus  = df['DI_plus'].iloc[-1]
            di_minus = df['DI_minus'].iloc[-1]
            if di_minus > di_plus:
                score_sell += 1
            else:
                score_buy += 1

        # ── Filtro de volumen ─────────────────────────────────────────────
        vol     = df['Volume'].iloc[-1]
        vol_avg = df['Volume'].rolling(20).mean().iloc[-1]
        score_sell, score_buy, _ = self.ajustar_scores_por_volumen(
            score_sell, score_buy, vol, vol_avg, p['vol_mult']
        )

        min_s = p['min_score_scalping']

        if score_sell >= min_s and not self.ya_enviada(f'SELL_{df.index[-1]}'):
            clave = f'SELL_{df.index[-1]}'
            senal = {
                'simbolo':    f'{SIMBOLO}_{TF_LABEL}',
                'asset':      'EURUSD',
                'timeframe':  TF_LABEL,
                'direccion':  'SELL',
                'entry':      close,
                'sl':         sl_v,
                'tp1':        tp1_v,
                'tp2':        tp2_v,
                'tp3':        tp3_v,
                'score':      score_sell,
                'atr_entry':  atr,
                'rsi_entry':  rsi,
                'adx_entry':  adx,
            }
            senal_id = self._guardar_senal(senal)
            msg = (
                f"🔴 <b>SELL EUR/USD 5M</b>\n"
                f"Entry: <b>{close:.5f}</b>  SL: {sl_v:.5f}\n"
                f"TP1: {tp1_v:.5f}  TP2: {tp2_v:.5f}  TP3: {tp3_v:.5f}\n"
                f"Score: {score_sell} | RSI: {rsi:.1f} | ATR: {atr:.5f}"
            )
            self.enviar(msg)
            self.marcar_enviada(clave)

        if score_buy >= min_s and not self.ya_enviada(f'BUY_{df.index[-1]}'):
            clave = f'BUY_{df.index[-1]}'
            senal = {
                'simbolo':    f'{SIMBOLO}_{TF_LABEL}',
                'asset':      'EURUSD',
                'timeframe':  TF_LABEL,
                'direccion':  'BUY',
                'entry':      close,
                'sl':         sl_c,
                'tp1':        tp1_c,
                'tp2':        tp2_c,
                'tp3':        tp3_c,
                'score':      score_buy,
                'atr_entry':  atr,
                'rsi_entry':  rsi,
                'adx_entry':  adx,
            }
            senal_id = self._guardar_senal(senal)
            msg = (
                f"🟢 <b>BUY EUR/USD 5M</b>\n"
                f"Entry: <b>{close:.5f}</b>  SL: {sl_c:.5f}\n"
                f"TP1: {tp1_c:.5f}  TP2: {tp2_c:.5f}  TP3: {tp3_c:.5f}\n"
                f"Score: {score_buy} | RSI: {rsi:.1f} | ATR: {atr:.5f}"
            )
            self.enviar(msg)
            self.marcar_enviada(clave)


def run():
    """Entry point para el orchestrator."""
    detector = DetectorEURUSD5M()
    logger.info("▶ Detector EURUSD 5M iniciado")
    while True:
        try:
            if detector.en_sesion_optima():
                detector.analizar()
        except Exception as e:
            logger.error(f"Error en detector EURUSD 5M: {e}", exc_info=True)
        time.sleep(CHECK_INTERVAL)
```

> Los detectores 15M, 1H y 4H siguen exactamente la misma estructura con los parámetros de la tabla del punto 4.1. El detector 1H añade análisis de Bollinger, MACD y Fibonacci igual que `detector_gold_1h.py`.

---

## 6. Diferencias de formato en mensajes Telegram

Para EUR/USD los precios tienen **5 decimales** (ej. `1.08542`) en lugar de 2 (gold). Solo hay que asegurarse de formatear con `:.5f` en los mensajes de Telegram y en los scripts de diagnóstico.

```python
# Gold (2 decimales)
f"Entry: {close:.2f}"   → "Entry: 4562.30"

# EUR/USD (5 decimales)  
f"Entry: {close:.5f}"   → "Entry: 1.08542"
```

---

## 7. MT5Broker — adaptar para EUR/USD

El adaptador `adapters/mt5_broker.py` ya soporta EUR/USD. Solo cambiar en `.env`:

```env
MT5_SYMBOL=EURUSD         # En lugar de XAUUSD
MT5_RISK_PCT=1.0
MT5_MAX_LOTES=1.00         # EUR/USD tiene lotes más pequeños (0.01 micro = 1000 EUR)
MT5_TIMEFRAMES_ACTIVOS=5m,15m
```

> **Diferencia de sizing:** en EUR/USD 1 lote estándar = 100.000 EUR. El pip value para EURUSD con cuenta en USD es ~$10/pip por lote estándar (fijo, no varía con el precio). La función `calcular_lotes()` ya maneja esto correctamente mediante `trade_tick_value` de MT5.

---

## 8. Checklist de implementación

### Fase 1 — Infraestructura de datos
- [ ] Añadir `'EURUSD=X': 'EUR/USD'` en `adapters/data_provider.py` → `_TICKER_MAP_TWELVE`
- [ ] Añadir targets EUR/USD en `services/ohlcv_poller.py` → `POLL_TARGETS`
- [ ] Reiniciar bot y verificar que el poller descarga velas de EUR/USD en la BD
- [ ] Verificar con: `python scripts/_check_ohlcv.py` (o query directa a BD)

### Fase 2 — Detectores
- [ ] Crear directorio `detectors/eurusd/`
- [ ] Crear `detectors/eurusd/__init__.py` (vacío)
- [ ] Crear `detectors/eurusd/detector_eurusd_5m.py` (plantilla de sección 5)
- [ ] Crear `detectors/eurusd/detector_eurusd_15m.py` (params ajustados)
- [ ] Crear `detectors/eurusd/detector_eurusd_1h.py` (con MACD + Bollinger)
- [ ] Crear `detectors/eurusd/detector_eurusd_4h.py` (swing)
- [ ] Añadir detectores al `DETECTOR_REGISTRY` en `services/orchestrator.py`

### Fase 3 — Telegram y entorno
- [ ] Crear topics en el grupo de Telegram para EUR/USD
- [ ] Obtener los thread IDs nuevos
- [ ] Añadir `THREAD_ID_EURUSD_SWING`, `THREAD_ID_EURUSD_INTRADAY`, `THREAD_ID_EURUSD_SCALPING` al `.env`

### Fase 4 — Pruebas
- [ ] Ejecutar detector 5M en modo standalone para verificar señales
- [ ] Comparar niveles con TradingView manualmente (5-10 señales)
- [ ] Ajustar `min_score` si hay demasiado o poco ruido
- [ ] Activar todos los detectores con bot completo

### Fase 5 — MT5 Demo (opcional, tras Fase 4)
- [ ] Cambiar `MT5_SYMBOL=EURUSD` en `.env`
- [ ] Verificar sizing de lotes con `broker.calcular_lotes(1.0854, 1.0830)`
- [ ] Activar `MT5_AUTO_TRADE=true` solo en demo

---

## 9. Consumo de API keys

EUR/USD añade 3 intervalos más al poller (`5m`, `4h`, `1d`). Impacto estimado sobre el Plan Grow 55:

| Intervalo | Calls/día adicionales |
|-----------|----------------------|
| 5M (cada 60s) | ~960 |
| 4H (cada 1800s) | ~32 |
| 1D (cada 3600s) | ~24 |
| **Total adicional** | **~1.016 calls/día** |

El Plan Grow 55 tiene peticiones ilimitadas → sin impacto en cuota. Las keys de backup (Basic 8) también absorben la carga adicional.

---

*Documento creado: 4 de mayo de 2026*
