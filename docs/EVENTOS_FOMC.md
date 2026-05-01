# Sistema de Gestión de Eventos FOMC y Alto Impacto

## Descripción General

El sistema incluye un filtro automático de eventos macroeconómicos de alto impacto (FOMC, NFP, CPI, PIB, discursos de Powell, etc.) que **bloquea la generación de nuevas señales** durante las ventanas de riesgo.

---

## Funcionamiento Actual

### 1. Bloqueo Automático de Nuevas Señales

**Archivo:** [services/economic_calendar.py](../services/economic_calendar.py)

El módulo `economic_calendar` contiene una lista actualizada manualmente de eventos USD de alto impacto.

#### Función Principal: `debe_bloquear_trading(ventana_minutos)`

```python
from services.economic_calendar import debe_bloquear_trading

bloqueado, descripcion, minutos = debe_bloquear_trading(90)
if bloqueado:
    logger.warning(f"🚫 Trading bloqueado: {descripcion} en {minutos} min")
    return  # No se generan señales
```

**Parámetros:**
- `ventana_minutos`: Tiempo antes/después del evento en el que se bloquea trading (ej: 90 min = ±1.5h)

**Retorna:**
- `bloqueado` (bool): True si hay un evento próximo
- `descripcion` (str): Nombre del evento (ej: "FOMC — Decisión tipos Fed")
- `minutos` (int): Minutos hasta el evento

**Uso en detectores:**
- ✅ `detector_gold_5m.py` (ventana: 90 min)
- ✅ `detector_gold_15m.py` (ventana: 90 min)
- ✅ `detector_gold_1h.py` (ventana: 120 min)
- ✅ `detector_gold_4h.py` (ventana: 180 min)
- ✅ `detector_gold_1d.py` (ventana: 360 min)

### 2. Avisos de Eventos Menores

**Función:** `obtener_aviso_macro(ventana_minutos, timeframe, simbolo)`

Retorna un mensaje de advertencia para eventos de impacto medio que **no bloquean señales** pero se añaden al pie del mensaje de Telegram:

```
⚠️ Evento macro próximo: Ventas minoristas EEUU en 35 min
```

---

## 🆕 Propuesta: Cierre Automático de Señales Activas

### Objetivo

Permitir al usuario configurar si desea cerrar automáticamente las señales activas cuando se detecta un evento FOMC u otro evento crítico.

### Diseño de la Funcionalidad

#### 1. Variable de Configuración en `.env`

```bash
# Opciones: "none" | "fomc" | "high_impact" | "all"
# - none: No cerrar señales automáticamente (comportamiento actual)
# - fomc: Cerrar solo en eventos FOMC
# - high_impact: Cerrar en todos los eventos de alto impacto
# - all: Cerrar incluso en eventos de impacto medio
AUTO_CLOSE_ON_EVENTS=fomc
```

#### 2. Nueva Función en `economic_calendar.py`

```python
def debe_cerrar_senales_activas(configuracion: str) -> tuple[bool, str]:
    """
    Determina si se deben cerrar las señales activas según la configuración.
    
    Args:
        configuracion: "none" | "fomc" | "high_impact" | "all"
    
    Returns:
        (debe_cerrar, descripcion_evento)
    """
    ahora = datetime.now(timezone.utc)
    
    # Buscar evento próximo (dentro de 30 minutos)
    for evento in EVENTOS_ALTO_IMPACTO:
        ts_evento = datetime(evento[0], evento[1], evento[2], 
                             evento[3], evento[4], tzinfo=timezone.utc)
        delta = (ts_evento - ahora).total_seconds() / 60
        
        if 0 < delta <= 30:  # Evento en los próximos 30 min
            desc = evento[5]
            
            if configuracion == "none":
                return False, ""
            elif configuracion == "fomc" and "FOMC" in desc:
                return True, desc
            elif configuracion == "high_impact":
                return True, desc
            elif configuracion == "all":
                return True, desc
    
    return False, ""
```

#### 3. Integración en `signal_monitor.py`

El servicio `signal_monitor.py` ya monitorea las señales activas. Se añadirá una verificación periódica:

```python
# En el loop principal de signal_monitor.py
config_auto_close = os.environ.get('AUTO_CLOSE_ON_EVENTS', 'none')

if config_auto_close != 'none':
    debe_cerrar, evento_desc = debe_cerrar_senales_activas(config_auto_close)
    
    if debe_cerrar:
        senales_activas = db.obtener_senales_activas()
        
        for senal in senales_activas:
            # Cerrar señal en precio actual
            precio_actual = obtener_precio_actual(senal['simbolo'])
            
            db.actualizar_estado_senal(
                senal['id'], 
                'CERRADA_EVENTO_MACRO', 
                precio_actual
            )
            
            # Enviar notificación
            mensaje = (
                f"⚠️ <b>SEÑAL CERRADA POR EVENTO MACRO</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"🆔 Señal #{senal['id']}\n"
                f"📊 {senal['simbolo']} {senal['direccion']}\n"
                f"💰 Entry: ${senal['precio_entrada']:.2f}\n"
                f"💰 Close: ${precio_actual:.2f}\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"📌 Evento: {evento_desc}\n"
                f"⚙️ Cierre automático configurado"
            )
            
            enviar_telegram(mensaje, senal['telegram_thread_id'])
```

