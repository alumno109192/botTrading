# PENDIENTES — Issues y Mejoras del Bot
> Creado: 15 abril 2026 | Precio de referencia: ~$4.833 XAUUSD

---

## 🔴 CRÍTICO — Bot genera CERO señales en 1D / 4H / 1H

### Causa raíz: Zonas S/R desactualizadas

Las zonas de soporte/resistencia en los detectores 1D, 4H y 1H siguen configuradas para el rango de precio antiguo (~$3.300). Con el oro cotizando a ~$4.833, el precio queda "en medio" de las zonas, nunca cerca de ninguna, y el filtro de proximidad bloquea el 100% de las señales.

#### Estado actual vs necesario

| Detector | Parámetro | Valor actual ❌ | Valor sugerido ✅ |
|---|---|---|---|
| 1D | zona_resist_high | 5200.0 | ~4980.0 |
| 1D | zona_resist_low | 5000.0 | ~4940.0 |
| 1D | zona_soporte_high | 4700.0 | ~4780.0 |
| 1D | zona_soporte_low | 4500.0 | ~4720.0 |
| 4H | zona_resist_high | 5200.0 | ~4950.0 |
| 4H | zona_resist_low | 5000.0 | ~4900.0 |
| 4H | zona_soporte_high | 4700.0 | ~4790.0 |
| 4H | zona_soporte_low | 4500.0 | ~4730.0 |
| 1H | zona_resist_high | 5200.0 | ~4920.0 |
| 1H | zona_resist_low | 5000.0 | ~4880.0 |
| 1H | zona_soporte_high | 4700.0 | ~4800.0 |
| 1H | zona_soporte_low | 4500.0 | ~4760.0 |

> Referencia: 15M tiene zonas correctas (resist 4880-4910 / support 4760-4790) y 5M también (resist 4850-4870 / support 4800-4815). Las zonas de 1D/4H/1H deben derivarse de esos mismos niveles amplificados por la tolerancia del timeframe.

**Archivos a modificar:**
- `detectors/gold/detector_gold_1d.py` — líneas 49-52
- `detectors/gold/detector_gold_4h.py` — líneas 51-54
- `detectors/gold/detector_gold_1h.py` — líneas 44-47

---

### TPs fijos en 1D y 4H también desactualizados

Una vez actualizadas las zonas, los TPs también hay que revisar. En 1D los TPs de venta tienen targets por debajo del precio actual, que funcionan en dirección correcta si hay un entry desde resistencia, pero los de compra (tp1_compra: 5000) estarían dentro de la resistencia.

#### 1D — TPs actuales
```
tp1_venta: 4750  → OK si entry es ~4940 (caída de ~$190)
tp2_venta: 4550  → Agresivo pero válido para 1D
tp3_venta: 4300  → Muy agresivo
tp1_compra: 5000 → Si entry es ~4730, subida de ~$270 ✅
tp2_compra: 5200 → Subida de ~$470 ✅
tp3_compra: 5400 → Subida de ~$670 ✅
```

#### 4H — TPs actuales
```
tp1_venta: 4800  → Si entry es ~4900, caída de solo ~$100 ✅
tp2_venta: 4600  → Caída de ~$300 — agresivo para 4H
tp3_venta: 4400  → Muy agresivo para 4H
tp1_compra: 5000 → Si entry es ~4730, subida de ~$270 — amplio para 4H
```

> Recomendación: convertir 4H a TPs basados en ATR (como ya hace 1H) para que se ajusten automáticamente al rango actual.

---

## 🔴 BUG — Anti-spam BUY en 4H comprueba dirección incorrecta

**Archivo:** `detectors/gold/detector_gold_4h.py` — línea 655

```python
# ACTUAL (INCORRECTO):
if db and db.existe_senal_reciente(simbolo_db, "VENTA", horas=2):
    print(f"  ℹ️  Señal COMPRA duplicada - No se guarda")

# CORRECTO:
if db and db.existe_senal_reciente(simbolo_db, "COMPRA", horas=2):
```

**Consecuencia:** El anti-spam para señales BUY nunca bloquea duplicados —  comprueba si hay una VENTA reciente en lugar de una COMPRA reciente. Las señales BUY podrían duplicarse por cada ciclo en que la condición se cumpla.

---

## 🟡 ALTO — Anti-spam `alertas_enviadas` se pierde en cada restart

**Todos los detectores** usan un diccionario en memoria:

```python
alertas_enviadas = {}  # Se resetea al reiniciar el proceso
```

En Render, cualquier deploy, crash o restart vacía este dict. El mecanismo `ya_enviada(tipo)` que evita duplicados dentro de la misma vela deja de funcionar temporalmente.

**Efecto concreto:** Si el bot se reinicia en medio de una vela donde ya envió una señal SELL_MAX, la puede reenviar en el siguiente ciclo.

**Solución candidata:** Usar la BD (SQLite / PostgreSQL) como fuente de verdad. Ya existe `existe_senal_reciente()` en `db_manager.py` que hace exactamente esto. Simplificar `ya_enviada()` para que llame a ese método.

**Nota:** `existe_senal_activa_tf()` ya existe en `db_manager.py` pero no está siendo usada por ningún detector todavía.

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

## 🟡 ALTO — Scores de 4/21 generan señales de ruido

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

## 🟢 BAJO — Cambios locales pendientes de commit

Los siguientes cambios están en local (post-revert del 15/04) sin commit:

**Archivos modificados:**
- `detectors/gold/detector_gold_1d.py` — Telegram retry backoff, OBV vectorizado
- `detectors/gold/detector_gold_4h.py` — Telegram retry backoff, OBV vectorizado  
- `detectors/gold/detector_gold_1h.py` — Telegram retry backoff, OBV vectorizado, session filter 07-17 UTC
- `app.py` — EURUSD reactivado
- `db_manager.py` — `existe_senal_activa_tf()` añadida

**Acción:** Hacer commit una vez validados los cambios (al menos el bug del anti-spam 4H BUY corregido).

---

## Resumen de Prioridades

| # | Issue | Impacto | Esfuerzo |
|---|---|---|---|
| 1 | Actualizar zonas 1D/4H/1H | 🔴 Crítico — bot parado | Bajo (~15 min) |
| 2 | Bug anti-spam BUY 4H | 🔴 Bug real | Muy bajo (1 línea) |
| 3 | Revisar TPs 1D/4H post-zona | 🔴 Señales incorrectas | Bajo (~20 min) |
| 4 | Anti-spam persistente (BD) | 🟡 Se pierden duplicados | Medio |
| 5 | Confirmar Thread IDs Telegram | 🟡 Señales en tópico wrong | Muy bajo |
| 6 | TPs ATR en 4H/1D | 🟡 Menor mantenimiento | Medio |
| 7 | yfinance delay 15M/5M | 🟡 Scalping con lag | Alto (requiere otra API) |
| 8 | Backtesting básico | 🟡 Validar parámetros | Alto |
| 9 | Subir score mínimo ALERTA | 🟢 Menos ruido | Bajo |
| 10 | Limpiar archivos huérfanos | 🟢 Higiene | Muy bajo |
| 11 | Commit cambios locales | 🟢 Pendiente | Muy bajo |
