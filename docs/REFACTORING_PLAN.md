# 🏗️ Plan de Refactorización — Clean Architecture

> **Autor:** @arquitecto-infra  
> **Fecha:** 2026-04-18  
> **Riesgo en producción:** 🟡 MEDIO (mitigable con fases atómicas)  
> **Comando de arranque actual:** `python app.py` (Render Web Service, puerto 5000)

---

## 1. ESTADO ACTUAL — Diagnóstico

### Problemas Estructurales

```
BotTrading/                    ← 26 archivos .py en la raíz (FLAT)
├── app.py                     ← 540 líneas: Flask + Orchestrator + Keep-alive + Routes
├── detector_bitcoin.py        ← LEGACY: duplicado de detectors/bitcoin/
├── detector_gold.py           ← LEGACY: duplicado de detectors/gold/
├── detector_spx.py            ← LEGACY
├── detector_spx_copy.py       ← LEGACY
├── shared_indicators.py       ← DOMINIO (trading puro) mezclado con infra
├── telegram_utils.py          ← ADAPTER (I/O externo) al mismo nivel
├── db_manager.py              ← ADAPTER (BD) al mismo nivel
├── data_provider.py           ← ADAPTER (yfinance/TwelveData/Polygon) al mismo nivel
├── signal_monitor.py          ← SERVICIO mezclado con root
├── yf_lock.py                 ← INFRA mezclada con root
├── test_*.py (5 archivos)     ← Tests sueltos
├── _get_thread_ids.py         ← Script auxiliar
├── run_scalping_15m.py        ← Script auxiliar
├── backtest_signals.py        ← Script auxiliar
└── detectors/                 ← ÚNICA carpeta organizada (30+ módulos)
    └── {asset}/{tf}.py        ← sys.path.insert(0, ...) en CADA archivo
```

### Los 7 Problemas Concretos

| # | Problema | Impacto |
|---|---------|---------|
| **P1** | **Flat root** — 26 .py sin paquete | No hay namespace, cualquier `import X` es ambiguo |
| **P2** | **sys.path.insert hack** — En CADA detector (~30 archivos) | Frágil, rompe si se mueve un archivo |
| **P3** | **app.py = Dios** — Flask + threads + keep-alive + routes (540 líneas) | Imposible testear Flask sin arrancar detectores |
| **P4** | **Trading ↔ I/O acoplados** — `shared_indicators.py` (dominio puro) vive junto a `telegram_utils.py` (I/O) | No se puede cambiar de exchange/broker sin tocar todo |
| **P5** | **Archivos legacy** — `detector_gold.py`, `detector_bitcoin.py`, `detector_spx.py`, `detector_spx_copy.py` en root | Confusión: ¿cuál se usa? (respuesta: ninguno, son copias viejas) |
| **P6** | **Sin capa de abstracción de datos** — `data_provider.py` mezcla 3 APIs (TwelveData + Polygon + yfinance) con lógica de cache | Imposible mockear para testing, tight coupling |
| **P7** | **Inicialización de detectores = código comentado (150+ líneas)** — Detectores pausados como bloques de comentarios | Debería ser configuración, no código comentado |

### Mapa de Dependencias Actual

```
                     ┌──────────────────────────────┐
                     │         app.py (GOD)         │
                     │  Flask + Threads + Routes     │
                     │  + Keep-alive + Logging       │
                     └──┬──────┬──────┬──────┬──────┘
                        │      │      │      │
              ┌─────────┘  ┌───┘  ┌───┘      │
              ▼            ▼      ▼           ▼
        detectors/*   signal_  gold_news_  yf_lock
              │       monitor  monitor
              │          │
              ▼          ▼
    ┌─────────────────────────────┐
    │  IMPORTS DIRECTOS (root):   │
    │  · shared_indicators.py     │  ← Dominio
    │  · telegram_utils.py        │  ← Adapter I/O
    │  · db_manager.py            │  ← Adapter BD
    │  · data_provider.py         │  ← Adapter Data
    │  · tf_bias.py               │  ← Servicio
    │  · dxy_bias.py              │  ← Servicio
    │  · economic_calendar.py     │  ← Servicio
    │  · yf_lock.py               │  ← Infra
    └─────────────────────────────┘
```

