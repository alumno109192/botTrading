# AUDITORÍA TÉCNICA v2 — BotTrading
## Fecha: 2026-04-18

---

### RESUMEN EJECUTIVO

22 hallazgos: 6 críticos, 7 deuda técnica, 3 performance, 3 seguridad, 3 resiliencia.
Prioridad inmediata: calendario duplicado sobreescribe datos (C01), DXY sin lock (C02), ATR/ADX rotos en scalping (C03-C05), PREP sin filtros (C06).

---

### MATRIZ DE HALLAZGOS

| ID   | Sev  | Archivo                          | Línea  | Descripción                                                     |
|------|------|----------------------------------|--------|-----------------------------------------------------------------|
| C01  | 🔴   | economic_calendar.py             | 191    | Segunda definición EVENTOS_ALTO_IMPACTO sobreescribe la primera |
| C02  | 🔴   | dxy_bias.py                      | 49     | yf.download() sin _yf_lock — race condition con otros threads   |
| C03  | 🔴   | detector_gold_15m.py             | 120    | calcular_atr() usa SMA en vez de Wilder EWM (diverge ~10-15%)  |
| C04  | 🔴   | detector_gold_5m.py              | 116    | calcular_atr() usa SMA en vez de Wilder EWM                    |
| C05  | 🔴   | detector_gold_15m.py             | 139    | calcular_adx() fórmula rota: `calcular_atr(df,1)*length`       |
| C06  | 🔴   | detector_gold_1h.py              | 309    | PREP signals enviadas sin confluencia multi-TF ni filtro R:R    |
| D01  | 🟡   | detectors/ (todos)               | —      | enviar_telegram() idéntica ×15+ archivos                        |
| D02  | 🟡   | detectors/spx,btc,eur,silver,wti | —      | Indicadores locales no migrados a shared_indicators              |
| D03  | 🟡   | run_detectors.py                 | 11-15  | Importa legacy root-level (detector_gold, detector_spx_copy)    |
| D04  | 🟡   | detector_gold_5m.py              | 121    | calcular_adx retorna Series; shared_indicators retorna tuple    |
| D05  | 🟡   | detectors/ (todos)               | ~30    | 6+ instancias DatabaseManager independientes                    |
| D06  | 🟡   | db_manager.py                    | 74     | class Result redefinida en cada llamada a ejecutar_query()       |
| D07  | 🟡   | detector_gold_15m.py/5m.py       | —      | patron_envolvente_* y patron_doji no centralizados              |
| P01  | 🔵   | signal_monitor.py                | ~300   | 3 queries HTTP/señal/tick → 60 req/min con 10 señales           |
| P02  | 🔵   | detector_gold_1d.py              | ~250   | Descarga 2 años datos diarios cada 10 min (solo cambia 1×/día) |
| P03  | 🔵   | economic_calendar.py             | 191    | Segunda lista regenera _EVENTOS_DT — overhead en import         |
| S01  | 🟠   | app.py                           | 454    | /status sin autenticación — expone estado interno del sistema   |
| S02  | 🟠   | dxy_bias.py                      | 49     | Sin rate limiting en llamadas yfinance externas                  |
| S03  | 🟠   | requirements.txt                 | 5      | requests==2.31.0 pinned exacto (Sep 2023) — sin patches         |
| R01  | ⚪   | signal_monitor.py                | 505    | No recrea DatabaseManager si query falla mid-session             |
| R02  | ⚪   | detectors/ (todos)               | —      | RuntimeError de calendario no genera alerta Telegram             |
| R03  | ⚪   | detector_gold_1h.py              | 309    | PREP enviado → confirmación bloqueada → usuario con orden falsa  |

---

### DETALLE POR CATEGORÍA

#### 🔴 CRÍTICOS

**C01 — Calendario duplicado sobreescribe datos completos**
```
economic_calendar.py
  Línea  27: EVENTOS_ALTO_IMPACTO = [...]   # Abr–Dic 2026 (lista completa)
  Línea 124: _EVENTOS_DT = sorted(...)       # Pre-computa sobre lista completa ✓
  Línea 191: EVENTOS_ALTO_IMPACTO = [...]   # Abr–Jun 2026 (truncada) ← SOBREESCRIBE
```
**Impacto**: `_EVENTOS_DT` y `_EVENTOS_SORTED` se computan en líneas 124-130 con la lista completa. Pero la segunda definición en línea 191 sobreescribe el nombre `EVENTOS_ALTO_IMPACTO`. La función `proximos_eventos()` (línea 225) itera sobre `_EVENTOS_SORTED` (ya computada, OK). Sin embargo, cualquier código que lea `EVENTOS_ALTO_IMPACTO` directamente obtiene solo Abr–Jun. Además genera confusión de mantenimiento.
**Fix**: Eliminar la segunda definición (líneas 186-222).

