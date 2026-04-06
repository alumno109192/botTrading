# 📊 Implementar Sistema de Tracking de Señales

## 🎯 Objetivo

Implementar un sistema completo de seguimiento de señales de trading que permita:
- ✅ Guardar todas las señales generadas
- ✅ Monitorear el progreso de cada señal (TP1, TP2, TP3, SL)
- ✅ Generar estadísticas de efectividad
- ✅ Análisis histórico de performance

---

## 📋 PASO 1: Diseñar la Base de Datos

### Opción A: SQLite (Recomendado para inicio)
**Ventajas:** Simple, sin servidor, archivo local, perfecto para un solo usuario
**Desventajas:** No soporta múltiples escrituras concurrentes a gran escala

### Opción B: PostgreSQL / MySQL
**Ventajas:** Robusto, escalable, múltiples usuarios
**Desventajas:** Requiere instalación y configuración de servidor

### ✅ Decisión recomendada: **Comenzar con SQLite**

---

## 📋 PASO 2: Definir Estructura de Datos

### Tabla: `senales`
```sql
CREATE TABLE senales (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp DATETIME NOT NULL,
    simbolo VARCHAR(20) NOT NULL,           -- BTCUSD, XAUUSD, SPX500
    direccion VARCHAR(10) NOT NULL,         -- COMPRA, VENTA
    precio_entrada DECIMAL(12, 2) NOT NULL,
    precio_actual DECIMAL(12, 2),
    
    -- Niveles objetivo
    tp1 DECIMAL(12, 2) NOT NULL,
    tp2 DECIMAL(12, 2) NOT NULL,
    tp3 DECIMAL(12, 2) NOT NULL,
    sl DECIMAL(12, 2) NOT NULL,
    
    -- Datos de la señal
    score INTEGER NOT NULL,
    indicadores TEXT,                        -- JSON con indicadores activados
    patron_velas TEXT,                       -- Patrón detectado
    
    -- Estado y seguimiento
    estado VARCHAR(20) DEFAULT 'ACTIVA',     -- ACTIVA, TP1, TP2, TP3, SL, CANCELADA
    tp1_alcanzado BOOLEAN DEFAULT FALSE,
    tp2_alcanzado BOOLEAN DEFAULT FALSE,
    tp3_alcanzado BOOLEAN DEFAULT FALSE,
    sl_alcanzado BOOLEAN DEFAULT FALSE,
    
    fecha_tp1 DATETIME,
    fecha_tp2 DATETIME,
    fecha_tp3 DATETIME,
    fecha_sl DATETIME,
    fecha_cierre DATETIME,
    
    -- Métricas
    max_beneficio_pct DECIMAL(8, 4),        -- Máximo % alcanzado
    beneficio_final_pct DECIMAL(8, 4),      -- % al cerrar
    duracion_minutos INTEGER,                -- Tiempo hasta cierre
    
    -- Metadatos
    notas TEXT,
    version_detector VARCHAR(20)
);
```

### Tabla: `historial_precios`
```sql
CREATE TABLE historial_precios (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    senal_id INTEGER NOT NULL,
    timestamp DATETIME NOT NULL,
    precio DECIMAL(12, 2) NOT NULL,
    distancia_tp1 DECIMAL(8, 4),
    distancia_tp2 DECIMAL(8, 4),
    distancia_tp3 DECIMAL(8, 4),
    distancia_sl DECIMAL(8, 4),
    FOREIGN KEY (senal_id) REFERENCES senales(id)
);
```

### Tabla: `estadisticas_diarias`
```sql
CREATE TABLE estadisticas_diarias (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha DATE NOT NULL UNIQUE,
    total_senales INTEGER DEFAULT 0,
    senales_tp1 INTEGER DEFAULT 0,
    senales_tp2 INTEGER DEFAULT 0,
    senales_tp3 INTEGER DEFAULT 0,
    senales_sl INTEGER DEFAULT 0,
    win_rate DECIMAL(5, 2),
    beneficio_promedio DECIMAL(8, 4)
);
```

---

## 📋 PASO 3: Crear Módulo de Base de Datos