**El problema central:** TODO depende de TODO al mismo nivel. No hay capas.

---

## 2. ESTRUCTURA OBJETIVO — Clean Architecture

```
BotTrading/
│
├── app.py                          ← ENTRY POINT (slim: solo arranca Flask + llama a orchestrator)
│
├── core/                           ← DOMINIO PURO (0 dependencias externas)
│   ├── __init__.py
│   ├── indicators.py               ← shared_indicators.py (RSI, ATR, ADX, BB, MACD, OBV, patrones)
│   ├── scoring.py                  ← Lógica de scoring extraída (hoy está dentro de cada detector)
│   └── models.py                   ← Dataclasses: Signal, Bias, ScoreResult (opcional, fase futura)
│
├── services/                       ← LÓGICA DE APLICACIÓN (orquesta dominio + adapters)
│   ├── __init__.py
│   ├── tf_bias.py                  ← Cascada multi-timeframe
│   ├── dxy_bias.py                 ← Sesgo DXY (inversamente correlacionado con Gold)
│   ├── economic_calendar.py        ← Filtro de eventos macro
│   ├── signal_monitor.py           ← Monitor de TP/SL (loop)
│   ├── news_monitor.py             ← gold_news_monitor.py
│   └── orchestrator.py             ← Lógica de iniciar/registrar/monitorear threads
│
├── adapters/                       ← I/O EXTERNO (todo lo que habla con el mundo)
│   ├── __init__.py
│   ├── telegram.py                 ← telegram_utils.py (enviar mensajes)
│   ├── database.py                 ← db_manager.py (Turso CRUD)
│   ├── data_provider.py            ← data_provider.py (TwelveData/Polygon/yfinance)
│   └── yf_lock.py                  ← Lock compartido para yfinance
│
├── api/                            ← CAPA WEB (solo rutas Flask)
│   ├── __init__.py
│   └── routes.py                   ← Las 4 rutas Flask (/health, /status, /cron, /)
│
├── detectors/                      ← SE MANTIENE (ya está bien organizado)
│   ├── __init__.py
│   ├── config.py                   ← NUEVO: Registro de detectores activos (reemplaza código comentado)
│   ├── gold/
│   │   ├── detector_gold_1d.py
│   │   ├── detector_gold_4h.py
│   │   ├── detector_gold_1h.py
│   │   ├── detector_gold_15m.py
│   │   └── detector_gold_5m.py
│   ├── bitcoin/
│   ├── spx/
│   ├── eurusd/
│   ├── silver/
│   ├── wti/
│   └── nasdaq/
│
├── scripts/                        ← HERRAMIENTAS MANUALES
│   ├── limpiar_duplicados.py
│   ├── stats_dashboard.py
│   ├── backtest_signals.py
│   ├── run_detectors.py
│   └── run_scalping_15m.py
│
├── tests/                          ← TESTS ORGANIZADOS
│   ├── test_db_simple.py
│   ├── test_detector_4h.py
│   ├── test_system.py
│   ├── test_telegram.py
│   └── test_thread_ids.py
│
├── docs/                           ← YA ORGANIZADO ✅
├── .github/                        ← YA ORGANIZADO ✅
├── requirements.txt
├── .env.example
└── .gitignore
```

### Diagrama de Dependencias (Post-Refactor)

```
                        app.py (entry point)
                           │
                    ┌──────┴──────┐
                    ▼             ▼
               api/routes    services/orchestrator
                    │             │
                    │      ┌──────┼──────────────────────┐
                    │      ▼      ▼           ▼          ▼
                    │  services/  services/  services/  detectors/*
                    │  tf_bias   dxy_bias   signal_     │
                    │                       monitor     │
                    │                                   │
                    └───────────┬───────────────────────┘
                                │
                    ┌───────────┼───────────┐
                    ▼           ▼           ▼
              core/         adapters/    adapters/
              indicators    telegram     database
              scoring       data_prov    yf_lock
              models

REGLA: Las flechas solo van HACIA ABAJO. core/ NO importa de adapters/.
```

