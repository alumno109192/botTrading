# Patrón Stop Hunt / Falsa Ruptura + Recuperación

**Añadido:** 20 abril 2026  
**Detectores afectados:** `detector_gold_5m.py`, `detector_gold_15m.py`  
**Función:** `detectar_stop_hunt_alcista()` / `detectar_stop_hunt_bajista()` en `core/indicators.py`

---

## ¿Qué es este patrón?

El **Stop Hunt** (también llamado "Falsa Ruptura" o "Liquidity Sweep") ocurre cuando el precio perfora brevemente un nivel clave — mínimo o máximo de sesión reciente — activando los stops de los traders que tenían posiciones en esa dirección, para luego revertir con fuerza y moverse en la dirección opuesta.

Es especialmente frecuente en **Gold (XAUUSD)** durante las sesiones London y NY, donde los market makers buscan la liquidez acumulada por encima o por debajo de zonas visibles.

---

## Por qué se implementó

El **20 de abril de 2026**, a las **15:28:56 UTC**, se produjo una entrada conservadora en Gold que resultó en 2 TPs cerrados y una posición abierta con SL en Break Even. El bot **no generó señal** porque:

1. La función `calcular_zonas_sr()` actualiza el `support_pivot` al **nuevo low del spike**, desplazando la zona hacia abajo en el momento exacto del rebote. El precio al recuperarse queda "fuera de zona" y no suma los 2 puntos de S/R.
2. El patrón no estaba modelado en el sistema de scoring.

---

## Estructura del patrón

### Alcista (BUY)

```
       ┌────────────────────────────────┐
       │   Mínimo de las últimas N velas│  ← swing_low
       └─────────────────┬──────────────┘
                         │
              ▼  Spike bajista (Low < swing_low)
              │
              └──→ Cierre por ENCIMA de swing_low  ← reclaim
                   + Mecha inferior larga
```

**Señal:** El precio barrió los stops bajo la zona y los compradores absorbieron toda la presión → impulso alcista.

### Bajista (SELL)

```
       ┌────────────────────────────────┐
       │   Máximo de las últimas N velas│  ← swing_high
       └─────────────────┬──────────────┘
                         │
              ▲  Spike alcista (High > swing_high)
              │
              └──→ Cierre por DEBAJO de swing_high  ← reclaim
                   + Mecha superior larga
```

---

## Condiciones de detección (código)

```python
# Alcista
ruptura    = vela.Low   < swing_low           # perfora el mínimo
reclaim    = vela.Close > swing_low           # cierra por encima
mecha_larga = (lower_wick > body)             # pin bar
             OR (lower_wick / total_range > 0.5)  # >50% del rango es mecha

# Bajista (simétrico con High / upper_wick)
```

Todos los campos usan la **vela cerrada más reciente** (`df.iloc[-1]`).

---

## Parámetros

| Parámetro | 5M | 15M | Descripción |
|---|---|---|---|
| `lookback` | 20 | 20 | Número de velas anteriores para calcular swing high/low |
| Ventana temporal | ~100 min | ~5 horas | Historia considerada |
| Puntos al score | +3 | +3 | Peso en el sistema de scoring |

El `lookback=20` se puede ajustar en la llamada a la función si en el futuro se quieren afinar los resultados por timeframe.

---

## Impacto en el scoring

Con la incorporación de este patrón, el **score máximo** pasa de 15 a **18 puntos** (se suman hasta 3 extra por Stop Hunt). El umbral de señal fuerte sigue en **≥ 8 puntos**.

Un Stop Hunt detectado sin otros indicadores alineados **no es suficiente** para generar señal por sí solo. Requiere que el resto del contexto (RSI, EMAs, sesgo TF superior) al menos no sea contradictorio.

---

## Registro en base de datos

El campo `patron_velas` de la tabla `senales` incluye ahora:

```
Envolvente:False, Doji:False, StopHunt:True
```

Esto permite analizar en backtesting qué porcentaje de señales con `StopHunt:True` resultaron en TP.

---

## Limitaciones conocidas

- El patrón detecta **la vela del spike**, no la vela de confirmación siguiente. En timeframes bajos (5M) puede haber ruido.
- No distingue si la ruptura fue provocada por un evento macro (en ese caso el filtro de calendario económico ya debería haberlo bloqueado).
- El `lookback` fijo no se adapta a la volatilidad del día. En jornadas de rango muy amplio, el swing_low/high puede ser muy lejano y el spike parecerá menor de lo que es.

---

## Ejemplo real (20 abril 2026)

| Campo | Valor |
|---|---|
| Hora entrada conservadora | 15:28:56 UTC |
| Activo | XAU/USD |
| Timeframe | 5M |
| Resultado | TP1 ✅, TP2 ✅, TP3 abierto con SL en BE |
| Señal bot | ❌ No generada (sin Stop Hunt en scoring) |
| Señal bot con parche | ✅ Se habría generado (+3 pts que cubren el déficit de S/R) |
