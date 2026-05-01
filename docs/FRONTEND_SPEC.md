# Dashboard Frontend — Especificación Funcional

## 1. Visión General

Panel web en tiempo real que muestra el estado del bot por módulo de timeframe.
Backend: Flask (extensión del `app.py` existente).
Frontend: HTML/CSS/JS puro (sin frameworks pesados) servido por Flask — sin dependencias npm.

---

## 2. Stack Técnico

| Capa | Tecnología | Justificación |
|---|---|---|
| Backend | Flask + Jinja2 | Ya existe, sin instalación extra |
| API datos | Nuevos endpoints `/api/v1/*` en `api/routes.py` | Reutiliza `DatabaseManager` y `tf_bias` existentes |
| Frontend | HTML5 + TailwindCSS CDN + Alpine.js CDN | Sin build step, carga desde CDN |
| Actualización | `setInterval` polling cada 30s | Consistente con el tick del monitor |
| Gráficas | Chart.js CDN | Ligero, suficiente para equity curve y barras |

---

## 3. Estructura de Páginas / Módulos

El dashboard tiene **una sola página** con 6 paneles colapsables, uno por timeframe:

```
┌─────────────────────────────────────────────────────────┐
│  🤖 BotTrading Dashboard          [último update: 14:32] │
│  Estado: 🟢 ONLINE   Threads: 7/7 vivos                  │
│  Evento macro: ⚠️ FOMC en 2h 15min                       │
├──────┬──────┬──────┬──────┬──────┬──────────────────────┤
│  1W  │  1D  │  4H  │  1H  │ 15M  │  5M                  │
└──────┴──────┴──────┴──────┴──────┴──────────────────────┘
[Panel activo expandido — ver sección 4]
```

---

## 4. Contenido de Cada Panel de Timeframe

Cada panel muestra la misma estructura adaptada al TF:

### 4.1 Cabecera del panel
- **Sesgo actual** (BULLISH 🟢 / BEARISH 🔴 / NEUTRAL ⚪) — fuente: `tf_bias`
- **Score** del último análisis
- **Tiempo desde último análisis**

### 4.2 Señal activa (si existe)
```
┌─────────────────────────────────────┐
│ GOLD 1H  ● COMPRA             ACTIVA│
│ Entrada:  $3.240,50                 │
│ TP1: $3.268   TP2: $3.295   TP3: – │
│ SL:  $3.215                        │
│ Progreso TP1: ████░░ 67%           │
│ Duración: 2h 18min                  │
└─────────────────────────────────────┘
```

Datos: `db.obtener_senales_activas()` filtrado por `timeframe`.

### 4.3 Canal / Cuña detectada
- `canal_bajista_roto` / `canal_alcista_roto`: badge con precio de ruptura
- `cuña_desc` / `cuña_asc` en compresión o ruptura
- Fuente: `tf_bias.obtener_canal_1h()`, `obtener_canal_4h()`, etc.

### 4.4 Historial reciente (últimas 5 señales del TF)
Tabla compacta:

| Fecha | Dir | Entry | Resultado | P&L% |
|---|---|---|---|---|
| 30/04 14:00 | COMPRA | 3.210 | TP1 ✅ | +1.8% |
| 30/04 09:15 | VENTA | 3.255 | SL ❌ | -1.0% |

### 4.5 Stats del TF (últimos 30 días)
- Win rate (%)
- Señales totales
- P&L acumulado (%)
- Racha actual (wins / losses consecutivos)

---

## 5. Sección Global (fuera de los paneles TF)

### 5.1 Barra de estado superior
- Estado sistema (ONLINE / degradado)
- Threads vivos / totales → fuente: `/cron` endpoint existente
- Próximo evento macro → fuente: `economic_calendar.proximos_eventos()`
- Precio XAUUSD en tiempo real → fuente: `db.obtener_precio_reciente_bd()`

### 5.2 Equity Curve (gráfica)
- Línea de P&L acumulado de todas las señales cerradas (últimos 30 días)
- Eje X: fecha; Eje Y: % acumulado
- Chart.js `line` chart

### 5.3 Tabla de señales activas (todas)
- Todas las señales ACTIVA / PENDIENTE_CONFIRM de todos los TF
- Columnas: `#id`, `asset`, `timeframe`, `dirección`, `entrada`, `precio actual`, `progreso TP1`, `duración`, `score`
- Fila resaltada en naranja si hay evento macro próximo (< 30 min)

### 5.4 Stats globales
- Win rate global
- Mejor TF (win rate)
- Peor TF
- Señales hoy / semana / mes

### 5.5 Panel de API Keys (Twelve Data)
- Tabla: alias | peticiones hoy | límite | % uso | estado (verde/amarillo/rojo)
- Fuente: `db.obtener_uso_keys_hoy()`