---

## 3. EL PLAN — 5 Fases Atómicas

Cada fase es **un commit independiente** que deja el sistema funcional. Si algo falla en producción, se revierte **solo esa fase**.

---

### FASE 1 — Crear estructura de directorios (0 riesgo)

**Acción:** Crear directorios vacíos con `__init__.py`. No se mueve ni modifica nada.

```bash
mkdir core services adapters api scripts tests
touch core/__init__.py services/__init__.py adapters/__init__.py api/__init__.py
```

**Archivos afectados:** 0 archivos existentes modificados  
**Riesgo:** 🟢 CERO  
**Rollback:** `rm -rf core services adapters api scripts tests`

---

### FASE 2 — Mover archivos + crear re-exports de compatibilidad (riesgo bajo)

**Estrategia clave:** Al mover cada archivo, dejar un **STUB de compatibilidad** en la ubicación original. Esto garantiza que todos los `import` existentes siguen funcionando.

#### 2A. Core (dominio puro)

| Origen (root) | Destino | Stub en root |
|---------------|---------|------------|
| `shared_indicators.py` | `core/indicators.py` | `from core.indicators import *` |

#### 2B. Adapters (I/O externo)

| Origen (root) | Destino | Stub en root |
|---------------|---------|------------|
| `telegram_utils.py` | `adapters/telegram.py` | `from adapters.telegram import *` |
| `db_manager.py` | `adapters/database.py` | `from adapters.database import *` |
| `data_provider.py` | `adapters/data_provider.py` | `from adapters.data_provider import *` |
| `yf_lock.py` | `adapters/yf_lock.py` | `from adapters.yf_lock import *` |

#### 2C. Services (lógica de aplicación)

| Origen (root) | Destino | Stub en root |
|---------------|---------|------------|
| `tf_bias.py` | `services/tf_bias.py` | `from services.tf_bias import *` |
| `dxy_bias.py` | `services/dxy_bias.py` | `from services.dxy_bias import *` |
| `economic_calendar.py` | `services/economic_calendar.py` | `from services.economic_calendar import *` |
| `signal_monitor.py` | `services/signal_monitor.py` | `from services.signal_monitor import *` |
| `gold_news_monitor.py` | `services/news_monitor.py` | `from services.news_monitor import *` |

#### 2D. Scripts y Tests

| Origen (root) | Destino |
|---------------|---------|
| `limpiar_duplicados.py` | `scripts/limpiar_duplicados.py` |
| `stats_dashboard.py` | `scripts/stats_dashboard.py` |
| `backtest_signals.py` | `scripts/backtest_signals.py` |
| `run_detectors.py` | `scripts/run_detectors.py` |
| `run_scalping_15m.py` | `scripts/run_scalping_15m.py` |
| `_get_thread_ids.py` | `scripts/_get_thread_ids.py` |
| `test_*.py` (5 archivos) | `tests/test_*.py` |

#### 2E. Eliminar archivos legacy

| Archivo | Razón |
|---------|-------|
| `detector_bitcoin.py` | Duplicado de `detectors/bitcoin/detector_bitcoin.py` |
| `detector_gold.py` | Duplicado de `detectors/gold/detector_gold.py` |
| `detector_spx.py` | Duplicado de `detectors/spx/detector_spx_1d.py` |
| `detector_spx_copy.py` | Copia antigua de SPX |

**Ejemplo de STUB de compatibilidad:**

```python
# shared_indicators.py (STUB — mantiene compatibilidad con imports existentes)
# La implementación real está en core/indicators.py
from core.indicators import *  # noqa: F401,F403
```

**Archivos afectados:** ~15 archivos movidos, ~10 stubs creados  
**Riesgo:** 🟡 BAJO (los stubs garantizan compatibilidad)  
**Rollback:** Revertir el commit (los stubs son el safety net)

---

### FASE 3 — Actualizar imports internos de los módulos movidos

Los módulos dentro de `core/`, `services/`, `adapters/` ahora deben importarse entre sí con rutas nuevas.