## URL PARA CONECTAR: libsql://senales-alumno109192.aws-eu-west-1.turso.io
## TOKEN: eyJhbGciOiJFZERTQSIsInR5cCI6IkpXVCJ9.eyJhIjoicnciLCJpYXQiOjE3NzU0NjcyNTIsImlkIjoiMDE5ZDYxZTEtOGIwMS03NWQ5LTk4YjEtZjAzYTFlOGU4ZWEzIiwicmlkIjoiZDkyNjBjYmUtYzU1MS00OWQyLTllZjQtYjk1YzNmZGY3N2Y0In0.beaHxywoycEPoxWA6k-jw9JtXMXYkVVbFIuRAdzPe9emMwqL2bwS8kJKXzjRakRZJUstJ8Tn4umjqOor26ruAA

### Archivo: `db_manager.py`

**Funcionalidades:**
```python
class DatabaseManager:
    def __init__(self, db_path='trading_signals.db')
    def crear_tablas()
    def guardar_senal(senal_data: dict) -> int
    def actualizar_estado_senal(senal_id: int, nuevo_estado: str)
    def registrar_precio(senal_id: int, precio_actual: float)
    def obtener_senales_activas() -> list
    def obtener_estadisticas(fecha_inicio, fecha_fin) -> dict
    def cerrar_senal(senal_id: int, estado_final: str)
```

**Métodos críticos:**
- `guardar_senal()`: Inserta nueva señal cuando se detecta
- `actualizar_estado_senal()`: Cambia estado cuando se alcanza TP o SL
- `registrar_precio()`: Guarda snapshots de precio para análisis
- `obtener_senales_activas()`: Lista señales que aún no cerraron

---

## 📋 PASO 4: Modificar Detectores

### Cambios en `detector_bitcoin.py`, `detector_gold_copy.py`, `detector_spx_copy.py`

#### 4.1 Importar módulo DB
```python
from db_manager import DatabaseManager

# Inicializar en main()
db = DatabaseManager()
```

#### 4.2 Guardar señal al detectar
```python
def enviar_telegram_alert(df, simbolo, direccion, score, ...):
    # ... código existente de formato mensaje ...
    
    # NUEVO: Guardar en base de datos
    senal_data = {
        'timestamp': datetime.now(),
        'simbolo': simbolo,
        'direccion': direccion,
        'precio_entrada': precio_actual,
        'tp1': tp1,
        'tp2': tp2,
        'tp3': tp3,
        'sl': sl,
        'score': score,
        'indicadores': json.dumps(indicadores_activados),
        'patron_velas': patron_detectado,
        'version_detector': '2.0'
    }
    
    senal_id = db.guardar_senal(senal_data)
    print(f"✅ Señal guardada con ID: {senal_id}")
```

#### 4.3 Evitar duplicados
```python
# Verificar si ya existe señal activa reciente (últimas 2 horas)
if db.existe_senal_reciente(simbolo, direccion, horas=2):
    return  # No enviar duplicado
```

---

## 📋 PASO 5: Crear Monitor de Señales

### Archivo: `signal_monitor.py`

**Propósito:** Revisar constantemente las señales activas y actualizar su estado

```python
import time
from db_manager import DatabaseManager
import yfinance as yf

def monitor_senales():
    """
    Revisa cada 5 minutos todas las señales activas
    y verifica si han alcanzado TP1, TP2, TP3 o SL
    """
    db = DatabaseManager()
    
    while True:
        senales_activas = db.obtener_senales_activas()
        
        for senal in senales_activas:
            # Obtener precio actual
            precio_actual = obtener_precio_actual(senal['simbolo'])
            
            # Registrar en historial
            db.registrar_precio(senal['id'], precio_actual)
            
            # Verificar niveles alcanzados
            if senal['direccion'] == 'COMPRA':
                if precio_actual >= senal['tp3']:
                    db.actualizar_estado_senal(senal['id'], 'TP3')
                    enviar_notificacion_telegram(f"🎯 TP3 alcanzado: {senal['simbolo']}")
                elif precio_actual >= senal['tp2']:
                    db.actualizar_estado_senal(senal['id'], 'TP2')
                elif precio_actual >= senal['tp1']:
                    db.actualizar_estado_senal(senal['id'], 'TP1')
                elif precio_actual <= senal['sl']:
                    db.actualizar_estado_senal(senal['id'], 'SL')
                    enviar_notificacion_telegram(f"❌ SL activado: {senal['simbolo']}")
            
            # Lógica similar para VENTA
            elif senal['direccion'] == 'VENTA':
                # ... invertir comparaciones ...
        
        time.sleep(300)  # Revisar cada 5 minutos

if __name__ == '__main__':
    monitor_senales()
```