#### 4. Nueva Columna en Base de Datos

Agregar estado `CERRADA_EVENTO_MACRO` a la columna `estado` en la tabla `senales`:

```sql
-- Estados posibles:
-- ACTIVA
-- PENDIENTE_CONFIRM
-- TP1_ALCANZADO
-- TP2_ALCANZADO
-- TP3_ALCANZADO
-- SL_ALCANZADO
-- CANCELADA
-- CERRADA_MANUAL
-- CERRADA_EVENTO_MACRO  <-- NUEVO
```

---

## Flujo de Ejecución

```
┌─────────────────────────────────────────────────┐
│  Signal Monitor Loop (cada 30 segundos)         │
└─────────────────────────────────────────────────┘
                    │
                    ▼
        ┌───────────────────────────┐
        │ ¿AUTO_CLOSE_ON_EVENTS ≠   │
        │       "none"?              │
        └───────────┬───────────────┘
                    │ Sí
                    ▼
        ┌───────────────────────────┐
        │ debe_cerrar_senales_      │
        │ activas(config)           │
        └───────────┬───────────────┘
                    │
                    ▼
        ┌───────────────────────────┐
        │ ¿Hay evento en próximos   │
        │ 30 minutos?               │
        └───────┬───────────────────┘
                │ Sí (FOMC, NFP...)
                ▼
        ┌───────────────────────────┐
        │ Obtener señales activas   │
        │ de la BD                  │
        └───────────┬───────────────┘
                    │
                    ▼
        ┌───────────────────────────┐
        │ Para cada señal:          │
        │ 1. Obtener precio actual  │
        │ 2. Cerrar señal en BD     │
        │ 3. Calcular P&L           │
        │ 4. Enviar notificación    │
        └───────────────────────────┘
```

---

## Ventajas de Este Diseño

1. **Configurable**: El usuario elige el nivel de automatización (none/fomc/high_impact/all)
2. **No invasivo**: No modifica los detectores existentes
3. **Centralizado**: Toda la lógica está en `signal_monitor.py` y `economic_calendar.py`
4. **Trazable**: Las señales cerradas automáticamente quedan registradas con estado específico
5. **Transparente**: El usuario recibe notificación inmediata de cada cierre

---

## Implementación Paso a Paso

### Fase 1: Backend (Base de Datos y Lógica)
- [ ] Agregar variable `AUTO_CLOSE_ON_EVENTS` a `.env.example`
- [ ] Implementar `debe_cerrar_senales_activas()` en `economic_calendar.py`
- [ ] Agregar estado `CERRADA_EVENTO_MACRO` en BD (migración)
- [ ] Integrar verificación en loop de `signal_monitor.py`

### Fase 2: Mensajes y Notificaciones
- [ ] Crear template de mensaje de cierre automático
- [ ] Agregar logging detallado del proceso
- [ ] Enviar resumen al finalizar cierre masivo (ej: "✅ 3 señales cerradas por FOMC")

### Fase 3: Testing
- [ ] Simular evento próximo ajustando fecha en `EVENTOS_ALTO_IMPACTO`
- [ ] Verificar que se cierran todas las señales activas
- [ ] Confirmar que los mensajes de Telegram llegan correctamente
- [ ] Validar que el estado en BD se actualiza

### Fase 4: Documentación Usuario Final
- [ ] Crear guía en [GUIA_INICIO.md](GUIA_INICIO.md) con ejemplos
- [ ] Actualizar README.md con la nueva funcionalidad

---

## Preguntas Frecuentes

### ¿Qué pasa si hay múltiples señales activas?

Todas se cierran automáticamente en precio actual y se envía una notificación por cada una.

### ¿Se puede desactivar el cierre automático temporalmente?

Sí, cambiar `AUTO_CLOSE_ON_EVENTS=none` en `.env` y reiniciar `signal_monitor.py`.

### ¿Se pierden las señales cerradas automáticamente?

No, quedan registradas en BD con estado `CERRADA_EVENTO_MACRO` y se puede consultar su historial.

### ¿El sistema cierra señales de otros timeframes (1H, 4H, 1D)?

Sí, cierra **todas** las señales activas independientemente del timeframe, ya que un evento FOMC afecta a todos los marcos temporales.

### ¿Se puede configurar diferentes acciones por timeframe?

No en esta versión. Futura mejora: permitir cerrar solo señales de scalping (5M, 15M) y dejar abiertas las de swing (4H, 1D).

---

## Mantenimiento

**Responsable:** Actualizar lista `EVENTOS_ALTO_IMPACTO` en [services/economic_calendar.py](../services/economic_calendar.py) cada primer lunes del mes.

**Fuente:** https://www.forexfactory.com/calendar (filtrar: USD, Impact=High)

**Alerta:** Si el último evento listado es anterior a 45 días, el sistema emite warning en logs.

---

## Estado Actual

🟡 **Funcionalidad en diseño** — Requiere implementación

Para solicitar esta funcionalidad, revisar la lista de tareas en la sección "Implementación Paso a Paso" arriba.