---

**C02 — dxy_bias.py: yf.download sin _yf_lock**
```python
# dxy_bias.py línea 49
dxy = yf.download("DX-Y.NYB", period="10d", interval="1h", progress=False)
```
**Impacto**: 5+ threads ejecutan detectores concurrentemente. Cada uno puede llamar `get_dxy_bias()`. Si coincide con otro `yf.download()` en un detector (protegido por `_yf_lock`), yfinance mezcla columnas MultiIndex (bug conocido de yfinance). Resultado: crash o datos corruptos silenciosos.
**Fix**:
```python
from yf_lock import _yf_lock
# En get_dxy_bias():
with _yf_lock:
    dxy = yf.download(...)
```

---

**C03 — 15m: calcular_atr() usa SMA, no Wilder EWM**
```python
# detector_gold_15m.py línea 120
def calcular_atr(df, length):
    ...
    atr = tr.rolling(length).mean()   # ← SMA (media simple)

# shared_indicators.py (canónico):
    return tr.ewm(com=length - 1, min_periods=length).mean()  # ← EWM Wilder
```
**Impacto**: SMA-ATR diverge ~10-15% del EWM-ATR. Afecta directamente SL (`atr * atr_sl_mult`), TP1/TP2/TP3, y ancho de zonas S/R. En scalping 15M con Gold (~$15-25 ATR), la diferencia es ~$2-3 en SL/TP.
**Fix**: Eliminar función local, importar `calcular_atr` de `shared_indicators`.

---

**C04 — 5m: calcular_atr() misma divergencia SMA vs EWM**
```python
# detector_gold_5m.py línea 116
def calcular_atr(df, length):
    ...
    return tr.rolling(length).mean()  # ← SMA
```
**Impacto**: Mismo que C03 pero en micro-scalping 5M donde márgenes son aún más ajustados.
**Fix**: Eliminar función local, importar `calcular_atr` de `shared_indicators`.

---

**C05 — 15m: calcular_adx() fórmula matemáticamente incorrecta**
```python
# detector_gold_15m.py línea 139
def calcular_adx(df, length=14):
    ...
    tr = calcular_atr(df, 1) * length  # ← ATR con length=1 = TR sin suavizado
    # Luego multiplica por 14... esto NO es ATR suavizado Wilder
    plus_di  = 100 * (plus_dm.ewm(alpha=1/length).mean() / tr)
```
**Impacto**: El ADX resultante no corresponde al indicador estándar. Condiciones `adx > 25` (tendencia fuerte) y `adx < 20` (rango) producen resultados arbitrarios. Las señales de scalping 15M filtran tendencia con un ADX roto.
**Fix**: Importar `calcular_adx` de `shared_indicators`. Nota: shared_indicators retorna `(adx, di_plus, di_minus)` como tuple — actualizar call site.

---

**C06 — 1H: PREP signals sin confluencia multi-TF ni R:R**
```python
# detector_gold_1h.py
# Líneas 309-348: PREP_SELL/PREP_BUY se evalúan y envían a Telegram
if aproximando_resist and not cancelar_sell and not ya_enviada('PREP_SELL'):
    msg = "⚡ SEÑAL SELL — PON ORDEN LIMIT AHORA\n..."
    enviar_telegram(msg)  # ← Se envía AQUÍ

# Líneas 381-395: confluencia multi-TF se verifica DESPUÉS
_ok, _desc = tf_bias.verificar_confluencia(simbolo, '1H', tf_bias.BIAS_BEARISH)
if not _ok: senal_sell_fuerte = False  # ← Solo bloquea FUERTE, no PREP

# Líneas 397-402: filtro R:R también DESPUÉS
if rr_sell_tp1 < 1.2: ...  # ← Solo bloquea confirmación, no PREP
```
**Impacto**: Usuario recibe "PON ORDEN LIMIT AHORA", pone la orden, pero la confirmación nunca llega porque confluencia o R:R la bloquean. Señal falsa ejecutada.
**Fix**: Mover verificación de confluencia y R:R ANTES del bloque PREP (líneas 309-348).

---

#### 🟡 DEUDA TÉCNICA

**D01 — enviar_telegram() copiada en 15+ archivos**
Función idéntica en cada detector (3 reintentos, backoff exponencial). Un cambio (ej: añadir message_thread_id) requiere editar 15+ archivos.
**Fix**: Mover a módulo compartido `telegram_utils.py`, importar desde cada detector.