---

## 📋 PASO 6: Dashboard de Estadísticas

### Archivo: `stats_dashboard.py`

**Funcionalidades:**
```python
def generar_reporte_diario():
    """Estadísticas del día"""
    
def generar_reporte_semanal():
    """Estadísticas de la semana"""
    
def calcular_win_rate(periodo='all'):
    """% de señales ganadoras"""
    
def mejor_peor_simbolo():
    """Qué instrumento tiene mejor/peor performance"""
    
def mejor_peor_hora():
    """A qué hora del día salen mejores señales"""
    
def promedio_duracion():
    """Cuánto tarda en promedio una señal en cerrar"""
    
def graficar_curva_equity():
    """Gráfico de ganancia acumulada"""
```

### Cálculos clave:
```python
win_rate = (TP1 + TP2 + TP3) / Total_Señales * 100

beneficio_promedio = sum(beneficio_pct) / total_señales

expectancy = (win_rate * avg_win) - (loss_rate * avg_loss)
```

---

## 📋 PASO 7: Automatización Completa

### Actualizar `run_detectors.py`

```python
import threading
from signal_monitor import monitor_senales

# Agregar hilo para monitor
hilo_monitor = threading.Thread(
    target=monitor_senales,
    name="SignalMonitor",
    daemon=True
)
hilo_monitor.start()
```

---

## 📋 PASO 8: Reportes por Telegram

### Módulo: `telegram_reports.py`

**Comandos:**
- `/stats` - Estadísticas del día
- `/stats_week` - Estadísticas semanal
- `/senales_activas` - Lista señales abiertas
- `/mejor_simbolo` - Mejor instrumento

**Implementación:**
```python
def enviar_reporte_diario():
    """
    Envía automáticamente cada día a las 23:00
    un resumen de las señales del día
    """
    stats = db.obtener_estadisticas_dia()
    
    mensaje = f"""
📊 REPORTE DIARIO - {fecha}
━━━━━━━━━━━━━━━━━━━━
🎯 Total señales: {stats['total']}
✅ TP1: {stats['tp1']} | TP2: {stats['tp2']} | TP3: {stats['tp3']}
❌ SL: {stats['sl']}
📈 Win Rate: {stats['win_rate']:.1f}%
💰 Beneficio promedio: {stats['avg_profit']:.2f}%
⏱️ Duración promedio: {stats['avg_duration']} min

🏆 Mejor símbolo: {stats['mejor_simbolo']}
    """
    
    enviar_telegram(mensaje)
```

---

## 📋 PASO 9: Validación y Testing

### Tests a implementar:

1. **Test de inserción:**
   - Crear señal de prueba
   - Verificar que se guarda correctamente

2. **Test de actualización:**
   - Simular precio alcanzando TP1
   - Verificar cambio de estado

3. **Test de estadísticas:**
   - Crear 10 señales de prueba (mix TP y SL)
   - Verificar cálculo de win_rate

4. **Test de duplicados:**
   - Intentar guardar 2 señales idénticas
   - Verificar que se rechaza la segunda

---

## 📋 PASO 10: Optimizaciones Futuras

### Fase 2:
- ✅ Web dashboard con Flask/FastAPI
- ✅ Gráficos interactivos (Plotly)
- ✅ Alertas personalizadas
- ✅ Backtesting automático
- ✅ ML para predecir probabilidad de éxito
- ✅ Exportar a Excel/CSV

### Fase 3:
- ✅ Integración con broker (ejecución automática)
- ✅ Gestión de riesgo automática
- ✅ Portfolio balancing

---

## 🔧 Dependencias Adicionales

### Actualizar `requirements.txt`:
```
Flask==3.0.2
requests==2.31.0
python-dotenv==1.0.1
yfinance>=0.2.0
pandas>=2.0.0
numpy>=1.24.0
# NUEVAS:
sqlite3  # (viene con Python)
plotly>=5.18.0         # Para gráficos
tabulate>=0.9.0        # Para tablas en consola
schedule>=1.2.0        # Para tareas programadas
```

---

## 📁 Estructura de Archivos Final

