# Arquitectura Multi-Timeframe — Recomendación Técnica

## El problema actual

Los detectores actuales analizan cada timeframe de forma **aislada**. El 15M no sabe qué dice el 4H, el 1D no sabe qué dice el 1W. Resultado: señales que técnicamente son válidas en el TF menor pero van contra la tendencia mayor → pérdidas innecesarias.

Ejemplo real (11-abr-2026):  
- 1D Gold: en zona de resistencia → bearish  
- 15M Gold: rebote momentáneo → señal BUY 🔥  
- El 15M "ganó" y mandó señal alcista en plena resistencia diaria

---

## Recomendación: Opción C — "Sesgo compartido + Cascada"

### Por qué esta opción

| Criterio | Opción A (todo en uno) | Opción B (consenso post) | **Opción C (cascada)** |
|---|---|---|---|
| Reutiliza código existente | ❌ Reescribir todo | ✅ Sí | ✅ Sí |
| Thread único por símbolo | ✅ | ❌ Múltiples | ❌ Múltiples |
| Señal solo si TFs alinean | ✅ | ⚠️ Depende | ✅ |
| Permite ver confluencia | ⚠️ Complejo | ✅ | ✅ |
| Implementación progresiva | ❌ Big bang | ✅ | ✅ |
| **Riesgo de romper lo que funciona** | 🔴 Alto | 🟡 Medio | 🟢 Bajo |

### Cómo funciona la cascada

```
1W → publica sesgo (BEARISH / BULLISH / NEUTRAL)
 └─ 1D → si confirma, publica sesgo
     └─ 4H → si confirma, publica sesgo
         └─ 1H → si confirma, publica sesgo
             └─ 15M → si confirma → DISPARA SEÑAL ✅
```

Si algún TF superior contradice → la señal del menor **no se envía**.

---

## Arquitectura técnica

### Módulo central: `tf_bias.py`

Un diccionario en memoria compartida entre todos los threads:

```python
# Estructura del sesgo compartido
bias_store = {
    'XAUUSD': {
        '1W':  {'bias': 'BEARISH',  'score': 14, 'ts': datetime},
        '1D':  {'bias': 'BEARISH',  'score': 11, 'ts': datetime},
        '4H':  {'bias': 'BEARISH',  'score': 8,  'ts': datetime},
        '1H':  {'bias': 'NEUTRAL',  'score': 5,  'ts': datetime},
        '15M': {'bias': 'BULLISH',  'score': 6,  'ts': datetime},
    },
    'EURUSD': { ... },
    'SPX500': { ... },
}
```

Cada detector, al terminar su análisis, **publica su sesgo** antes de decidir si envía señal.

### Regla de activación

```
Para DISPARAR señal en TF X:
  - TF X debe tener score ≥ umbral
  - Al menos 2 de los 3 TFs inmediatamente superiores deben coincidir en dirección
  - El TF 1D nunca puede ser contrario (es el árbitro final)
```

### Mensaje enriquecido con confluencia

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
  ✅ 1W  → BEARISH  (score 14)
  ✅ 1D  → BEARISH  (score 11)
  ✅ 4H  → BEARISH  (score 14)  ← este TF
  ✅ 1H  → BEARISH  (score 8)
  ❌ 15M → BULLISH  (score 6)  — ignorada
