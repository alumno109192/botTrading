# 📊 Sistema de Tracking de Señales

Sistema automático de seguimiento de señales activas: TP/SL, estadísticas, historial de precios.

---

## 📋 Contenido

1. [Visión General](#visión-general)
2. [Componentes](#componentes)
3. [Flujo de Datos](#flujo-de-datos)
4. [Base de Datos](#base-de-datos)
5. [Monitor de Señales](#monitor-de-señales)
6. [Dashboard de Estadísticas](#dashboard-de-estadísticas)
7. [Ejemplos de Uso](#ejemplos-de-uso)

---

## Visión General

El sistema de tracking monitoriza **automáticamente** cada señal emitida:

1. **Guarda** la señal en base de datos Turso
2. **Revisa TP/SL** cada 5 minutos
3. **Notifica** cuando se alcance un nivel
4. **Registra** historial de precios
5. **Cierra** señales y calcula beneficio/pérdida

### Beneficios

- ✅ Sin intervención manual — totalmente automático
- ✅ Historial completo de todas las señales
- ✅ Estadísticas en tiempo real (win rate, profit factor, etc.)
- ✅ Base de datos en la nube (Turso) — accesible desde cualquier lugar
- ✅ Notificaciones Telegram cuando se alcanza TP o SL

---

## Componentes

### 1. db_manager.py — Gestión de BD Turso

**Responsabilidades:**
- Conexión a base de datos Turso (SQLite en la nube)
- Operaciones CRUD (Create, Read, Update, Delete)
- Singleton pattern para evitar múltiples conexiones

**Métodos principales:**

```python
class DatabaseManager:
    def guardar_senal(self, datos_senal):
        """Inserta nueva señal en la tabla signals."""
        
    def actualizar_senal(self, signal_id, estado, profit_pct=None):
        """Actualiza estado de señal (ACTIVA, CLOSED, CANCELADA)."""
        
    def obtener_senales_activas(self):
        """SELECT * FROM signals WHERE estado='ACTIVA'"""
        
    def registrar_precio(self, signal_id, precio_actual, timestamp):
        """Inserta en table price_history para tracking."""
        
    def obtener_estadisticas(self):
        """Calcula estadísticas agregadas (win rate, profit, etc.)."""
```

**Ejemplo de uso:**

```python
from db_manager import DatabaseManager

db = DatabaseManager()

# Guardar nueva señal
db.guardar_senal({
    'symbol': 'XAUUSD',
    'timeframe': '4H',
    'direction': 'SELL',
    'entry_price': 3320.50,
    'stop_loss': 3348.00,
    'tp1': 3272.00,
    'tp2': 3200.00,
    'tp3': 3100.00,
    'score': 14,
})

# Actualizar cuando se cierre
db.actualizar_senal(signal_id=123, estado='CLOSED', profit_pct=2.5)

# Obtener estadísticas
stats = db.obtener_estadisticas()
print(f"Win rate: {stats['win_rate']:.1f}%")
print(f"Profit total: {stats['total_profit']:.2f}%")
```

---

### 2. signal_monitor.py — Monitor Automático

**Responsabilidades:**
- Ejecuta cada 5 minutos en background
- Descarga precio actual de yfinance
- Compara con TP1, TP2, TP3, SL
- Actualiza base de datos
- Envía notificaciones a Telegram

**Flujo:**

```
1. SELECT * FROM signals WHERE estado='ACTIVA'
   │
2. Para cada señal:
   a. Descargar precio actual
   b. ¿Precio >= TP3?        → CERRAR como TP3 (100% ganancia)
   c. ¿Precio >= TP2?        → CERRAR como TP2
   d. ¿Precio >= TP1?        → CERRAR como TP1 (33% cierre)
   e. ¿Precio <= SL?         → CERRAR como SL (PÉRDIDA)
   f. ¿Antigüedad > 7 días?  → CANCELAR como EXPIRADA
   │
3. Si nivel alcanzado:
   a. Calcular profit/loss %
   b. UPDATE signals SET estado='CLOSED', profit_pct=X
   c. INSERT price_history con último precio
   d. Enviar notificación Telegram con consejo
```

**Ejemplo de notificación:**

```
🎯 TP1 ALCANZADO — GOLD (4H)
━━━━━━━━━━━━━━━━━━━━
Entrada: $3,320.00
TP1:     $3,272.00 ✅
Profit:  +1.45% (0.5 R:R)

💡 Acción recomendada:
   • Cerrar 33% de posición
   • Mover SL a breakeven
   • Dejar trailing stop para TP2
━━━━━━━━━━━━━━━━━━━━
📊 Tiempo: 2h 30m
```

**Configuración:**

```python
# En signal_monitor.py
CHECK_INTERVAL = 300         # 5 minutos
CLOSE_AFTER_DAYS = 7         # Cerrar como EXPIRADA después de 7 días
TP_PROFIT_TARGETS = {
    'TP1': 0.33,    # 33% de la posición
    'TP2': 0.33,    # 33% de la posición
    'TP3': 0.34,    # 34% de la posición
}
```

---

### 3. stats_dashboard.py — Estadísticas

**Responsabilidades:**
- Calcula estadísticas agregadas
- Genera reportes por símbolo, timeframe, hora del día
- Exporta a CSV

**Métricas principales:**

| Métrica | Descripción |
|---------|-------------|
| **Win Rate** | % de señales ganadoras |
| **Profit Factor** | Ganancias / Pérdidas |
| **Expectancy** | Ganancia promedio por operación |
| **Max Drawdown** | Mayor pérdida desde máximo |
| **Trades por día** | Promedio de operaciones |
| **Mejor hora** | Hora del día con más ganancias |

**Ejemplo de reporte:**

```
📊 ESTADÍSTICAS TRADING BOT
═══════════════════════════════════════════

RESUMEN GENERAL:
  Total señales:    245
  Cerradas:        189
  Activas:          34
  Expiradas:        22

WIN RATE:            62.4%  (118 ganadoras / 189)
PROFIT FACTOR:       2.8x   (Ganancias/Pérdidas)
EXPECTANCY:          +1.85% por operación
PROFIT TOTAL:        +349.5%

POR SÍMBOLO:
  XAUUSD:  Win 68% | Profit +4.2%  | 156 trades
  EURUSD:  Win 55% | Profit -0.8%  | 89 trades

POR TIMEFRAME:
  1D:      Win 75% | Profit +6.1%  | 45 trades
  4H:      Win 62% | Profit +2.1%  | 98 trades
  15M:     Win 48% | Profit -1.3%  | 46 trades

MEJOR HORA:
  13:00-14:00 UTC: Win 72% | +2.3% avg
  14:00-15:00 UTC: Win 68% | +1.9% avg
```

---

## Flujo de Datos

### A. Generación de Señal

```
detector_gold_4h.py
    │
    ├─ Calcular indicadores
    ├─ Analizar última vela
    ├─ Generar score
    │
    ├─ ✓ Score ≥ umbral?
    ├─ ✓ Filtros OK?
    ├─ ✓ Confluencia OK?
    │
    ├─ Enviar Telegram
    │
    └─ db_manager.guardar_senal()
           │
           └─ INSERT INTO signals VALUES (...)
                   Estado: ACTIVA
                   Timestamp: ahora
                   Toda la metadata de la operación
```

### B. Monitorización (cada 5 minutos)

```
signal_monitor.py
    │
    ├─ SELECT signals WHERE estado='ACTIVA'
    │
    ├─ Para cada señal:
    │  ├─ yfinance.download(precio_actual)
    │  ├─ ¿Precio alcanzó algún nivel?
    │  │
    │  ├─ SÍ → UPDATE signals SET estado='CLOSED'
    │  │       └─ INSERT price_history
    │  │       └─ Enviar Telegram notificación
    │  │
    │  └─ NO → registrar precio en price_history
    │
    └─ Sleep(5 minutos)
```

---

## Base de Datos

### Tabla: signals

```sql
CREATE TABLE signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    direction TEXT NOT NULL,  -- BUY, SELL
    entry_price REAL NOT NULL,
    stop_loss REAL NOT NULL,
    tp1 REAL NOT NULL,
    tp2 REAL NOT NULL,
    tp3 REAL NOT NULL,
    score INTEGER,
    signal_type TEXT,  -- ALERTA, MEDIA, FUERTE, MÁXIMA
    estado TEXT DEFAULT 'ACTIVA',  -- ACTIVA, CLOSED, CANCELADA
    profit_pct REAL,
    closing_reason TEXT,  -- TP1, TP2, TP3, SL, EXPIRADA
    closing_timestamp TEXT
);
```

### Tabla: price_history

```sql
CREATE TABLE price_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id INTEGER NOT NULL,
    price REAL NOT NULL,
    timestamp TEXT NOT NULL,
    FOREIGN KEY(signal_id) REFERENCES signals(id)
);
```

---

## Monitor de Señales

### Ejecución

**Modo 1: Autónomo**
```bash
python signal_monitor.py
```

**Modo 2: Con detectores (recomendado)**
```bash
python app.py
```

### Logs esperados

```
[14:30:15] 🔵 Iniciando Signal Monitor...
[14:35:20] 📊 Checando 12 señales activas...
[14:35:22]   ✅ XAUUSD-SELL (4H): precio $3,285 | +1.2% (TP1 alcanzado)
[14:35:23]   ➡️  EURUSD-BUY (1D): precio $1.095 | -0.5% (en rango)
[14:35:24] 💾 Actualizado 1 señal en BD
[14:35:25] 📱 Telegram: 1 notificación enviada
[14:35:26] ⏰ Próximo check en 5 minutos...
```

### Configuración de Umbrales

En `signal_monitor.py`:

```python
# Umbrales de cierre automático
TP_LEVELS = {
    'TP3': ('TP3 alcanzado', 1.0),     # 100% cierre
    'TP2': ('TP2 alcanzado', 0.33),    # 33% cierre
    'TP1': ('TP1 alcanzado', 0.33),    # 33% cierre
}

SL_THRESHOLD = 'SL alcanzado'  # Cierre total

EXPIRY_DAYS = 7  # Cerrar como EXPIRADA después de 7 días
```

---

## Dashboard de Estadísticas

### Ejecución

```bash
python stats_dashboard.py
```

### Visualizaciones

1. **Win Rate por Símbolo** — Gráfico de barras
2. **Profit Factor** — Métrica de rentabilidad
3. **Drawdown Máximo** — Mayor pérdida
4. **Distribución Horaria** — Mejor hora del día
5. **Correlación Indicadores** — Qué indicadores funcionan mejor

### Exportación CSV

```bash
# Genera estadisticas_YYYY-MM-DD_HH-MM-SS.csv
python stats_dashboard.py --export-csv
```

Columnas de salida:
```
timestamp,symbol,timeframe,direction,entry_price,profit_pct,closing_reason,duration_hours
```

---

## Ejemplos de Uso

### Ejemplo 1: Guardar y Monitorizar una Señal

```python
from db_manager import DatabaseManager
from telegram_utils import enviar_telegram

db = DatabaseManager()

# 1. Genera una señal
senal = {
    'symbol': 'XAUUSD',
    'timeframe': '4H',
    'direction': 'SELL',
    'entry_price': 3320.50,
    'stop_loss': 3348.00,
    'tp1': 3272.00,
    'tp2': 3200.00,
    'tp3': 3100.00,
    'score': 14,
}

# 2. Guarda en BD
signal_id = db.guardar_senal(senal)

# 3. Envía a Telegram
mensaje = f"🔴 SELL FUERTE — GOLD (4H)\n Entrada: ${senal['entry_price']}\n SL: ${senal['stop_loss']}"
enviar_telegram(mensaje)

# 4. Signal monitor revisa automáticamente cada 5 min
print(f"✅ Señal #{signal_id} guardada y monitoreada")
```

### Ejemplo 2: Obtener Estadísticas

```python
from db_manager import DatabaseManager

db = DatabaseManager()

# Estadísticas generales
stats = db.obtener_estadisticas()

# Estadísticas por símbolo
stats_gold = db.obtener_estadisticas_por_simbolo('XAUUSD')

# Estadísticas por timeframe
stats_1d = db.obtener_estadisticas_por_timeframe('1D')

# Mejores indicadores
mejores = db.obtener_mejores_indicadores()

print(f"Win rate: {stats['win_rate']:.1f}%")
print(f"Profit total: {stats['total_profit']:.2f}%")
print(f"Mejor símbolo: {mejores['best_symbol']} ({mejores['best_win_rate']:.1f}%)")
```

### Ejemplo 3: Diagnóstico de Señal Específica

```python
from db_manager import DatabaseManager

db = DatabaseManager()

# Obtener historial de una señal
signal_id = 42
senal = db.obtener_senal_por_id(signal_id)
historial = db.obtener_price_history_de_senal(signal_id)

print(f"Señal #{signal_id}")
print(f"  Entrada: ${senal['entry_price']}")
print(f"  Estado: {senal['estado']}")
print(f"  Profit: {senal['profit_pct']:.2f}%")
print(f"  Historial de precios:")
for registro in historial:
    print(f"    {registro['timestamp']}: ${registro['price']}")
```

---

## Troubleshooting

### ❌ "Connection refused to Turso"

**Causa:** Credenciales de Turso incorrectas

**Solución:**
```bash
# Verifica .env
python -c "import os; from dotenv import load_dotenv; load_dotenv(); print(os.getenv('TURSO_DATABASE_URL'))"
```

Debe mostrar: `libsql://xxxx-yyyy-zzzz.turso.io`

### ❌ "signal_monitor: No active signals"

**Causa:** No hay señales en estado ACTIVA

**Solución:** Ejecuta un detector individualmente:
```bash
python detectors/gold/detector_gold_1d.py
```

### ❌ "Telegram: No thread_id"

**Causa:** Señal guardada sin thread_id

**Solución:** Revisa que el detector envíe `thread_id=THREAD_ID` al llamar `enviar_telegram()`.

---

*Última actualización: 2026-04-18*