#### Cambios concretos por archivo:

**`services/signal_monitor.py`** (antes en root):
```python
# ANTES
from db_manager import DatabaseManager
from yf_lock import _yf_lock
from telegram_utils import enviar_telegram

# DESPUÉS
from adapters.database import DatabaseManager
from adapters.yf_lock import _yf_lock
from adapters.telegram import enviar_telegram
```

**`services/dxy_bias.py`**:
```python
# ANTES
from yf_lock import _yf_lock

# DESPUÉS
from adapters.yf_lock import _yf_lock
```

**`adapters/data_provider.py`**:
```python
# Sin cambios internos — solo usa stdlib + yfinance + requests
```

**`services/news_monitor.py`**:
```python
# Sin cambios internos — solo usa stdlib + requests + dotenv
```

**Archivos a modificar (imports internos):**

| Archivo movido | Imports a actualizar |
|---------------|---------------------|
| `services/signal_monitor.py` | `db_manager` → `adapters.database`, `yf_lock` → `adapters.yf_lock`, `telegram_utils` → `adapters.telegram` |
| `services/dxy_bias.py` | `yf_lock` → `adapters.yf_lock` |
| `scripts/stats_dashboard.py` | `db_manager` → `adapters.database` |
| `scripts/limpiar_duplicados.py` | `db_manager` → `adapters.database` |
| `scripts/run_detectors.py` | Todos los imports de detectors (ya usan `detectors.*`) |

**Riesgo:** 🟡 BAJO (los stubs en root siguen funcionando como fallback)  
**Rollback:** Revertir commit

---

### FASE 4 — Actualizar imports de los 30+ detectores (el más delicado)

**Estrategia:** Mientras los STUBS existan en root, los detectores siguen funcionando. Esta fase actualiza los imports para que apunten a la nueva estructura, y ELIMINA el hack `sys.path.insert(0, ...)`.

#### Patrón actual en TODOS los detectores:

```python
# LÍNEAS 1-3 de cada detector (30+ archivos)
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

# Imports desde root
from data_provider import get_ohlcv
from telegram_utils import enviar_telegram
from db_manager import DatabaseManager
from shared_indicators import calcular_rsi, calcular_ema, ...
import tf_bias
from dxy_bias import get_dxy_bias, ajustar_score_por_dxy
from economic_calendar import hay_evento_impacto
```

#### Patrón objetivo:

```python
# Sin sys.path hack — imports directos desde paquetes
from core.indicators import calcular_rsi, calcular_ema, calcular_atr, ...
from adapters.telegram import enviar_telegram
from adapters.database import DatabaseManager
from adapters.data_provider import get_ohlcv
from services import tf_bias
from services.dxy_bias import get_dxy_bias, ajustar_score_por_dxy
from services.economic_calendar import hay_evento_impacto
```

#### Lista completa de archivos a actualizar:

**Gold (5 archivos):**
- `detectors/gold/detector_gold_1d.py`
- `detectors/gold/detector_gold_4h.py`
- `detectors/gold/detector_gold_1h.py`
- `detectors/gold/detector_gold_15m.py`
- `detectors/gold/detector_gold_5m.py`

**Bitcoin (3 archivos):**
- `detectors/bitcoin/detector_bitcoin.py`
- `detectors/bitcoin/detector_bitcoin_1d.py`
- `detectors/bitcoin/detector_bitcoin_4h.py`

**SPX (4 archivos):**
- `detectors/spx/detector_spx_1d.py`
- `detectors/spx/detector_spx_4h.py`
- `detectors/spx/detector_spx_15m.py`
- `detectors/spx/detector_spx_copy.py`

**EURUSD (3 archivos):**
- `detectors/eurusd/detector_eurusd_1d.py`
- `detectors/eurusd/detector_eurusd_4h.py`
- `detectors/eurusd/detector_eurusd_15m.py`

**Silver (2 archivos):**
- `detectors/silver/detector_silver_1d.py`
- `detectors/silver/detector_silver_4h.py`

**WTI (2 archivos):**
- `detectors/wti/detector_wti_1d.py`
- `detectors/wti/detector_wti_4h.py`