```
BotTrading/
├── detector_bitcoin.py
├── detector_gold.py
├── detector_spx.py
├── run_detectors.py           # Ejecutor principal
├── db_manager.py              # ⭐ NUEVO - Gestión BD
├── signal_monitor.py          # ⭐ NUEVO - Monitor de señales
├── stats_dashboard.py         # ⭐ NUEVO - Estadísticas
├── telegram_reports.py        # ⭐ NUEVO - Reportes
├── trading_signals.db         # ⭐ NUEVO - Base de datos
├── requirements.txt
├── .env
└── README.md
```

---

## 🚀 Orden de Implementación Recomendado

### Semana 1:
1. ✅ Crear `db_manager.py` con estructura básica
2. ✅ Implementar métodos de inserción y consulta
3. ✅ Modificar detectores para guardar señales

### Semana 2:
4. ✅ Crear `signal_monitor.py`
5. ✅ Testear actualización automática de estados
6. ✅ Integrar monitor en `run_detectors.py`

### Semana 3:
7. ✅ Implementar `stats_dashboard.py`
8. ✅ Crear reportes básicos
9. ✅ Agregar exportación a CSV

### Semana 4:
10. ✅ Implementar `telegram_reports.py`
11. ✅ Comandos interactivos
12. ✅ Reporte automático diario

---

## ⚠️ Consideraciones Importantes

### 1. **Timestamp y Timezones**
Usar UTC en base de datos y convertir a local solo en visualización:
```python
from datetime import datetime, timezone
timestamp = datetime.now(timezone.utc)
```

### 2. **Gestión de errores**
```python
try:
    db.guardar_senal(data)
except Exception as e:
    log_error(f"Error guardando señal: {e}")
    # No detener el detector por un error de BD
```

### 3. **Backup automático**
```python
import shutil
from datetime import datetime

def backup_database():
    fecha = datetime.now().strftime('%Y%m%d_%H%M%S')
    shutil.copy('trading_signals.db', f'backups/db_{fecha}.db')
```

### 4. **Límite de señales activas**
Para evitar sobrecarga, limitar a máximo 50 señales activas simultaneas:
```python
if len(db.obtener_senales_activas()) >= 50:
    # Cerrar la más antigua automáticamente
    db.cerrar_senal_mas_antigua()
```

---

## 📊 Ejemplo de Consulta SQL para Análisis

```sql
-- Win rate por símbolo
SELECT 
    simbolo,
    COUNT(*) as total,
    SUM(CASE WHEN estado IN ('TP1','TP2','TP3') THEN 1 ELSE 0 END) as wins,
    ROUND(100.0 * SUM(CASE WHEN estado IN ('TP1','TP2','TP3') THEN 1 ELSE 0 END) / COUNT(*), 2) as win_rate
FROM senales
WHERE estado != 'ACTIVA'
GROUP BY simbolo;

-- Mejores indicadores
SELECT 
    indicadores,
    COUNT(*) as veces_usado,
    AVG(score) as score_promedio,
    SUM(CASE WHEN estado IN ('TP1','TP2','TP3') THEN 1 ELSE 0 END) as wins
FROM senales
GROUP BY indicadores
ORDER BY wins DESC;

-- Señales por hora del día
SELECT 
    strftime('%H', timestamp) as hora,
    COUNT(*) as total_senales,
    AVG(beneficio_final_pct) as avg_profit
FROM senales
GROUP BY hora
ORDER BY hora;
```

---

## ✅ Checklist de Implementación

- [ ] **PASO 1:** Decidir tipo de base de datos
- [ ] **PASO 2:** Crear script SQL de tablas
- [ ] **PASO 3:** Implementar `db_manager.py`
- [ ] **PASO 4:** Modificar detectores para guardar
- [ ] **PASO 5:** Crear `signal_monitor.py`
- [ ] **PASO 6:** Implementar `stats_dashboard.py`
- [ ] **PASO 7:** Integrar todo en `run_detectors.py`
- [ ] **PASO 8:** Crear `telegram_reports.py`
- [ ] **PASO 9:** Testing completo
- [ ] **PASO 10:** Documentar y optimizar

---

## 📝 Notas Finales

Este sistema te permitirá:
- 📊 Analizar objetivamente qué señales funcionan mejor
- 🎯 Optimizar parámetros basándote en datos reales
- 📈 Mejorar continuamente la estrategia
- 💰 Calcular expectancy real del sistema

**Próximo paso:** Comenzar con PASO 1 y crear `db_manager.py` como base sólida.