---

**D02 — Detectores no-Gold sin shared_indicators**
| Detector | Funciones locales duplicadas |
|---|---|
| detectors/spx/detector_spx_1d.py | rsi, ema, atr, bollinger, macd, obv, adx (7) |
| detectors/eurusd/detector_eurusd_1d.py | rsi, ema, atr, bollinger, macd, obv, adx (7) |
| detectors/bitcoin/detector_bitcoin_1d.py | rsi, adx (2) |
| detectors/silver/* | (no verificado, probable) |
| detectors/wti/* | (no verificado, probable) |
| detectors/nasdaq/* | (no verificado, probable) |

**Fix**: Migrar progresivamente a `from shared_indicators import ...`, verificando defaults de cada timeframe.

---

**D03 — run_detectors.py con imports legacy**
```python
# run_detectors.py líneas 11-15
import detector_gold          # ← root-level legacy
import detector_spx_copy      # ← root-level legacy
import detector_bitcoin       # ← root-level legacy
```
Los detectores activos están en `detectors/`. Estos imports funcionan solo porque los archivos legacy aún existen en root. Si se eliminan, startup crashea.
**Fix**: Actualizar imports a `detectors.gold.detector_gold_1d` etc., o deprecar el script.

---

**D04 — 5m: calcular_adx retorna tipo incompatible con shared_indicators**
```python
# detector_gold_5m.py — retorna Series (solo ADX)
return dx.ewm(...).mean()

# shared_indicators.py — retorna tuple (adx, plus_di, minus_di)
return adx, plus_di, minus_di
```
**Impacto**: Si se migra 5m a shared_indicators sin cambiar call site, crash:
```python
adx = calcular_adx(df).iloc[-1]  # ← .iloc[-1] sobre tuple → error
```
**Fix**: Al migrar, cambiar call site a `adx, _, _ = calcular_adx(df)` y luego `.iloc[-1]`.

---

**D05 — Múltiples instancias DatabaseManager**
Cada detector crea su propia instancia al importar el módulo. 6+ conexiones HTTP independientes a Turso.
**Fix**: Singleton pattern o inyección de dependencia desde `app.py`.

---

**D06 — class Result redefinida en cada query**
```python
# db_manager.py línea 74 (dentro de ejecutar_query())
class Result:
    def __init__(self, rows_data, columns): ...
```
Se crea una nueva clase en cada invocación. Funcional pero ineficiente.
**Fix**: Mover `Result` a nivel de módulo o usar `namedtuple`.

---

**D07 — Patrones candlestick no centralizados**
`patron_envolvente_alcista()`, `patron_envolvente_bajista()`, `patron_doji()` están en 15m y 5m pero no en `shared_indicators.py`.
**Fix**: Añadir a `shared_indicators.py`.

---

#### 🔵 PERFORMANCE

**P01 — signal_monitor: 3 queries HTTP por señal por tick**
```
Por cada señal activa, cada 30 segundos:
  1. SELECT datos señal (tp/sl/dirección)
  2. INSERT historial_precios
  3. UPDATE precio_actual en señales
= 3 req × 10 señales × 2/min = 60 HTTP req/min solo en monitoreo
```
**Fix**: Batch queries en un solo pipeline HTTP. O reducir `registrar_precio()` a cada 5 minutos en vez de cada tick.

---

**P02 — 1D: descarga 2 años de datos cada 10 minutos**
```python
# detector_gold_1d.py
df, is_delayed = get_ohlcv(params['ticker_yf'], period='2y', interval='1d')
```
Datos diarios cambian 1 vez al día. Descargar 730 filas cada 10 min desperdicia bandwidth y API rate limits.
**Fix**: Cache con TTL = hasta cierre de mercado (17:00 NYC). Solo re-descargar si `date > last_date`.

---

**P03 — Calendario duplicado regenera pre-cómputo**
La segunda definición de `EVENTOS_ALTO_IMPACTO` (línea 191) no tiene pre-cómputo, pero sobreescribe el nombre. `_EVENTOS_DT` y `_EVENTOS_SORTED` ya se computaron con la primera lista y no se re-generan. Confusión pero sin overhead real. Se resuelve con C01.

---

#### 🟠 SEGURIDAD

**S01 — /status sin autenticación**
```python
# app.py línea 454
@app.route('/status')
def status():
    return jsonify(estado_sistema)  # Sin auth → expone threads, timestamps, estados
```
**Fix**: Requerir mismo token que `/cron`:
```python
token = request.headers.get('X-Cron-Token', '')
if token != CRON_TOKEN:
    return jsonify({'error': 'Unauthorized'}), 401
```

---

**S02 — dxy_bias sin rate limiting**
`get_dxy_bias()` tiene cache de 30 min, pero si el cache expira y 5 threads llaman simultáneamente, 5 requests a yfinance en paralelo (sin lock, ver C02).
**Fix**: Además del lock (C02), verificar cache DESPUÉS de adquirir lock (double-check locking).

---

**S03 — requests pinned a versión exacta antigua**
```
# requirements.txt
requests==2.31.0   # Sep 2023 — 2.5 años sin actualizar
```
**Fix**: Cambiar a `requests>=2.31.0,<3.0`.

---

#### ⚪ RESILIENCIA

**R01 — signal_monitor no reconecta DB mid-session**
```python
# signal_monitor.py línea 505
except Exception as e:
    print(f"❌ Error en monitor: {e}")
    time.sleep(60)  # ← Duerme pero NO recrea db = DatabaseManager()
```
Si Turso devuelve 401 (token expirado) o timeout persistente, el monitor queda en loop infinito con la misma instancia rota.
**Fix**:
```python
except Exception as e:
    print(f"❌ Error: {e}")
    try:
        db = DatabaseManager()  # Reconectar
    except:
        pass
    time.sleep(60)
```

---

**R02 — RuntimeError de calendario sin alerta Telegram**
Cuando `economic_calendar.py` expira, lanza `RuntimeError`. Los detectores lo capturan en `except Exception` genérico, hacen retry, pero nunca alertan al operador por Telegram. El operador no sabe que el calendario está caducado hasta revisar logs.
**Fix**: En el loop de cada detector, detectar `RuntimeError` y enviar alerta Telegram una sola vez.

---

**R03 — PREP → bloqueo = señal huérfana**
Flujo problemático:
1. Detector 1H envía PREP "PON ORDEN LIMIT AHORA" → usuario pone orden
2. Precio llega a zona → detector evalúa confirmación
3. Confluencia multi-TF bloquea → confirmación no se envía
4. Usuario tiene orden limit activa sin cancelación automática

**Fix**: (a) Mover confluencia antes de PREP (ver C06), o (b) enviar mensaje de cancelación si confluencia bloquea.

---

### ESTADO vs AUDITORÍA ANTERIOR

La auditoría anterior (v1) identificó 22 items (C1-C4, D1-D10, P1-P4, S1-S4). Todos fueron corregidos:
- ✅ C1 (run_detectors gold_copy): corregido
- ✅ C2 (signal_monitor bool cast): corregido
- ✅ C3 (calendario extendido Dic 2026): corregido
- ✅ C4 (limpiar_duplicados campo): corregido
- ✅ D1 (shared_indicators creado): parcial — solo gold migrado
- ✅ D2-D9: corregidos
- ✅ P1-P4: corregidos
- ✅ S1-S2: corregidos
- ✅ yf_lock.py creado y aplicado en app.py + signal_monitor.py

**Nuevos hallazgos v2** (no presentes en v1):
- C01: Calendario duplicado (efecto colateral de la corrección v1)
- C02: dxy_bias.py sin lock (no cubierto en v1)
- C03-C05: ATR/ADX incorrectos en 15m/5m (no cubiertos en v1)
- C06: PREP sin filtros (lógica de trading no auditada en v1)
- D01-D07: Deuda técnica estructural más amplia
- R01-R03: Resiliencia no cubierta en v1

---

### PRIORIDAD DE ARREGLO

| Prioridad | IDs | Esfuerzo | Riesgo si no se arregla |
|-----------|-----|----------|-------------------------|
| 🔴 P0 — Inmediato | C01, C02 | 5 min | Datos corruptos en producción |
| 🔴 P0 — Inmediato | C03, C04, C05 | 15 min | SL/TP de scalping incorrectos |
| 🔴 P0 — Inmediato | C06 | 20 min | Señales falsas enviadas al usuario |
| 🟡 P1 — Esta semana | S01, R01 | 10 min | Exposición info + monitor muerto |
| 🟡 P1 — Esta semana | D04 | 5 min | Bloquea futura migración 5m |
| 🔵 P2 — Próxima sprint | D01, D02, D07 | 2-4h | Mantenibilidad |
| 🔵 P2 — Próxima sprint | P01, P02 | 1h | Rendimiento en Render free tier |
| ⚪ P3 — Backlog | D03, D05, D06, S02, S03, R02, R03 | Variable | Bajo riesgo |