```

---

## Plan de implementación (4 fases)

### Fase 1 — Módulo `tf_bias.py` (base)
Crear el módulo central con:
- `publicar_sesgo(simbolo, tf, bias, score)`
- `obtener_confluencia(simbolo, tf_actual, direccion)` → devuelve True/False + descripción
- Thread-safe con `threading.Lock()`

**Estimado: 1 archivo nuevo, ~80 líneas**

### Fase 2 — Integrar en detectores existentes (1D + 4H)
Agregar al final de `analizar()` en los 14 detectores actuales:
```python
# Publicar sesgo (siempre, independiente de si se envía señal)
publicar_sesgo(simbolo, '1D', 'BEARISH' if score_sell > score_buy else 'BULLISH' if score_buy > score_sell else 'NEUTRAL', max(score_sell, score_buy))
```
Y antes de enviar señal, verificar confluencia del TF superior.

**Estimado: ~5 líneas por detector × 14 = 70 líneas en total**

### Fase 3 — Nuevos detectores TFs faltantes
Los TFs que no existen aún y que necesita la cascada completa:

| TF | Símbolo(s) prioritario(s) | Existe | Acción |
|---|---|---|---|
| 1W | Gold, SPX, EURUSD | ❌ | Crear |
| 1H | SPX, EURUSD, BTC | ⚠️ Solo Gold | Crear para los demás |
| 30M | Gold, SPX, EURUSD | ❌ | Crear (opcional, agrega ruido) |
| 5M  | Gold | ❌ | Crear (muy ruidoso, último paso) |
| 1M  | — | ❌ | **No recomendado** (yfinance no es fiable a 1M) |

**Prioridad real: 1W para todos > 1H SPX/EURUSD > 30M (opcional) > 5M (avanzado)**

### Fase 4 — Dashboard de confluencia
Endpoint `/confluencia` en Flask que muestre el estado actual de todos los TFs por símbolo. Útil para ver de un vistazo qué activos tienen alineación completa.

---

## Cascada recomendada por símbolo

### Gold (XAUUSD)
```
1W → 1D → 4H → 1H → 15M → 5M
```
Gold tiene volumen 24H y reacciona muy bien a niveles de TFs mayores. El 15M y 5M son excelentes para entrada precisa.

### SPX500
```
1W → 1D → 4H → 1H → 15M
```
El 5M en SPX genera demasiado ruido intradiario, sobre todo en apertura NY. Parar en 15M es lo óptimo.

### EURUSD
```
1W → 1D → 4H → 1H → 15M
```
Forex respeta mucho los niveles semanales y diarios. El 1W es crítico aquí.

### Bitcoin (BTCUSD)
```
1W → 1D → 4H
```
BTC es tan volátil en TFs menores que el 4H ya es suficientemente "corto". Scalping en BTC con 15M es de alto riesgo.

### WTI, NAS100, Silver
```
1W → 1D → 4H
```
Suficiente para estos activos. Scalping intradiario en WTI y Silver es posible pero secundario.

---

## Reglas de oro (no cambiar nunca)

1. **1D nunca se ignora.** Si 1D dice BEARISH y 15M dice BUY → no se envía la señal BUY del 15M.
2. **1W define el contexto de la semana.** Si 1W es BULLISH, las señales SELL son "contra tendencia" y se marcan con ⚠️ en el mensaje.
3. **Mínimo 2 TFs superiores confirmando** para disparar señal en cualquier TF menor.
4. **Los TFs muy cortos (5M, 1M) solo se usan para timing de entrada**, nunca para definir dirección.
5. **yfinance no entrega datos de 1M fiables** en las pruebas. Descartar el 1M por ahora.

---

## Estado actual del sistema

```
DETECTORES ACTIVOS ✅:
  Gold:    1D ✅ | 4H ✅ | 1H ✅ | 15M ✅
  SPX:     1D ✅ | 4H ✅ | 15M ✅
  EURUSD:  1D ✅ | 4H ✅ | 15M ✅
  Bitcoin: 1D ✅ | 4H ✅
  NAS100:  1D ✅ | 4H ✅
  WTI:     1D ✅ | 4H ✅
  Silver:  1D ✅ | 4H ✅

CASCADA MULTI-TF (Fase 1+2) ✅:
  tf_bias.py ✅  (módulo central thread-safe)
  SPX:     1D publica sesgo ✅ | 4H verifica 1D ✅ | 15M verifica 1D+4H ✅
  EURUSD:  1D publica sesgo ✅ | 4H verifica 1D ✅ | 15M verifica 1D+4H ✅
  Bitcoin: 1D publica sesgo ✅ | 4H verifica 1D ✅
  Gold:    ❌ pendiente integración tf_bias
  NAS100:  ❌ pendiente integración tf_bias
  WTI:     ❌ pendiente integración tf_bias
  Silver:  ❌ pendiente integración tf_bias

CANALES TELEGRAM (tópicos foro) ✅:
  Scalping (thread 302): gold_15m | spx_15m | eurusd_15m
  Swing    (thread 304): todos los 1D/4H/1H

PENDIENTE (Fase 3):
  Todos:   1W ❌
  SPX:     1H ❌
  EURUSD:  1H ❌
  Bitcoin: 1H ❌ (opcional)
  Gold:    5M ❌ (opcional, avanzado)
  Gold/NAS100/WTI/Silver: integración tf_bias ❌
```

---

## Próximo paso concreto

~~Implementar **Fase 1** (`tf_bias.py`) y **Fase 2** (integrar en los 14 detectores existentes) antes de crear detectores nuevos.~~ ✅ **Completado para SPX, EURUSD, Bitcoin.**

Pendiente de integración tf_bias: **Gold, NAS100, WTI, Silver** (misma mecánica — publicar sesgo 1D, verificar en 4H).

**Orden recomendado:**
1. ~~`tf_bias.py`~~ ✅
2. ~~Integrar en SPX + EURUSD (1D/4H/15M)~~ ✅
3. ~~Integrar en Bitcoin (1D/4H)~~ ✅
4. Integrar en Gold, NAS100, WTI, Silver (1D/4H)
5. Detectores 1W (Gold, SPX, EURUSD, BTC, NAS100)
6. Detectores 1H faltantes (SPX, EURUSD)
7. Validar en producción durante 1 semana
8. 30M y 5M solo si la validación es positiva