---

## 6. Nuevos Endpoints API necesarios

Todos bajo `/api/v1/`. Requieren `X-Cron-Token` header (usa el token existente).

| Endpoint | Método | Descripción | Fuente |
|---|---|---|---|
| `/api/v1/status` | GET | Estado threads + evento macro | `estado_sistema` + `proximos_eventos()` |
| `/api/v1/senales/activas` | GET | Señales activas con precio actual | `obtener_senales_activas()` |
| `/api/v1/senales/historial` | GET | Últimas N señales cerradas (param: `?tf=1H&limit=20`) | Query directa a BD |
| `/api/v1/stats/global` | GET | Win rate, P&L acumulado, racha | `obtener_estadisticas_periodo()` |
| `/api/v1/stats/por_tf` | GET | Stats por timeframe | `obtener_win_rate_por_simbolo()` agrupado |
| `/api/v1/stats/equity_curve` | GET | Serie temporal de P&L para la gráfica | Query directa a BD |
| `/api/v1/bias` | GET | Sesgo y canal de todos los TF | `tf_bias._bias_store` + canal stores |
| `/api/v1/precio/:symbol` | GET | Precio reciente de un activo | `obtener_precio_reciente_bd()` |
| `/api/v1/keys/uso` | GET | Uso de API keys hoy | `obtener_uso_keys_hoy()` |
| `/api/v1/macro/proximos` | GET | Próximos eventos del calendario | `proximos_eventos()` |

---

## 7. Nuevos Archivos a Crear

```
BotTrading/
├── api/
│   └── routes.py          ← MODIFICAR: añadir los 10 endpoints nuevos
├── frontend/
│   ├── templates/
│   │   └── dashboard.html  ← template Jinja2 principal
│   └── static/
│       ├── css/
│       │   └── dashboard.css
│       └── js/
│           └── dashboard.js ← lógica de polling y renderizado
```

La ruta `/` del Flask pasará a servir `dashboard.html` (o `/dashboard` si se prefiere mantener `/` como health check).

---

## 8. Datos Disponibles en BD (tabla `senales`)

Columnas relevantes para el frontend:

| Columna | Tipo | Uso en UI |
|---|---|---|
| `id` | INT | Identificador señal |
| `timestamp` | TEXT | Fecha/hora de emisión |
| `timestamp_entry` | TEXT | Timestamp de la vela de entrada |
| `asset` | TEXT | GOLD, BTC, SPX |
| `timeframe` | TEXT | 5M, 15M, 1H, 4H, 1D |
| `simbolo` | TEXT | XAUUSD_1H (legacy, siempre presente) |
| `direccion` | TEXT | COMPRA / VENTA |
| `precio_entrada` | REAL | Entry price |
| `tp1/tp2/tp3` | REAL | Objetivos de precio |
| `sl` | REAL | Stop loss |
| `score` | INT | Score del detector (0-20+) |
| `estado` | TEXT | ACTIVA / TP1 / TP2 / TP3 / SL / CANCELADA / CERRADA_EVENTO_MACRO |
| `beneficio_final_pct` | REAL | P&L en % al cerrar |
| `duracion_minutos` | INT | Duración total de la señal |
| `indicadores` | JSON | Objeto con los indicadores activos al abrir |
| `patron_velas` | TEXT | Nombre del patrón detectado |

---

## 9. Autenticación del Dashboard

- Mismo `CRON_TOKEN` del `.env` → header `X-Cron-Token` para los endpoints API
- La página HTML es pública (o protegida con HTTP Basic Auth si se añade `DASHBOARD_PASSWORD` al `.env`)
- Opción recomendada para Render: añadir `DASHBOARD_PASSWORD` y proteger con Flask `@before_request`

---

## 10. Consideraciones de Despliegue (Render)

- Los archivos estáticos se sirven con `Flask.static_folder='frontend/static'`
- Los templates con `Flask.template_folder='frontend/templates'`
- Sin proceso de build (todo CDN) → sin cambios en `Dockerfile` / `render.yaml`
- El polling del frontend (30s) no añade carga significativa al servidor

---

## 11. Orden de Implementación Sugerido

1. **Endpoints API** (`api/routes.py`) — backend primero, testeable desde `curl`
2. **Template base** (`dashboard.html`) — estructura HTML + cabecera de estado
3. **Panel de señales activas** — el más urgente operativamente
4. **Panel por TF** — sesgo + historial + stats por módulo
5. **Equity curve** — gráfica Chart.js con los datos de la BD
6. **Panel API Keys** — monitorización de cuota Twelve Data
7. **Autenticación** (opcional, si se despliega en Render público)