**NASDAQ (2 archivos):**
- `detectors/nasdaq/detector_nasdaq_1d.py`
- `detectors/nasdaq/detector_nasdaq_4h.py`

**Total: ~21 archivos** (patrón de cambio idéntico en todos)

**Riesgo:** 🟡 MEDIO — Es el cambio más masivo. Los stubs siguen existiendo como safety net.  
**Rollback:** Revertir commit. Los stubs siguen funcionando.

---

### FASE 5 — Refactorizar app.py + eliminar stubs + cleanup

#### 5A. Extraer rutas Flask a `api/routes.py`

```python
# api/routes.py
from flask import Flask, jsonify, request
import os

def create_app(estado_sistema, threads_detectores):
    """Factory pattern para crear la app Flask."""
    app = Flask(__name__)
    CRON_TOKEN = os.environ.get('CRON_TOKEN', '')
    
    @app.route('/health')
    def health():
        return jsonify({'status': 'healthy'}), 200
    
    @app.route('/status')
    def status():
        token = request.headers.get('X-Cron-Token', '')
        if not CRON_TOKEN or token != CRON_TOKEN:
            return jsonify({'error': 'Unauthorized'}), 401
        return jsonify(estado_sistema)
    
    # ... /cron, / ...
    return app
```

#### 5B. Extraer orquestador a `services/orchestrator.py`

```python
# services/orchestrator.py
import threading
import time

DETECTOR_REGISTRY = {
    # Activos
    'gold_1d':  {'module': 'detectors.gold.detector_gold_1d',  'enabled': True},
    'gold_4h':  {'module': 'detectors.gold.detector_gold_4h',  'enabled': True},
    'gold_1h':  {'module': 'detectors.gold.detector_gold_1h',  'enabled': True},
    'gold_15m': {'module': 'detectors.gold.detector_gold_15m', 'enabled': True},
    'gold_5m':  {'module': 'detectors.gold.detector_gold_5m',  'enabled': True},
    # Pausados (activar cambiando enabled: True)
    'btc_1d':   {'module': 'detectors.bitcoin.detector_bitcoin_1d', 'enabled': False},
    'btc_4h':   {'module': 'detectors.bitcoin.detector_bitcoin_4h', 'enabled': False},
    'eur_1d':   {'module': 'detectors.eurusd.detector_eurusd_1d',   'enabled': False},
    # ...
}

def iniciar_detectores(registry=DETECTOR_REGISTRY):
    """Inicia solo los detectores con enabled=True."""
    threads = {}
    for name, config in registry.items():
        if not config['enabled']:
            continue
        module = importlib.import_module(config['module'])
        t = threading.Thread(target=module.main, name=name, daemon=True)
        t.start()
        threads[name] = t
        time.sleep(2)
    return threads
```

#### 5C. app.py reducido (~30 líneas)

```python
# app.py — Entry Point
from api.routes import create_app
from services.orchestrator import iniciar_detectores
import adapters.yf_lock  # Aplica parche yfinance

estado = {'iniciado': datetime.now().isoformat(), 'detectores': {}}
threads = {}

if __name__ == '__main__':
    threads = iniciar_detectores()
    estado['detectores'] = {k: 'activo' for k in threads}
    app = create_app(estado, threads)
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
```

#### 5D. Eliminar stubs de compatibilidad

Una vez que TODOS los imports apuntan a las nuevas rutas, eliminar los stubs:

```bash
# Eliminar stubs de compatibilidad en root
rm shared_indicators.py telegram_utils.py db_manager.py data_provider.py
rm yf_lock.py tf_bias.py dxy_bias.py economic_calendar.py
rm signal_monitor.py gold_news_monitor.py

# Eliminar legacy
rm detector_bitcoin.py detector_gold.py detector_spx.py detector_spx_copy.py

# Eliminar scripts/tests movidos
rm limpiar_duplicados.py stats_dashboard.py backtest_signals.py
rm run_detectors.py run_scalping_15m.py _get_thread_ids.py
rm test_*.py
```

