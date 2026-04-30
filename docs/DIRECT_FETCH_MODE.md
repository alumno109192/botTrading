# DIRECT_FETCH_MODE — Consulta Directa a TwelveData

## 🔥 Descripción

Con el **plan Grow 55** (peticiones ilimitadas), activar `DIRECT_FETCH_MODE=true` para obtener **datos siempre frescos** directamente desde TwelveData, sin capas de cache intermedias.

## 📊 Arquitecturas Disponibles

### Modo DIRECT (recomendado con plan de pago)

```
┌─────────────┐
│  Detector   │
└──────┬──────┘
       ↓
┌──────────────┐         ┌──────────────┐
│ TwelveData   │ ←────→  │  yfinance    │
│ (tiempo real)│         │  (fallback)  │
└──────────────┘         └──────────────┘
```

**Ventajas:**
- ✅ Datos siempre frescos (tiempo real)
- ✅ Sin TTL de cache (no hay datos antiguos)
- ✅ Arquitectura simple
- ✅ Ideal para plan ilimitado

**Configuración:**
```bash
DIRECT_FETCH_MODE=true
TWELVE_DATA_API_KEY=tu_key_grow_55
```

---

### Modo LEGACY (para plan gratuito limitado)

```
┌─────────────┐
│  Detector   │
└──────┬──────┘
       ↓
┌──────────────┐  miss
│ Cache Memoria│ ────→
│  (TTL 65s)   │
└──────────────┘
       ↓ miss
┌──────────────┐
│ BD Local     │ ────→
│ (Turso/SQLite)│
└──────────────┘
       ↓ miss
┌──────────────┐         ┌──────────────┐
│ TwelveData   │ ←────→  │  yfinance    │
│              │         │  (fallback)  │
└──────────────┘         └──────────────┘
```

**Ventajas:**
- ✅ Ahorra peticiones API (para plan limitado)
- ✅ Funciona offline con datos en BD
- ✅ Resiliente a caídas de API

**Configuración:**
```bash
DIRECT_FETCH_MODE=false
TWELVE_DATA_API_KEY=tu_key_free
```

---

## 🎯 ¿Cuándo usar cada modo?

| Criterio | DIRECT | LEGACY |
|----------|--------|--------|
| **Plan TD** | Grow 55+ (ilimitado) | Free (800/día) |
| **Frescura datos** | Tiempo real siempre | TTL 65s (cache) |
| **API calls/día** | ~3,000-5,000 | ~500-1,000 |
| **Latencia** | Baja (directo) | Muy baja (cache) |
| **Uso de BD** | No (solo poller) | Sí (lectura/escritura) |

## 📝 Logs del Sistema

### Logs en modo DIRECT

```
🔥 [DIRECT] Twelve Data (key1) — GC=F 5m (400 velas, tiempo real)
⚠️ [DIRECT] Twelve Data key1 falló — rotando a siguiente key
⚠️ [DIRECT] Todas las keys TD fallaron — fallback a yfinance
```

### Logs en modo LEGACY

```
💾 [cache] Cache mem hit — GC=F 5m (400 velas)
✅ [legacy] Twelve Data (key1) — GC=F 5m (400 velas, tiempo real)
⚠️ [cache] Cache insuficiente (25 velas) — refrescando
```

---

## ⚙️ Configuración en Render

Para cambiar el modo en producción (Render):

1. **Dashboard de Render** → tu servicio
2. **Environment** → Edit
3. Agregar/modificar: `DIRECT_FETCH_MODE=true`
4. **Save Changes** (auto-redeploy)

---

## 🔍 Verificar Modo Activo

```python
from adapters.data_provider import DIRECT_FETCH_MODE
print(f"DIRECT_FETCH_MODE: {DIRECT_FETCH_MODE}")
```

O en logs al arrancar:
```
🔥 MODO DIRECT FETCH — Plan Grow 55 (peticiones ilimitadas)
```

---

## 📈 Consumo Estimado

Con `DIRECT_FETCH_MODE=true`:

```
Polling cada 60s (5M):     ~1,440 req/día
Detectores (5 activos):    ~2,000 req/día
──────────────────────────────────────
Total:                     ~3,500 req/día
```

Con plan **Grow 55 ilimitado**, esto es **0% de capacidad** ✨

---

## ⚠️ Importante

- El `ohlcv_poller` sigue escribiendo en BD (para históricos y análisis)
- Los detectores **NO leen de BD** en modo DIRECT, consultan directo
- Fallback a yfinance si TD falla (delay 15 min aceptable)
- Límite de 55 req/min se respeta en ambos modos

---

## 🚀 Recomendación

**Con plan Grow 55:** Usar `DIRECT_FETCH_MODE=true` para máxima frescura de datos.

**Con plan Free:** Usar `DIRECT_FETCH_MODE=false` para ahorrar peticiones.
