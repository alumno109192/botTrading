# PENDIENTES — Issues y Mejoras del Bot
> Creado: 15 abril 2026 | Precio de referencia: ~$4.833 XAUUSD
> Última actualización: 15 abril 2026 (commit b17aef9)

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

### 🟡 P6 — Limpiar archivos huérfanos del repositorio

| Archivo | Acción |
|---|---|
| `detectors/gold/detector_gold_copy.py` | Eliminar (copia obsoleta) |
| `detector_gold_copy.py` (raíz) | Eliminar |
| `CONTEXTO_PROYECTO.md` | Añadir a .gitignore (doc Claude-web, no código) |
| `PLAN_MEJORA_Y_APP.md` | Añadir a .gitignore |

---

### 🟡 P7 — Verificar Thread IDs de Telegram

```
THREAD_ID_SWING=304      # ← ¿Confirmado? 1D/4H/1H envían aquí
THREAD_ID_INTRADAY=???   # ← Nunca configurado
THREAD_ID_SCALPING=535   # ✅ Confirmado
```

Si `THREAD_ID_SWING=304` es incorrecto, todas las señales swing van al tópico equivocado sin avisar (Telegram no devuelve error visible).

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
| P6 | Limpiar archivos huérfanos | 🟡 Higiene | Pendiente |
| P7 | Confirmar Thread IDs Telegram | 🟡 Señales tópico wrong | Pendiente |
| — | yfinance delay 15M/5M | 🟡 Scalping con lag | Requiere otra API |
| — | Backtesting básico | 🟡 Validar parámetros | Sin fecha |