#### 5E. Eliminar logfile.txt del root

```bash
echo "logfile.txt" >> .gitignore  # si no está ya
rm logfile.txt
```

**Riesgo:** 🟠 MEDIO-ALTO — Eliminar stubs rompe rollback. Hacer SOLO cuando fase 4 esté verificada en producción 24h.  
**Rollback:** Restaurar stubs desde git (`git checkout HEAD~1 -- shared_indicators.py ...`)

---

## 4. RESULTADO FINAL

### Root limpio (7 archivos vs 26 actuales)

```
BotTrading/
├── app.py                  ← ~30 líneas (entry point)
├── requirements.txt
├── .env.example
├── .gitignore
├── core/                   ← Dominio puro (indicators, scoring)
├── services/               ← Lógica de app (bias, calendar, monitor, orchestrator)
├── adapters/               ← I/O externo (telegram, database, data_provider)
├── api/                    ← Flask routes
├── detectors/              ← Detectores por activo/timeframe
├── scripts/                ← Herramientas manuales
├── tests/                  ← Tests
└── docs/                   ← Documentación
```

### Métricas de mejora

| Métrica | Antes | Después |
|---------|-------|---------|
| Archivos en root | 26 .py | 1 .py (app.py) |
| `sys.path.insert` hacks | 30+ | 0 |
| Líneas en app.py | 540 | ~30 |
| Código comentado (detectores pausados) | 150+ líneas | 0 (config dict) |
| Archivos legacy muertos | 4 | 0 |
| Capas de arquitectura | 1 (flat) | 4 (core/services/adapters/api) |
| Testabilidad de Flask routes | Imposible | `create_app()` factory |
| Cambiar de exchange/broker | Tocar 30+ archivos | Tocar 1 adapter |

---

## 5. RIESGOS Y MITIGACIONES

| Riesgo | Probabilidad | Impacto | Mitigación |
|--------|-------------|---------|------------|
| **Import roto en producción** | Media | 🔴 Bot se cae | Stubs de compatibilidad (Fase 2) garantizan que imports viejos siguen funcionando. Eliminar stubs SOLO después de 24h en producción. |
| **Render no encuentra app.py** | Baja | 🔴 Deploy falla | app.py se queda en root. Render ejecuta `python app.py` — no cambia. |
| **yfinance lock no se aplica** | Baja | 🟡 Datos corruptos | El parche de `yf.download` se aplica en app.py al importar `adapters.yf_lock`. Verificar en los logs. |
| **Detector pausado no arranca al reactivar** | Media | 🟡 Detector no funciona | El registry de `orchestrator.py` con `enabled: True/False` es más fiable que código comentado. |
| **Tests rotos por paths** | Alta | 🟢 Solo CI/CD | Los tests ya usan imports root → stubs garantizan compatibilidad temporalmente. Actualizar en Fase 4. |
| **Merge conflict con ramas en progreso** | Media | 🟡 Resolver manualmente | Hacer refactor en rama separada, merge rápido cuando esté listo. |

### Orden de Ejecución Recomendado

```
Día 1:  Fase 1 (mkdir)          → Deploy → Verificar ✅
Día 1:  Fase 2 (move + stubs)   → Deploy → Verificar 24h ✅
Día 2:  Fase 3 (imports módulos) → Deploy → Verificar ✅
Día 2:  Fase 4 (imports detectors)→ Deploy → Verificar 24h ✅
Día 3:  Fase 5 (cleanup + slim)  → Deploy → Verificar ✅
```

**Tiempo total estimado de ejecución:** Las 5 fases se pueden ejecutar en una sesión con @ejecutor. El tiempo de verificación en producción es lo que marca el ritmo.

---

## 6. COMANDO PARA @EJECUTOR

```
@ejecutor Ejecuta la Fase 1 y Fase 2 del plan de refactorización en 
docs/REFACTORING_PLAN.md. Crea los directorios, mueve los archivos y 
genera los stubs de compatibilidad. No elimines nada todavía. Verifica 
que `python app.py` arranca sin errores.
```

---

*Plan generado por @arquitecto-infra — 2026-04-18*
