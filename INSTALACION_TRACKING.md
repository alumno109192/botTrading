# 🚀 Guía de Instalación - Sistema de Tracking de Señales

## ✅ Pasos Completados

Has completado exitosamente la implementación del sistema de tracking. Los siguientes archivos han sido creados:

### 📁 Archivos Nuevos Creados:
- ✅ `db_manager.py` - Gestión de base de datos Turso
- ✅ `signal_monitor.py` - Monitor automático de señales
- ✅ `stats_dashboard.py` - Dashboard de estadísticas
- ✅ `detector_bitcoin.py` - Modificado para guardar señales
- ✅ `run_detectors.py` - Actualizado con monitor

---

## 📋 Pasos para Completar la Instalación

### 1. Instalar Nuevas Dependencias

```powershell
.\venv\Scripts\python.exe -m pip install libsql-client plotly tabulate schedule
```

O simplemente:

```powershell
.\venv\Scripts\python.exe -m pip install -r requirements.txt
```

### 2. Configurar Variables de Entorno

Agrega las siguientes líneas a tu archivo `.env`:

```env
# Base de Datos Turso
TURSO_DATABASE_URL=libsql://tu-database-url.turso.io
TURSO_AUTH_TOKEN=tu_token_aqui
```

**¿Dónde obtener estos valores?**
- Visita: https://turso.tech/
- Crea una cuenta gratis
- Crea una nueva base de datos
- Copia el `Database URL` y `Auth Token`

### 3. Verificar Tablas en Turso

Las tablas ya deberían estar creadas en tu base de datos Turso. Si necesitas crearlas manualmente, ejecuta el SQL que se encuentra en `IMPLEMENTAR_TRACKING_SENALES.md` (Paso 2).

### 4. Probar Conexión a la Base de Datos

```powershell
.\venv\Scripts\python.exe db_manager.py
```

Deberías ver:
```
✅ Conexión a Turso establecida correctamente
✅ Test de conexión exitoso
📊 Señales activas: 0
```

### 5. Probar el Monitor de Señales (Opcional)

Para probar el monitor independientemente:

```powershell
.\venv\Scripts\python.exe signal_monitor.py
```

### 6. Probar el Dashboard de Estadísticas

```powershell
.\venv\Scripts\python.exe stats_dashboard.py
```

---

## 🚀 Ejecutar el Sistema Completo

Una vez todo configurado, ejecuta:

```powershell
.\venv\Scripts\python.exe run_detectors.py
```

El sistema ahora ejecutará:
- ✅ Detector de Gold (XAUUSD)
- ✅ Detector de SPX500
- ✅ Detector de Bitcoin (BTCUSD) - **CON GUARDADO EN DB**
- ✅ Monitor de señales activas (revisa cada 5 min)

---

## 📊 Cómo Funciona el Sistema

### Flujo de Trabajo:

1. **Detector encuentra señal** → Guarda en DB (tabla `senales`)
2. **Monitor revisa cada 5 min** → Verifica si precio alcanzó TP o SL
3. **Alcanza TP1/TP2/TP3/SL** → Actualiza estado y envía notificación Telegram
4. **Historial de precios** → Se guarda cada snapshot para análisis posterior

### Estados de Señales:

- `ACTIVA` - Señal abierta, esperando TP o SL
- `TP1` - Alcanzó primer objetivo
- `TP2` - Alcanzó segundo objetivo  
- `TP3` - Alcanzó tercer objetivo (cierra automáticamente)
- `SL` - Stop Loss activado (cierra automáticamente)
- `CANCELADA` - Cerrada manualmente o por antigüedad (>7 días)

---

## 🔧 Modificar Otros Detectores

**detector_bitcoin.py** ya está modificado. Para aplicar lo mismo a `detector_gold.py` y `detector_spx.py`:

### Cambios necesarios:

1. **Importar al inicio:**
```python
import json
from datetime import datetime, timezone
from db_manager import DatabaseManager

# Después de load_dotenv()
db = DatabaseManager()
```

2. **En las secciones de señal VENTA y COMPRA**, agregar antes de `enviar_telegram()`:

```python
# Verificar si ya existe señal reciente
if not db.existe_senal_reciente(simbolo, 'VENTA', horas=2):  # o 'COMPRA'
    
    # Crear datos de señal
    senal_data = {
        'timestamp': datetime.now(timezone.utc),
        'simbolo': simbolo,
        'direccion': 'VENTA',  # o 'COMPRA'
        'precio_entrada': sell_limit,  # o buy_limit
        'tp1': tp1_v,  # o tp1_c
        'tp2': tp2_v,
        'tp3': tp3_v,
        'sl': sl_venta,  # o sl_compra
        'score': score_sell,  # o score_buy
        'indicadores': json.dumps({
            'rsi': round(rsi, 1),
            'ema_fast': round(ema_fast, 2),
            # ... otros indicadores
        }),
        'patron_velas': 'Shooting Star' if shooting_star else '',
        'version_detector': '2.0'
    }
    
    # Guardar en DB
    try:
        senal_id = db.guardar_senal(senal_data)
        print(f"  💾 Señal guardada con ID: {senal_id}")
    except Exception as e:
        print(f"  ⚠️ Error guardando señal: {e}")
    
    # Enviar a Telegram
    enviar_telegram(msg)
    marcar_enviada(tipo_clave)
else:
    print(f"  ℹ️  Señal duplicada - No se guarda")
```

---

## 📊 Ver Estadísticas

### En consola:

```powershell
.\venv\Scripts\python.exe stats_dashboard.py
```

### Exportar a CSV:

```python
from stats_dashboard import StatsDashboard

dashboard = StatsDashboard()
dashboard.exportar_csv('mis_senales.csv', periodo_dias=30)
```

---

## 🔍 Consultas SQL Útiles

### Ver señales activas:
```sql
SELECT * FROM senales WHERE estado = 'ACTIVA';
```

### Win rate por símbolo:
```sql
SELECT 
    simbolo,
    COUNT(*) as total,
    SUM(CASE WHEN estado IN ('TP1','TP2','TP3') THEN 1 ELSE 0 END) as wins,
    ROUND(100.0 * SUM(CASE WHEN estado IN ('TP1','TP2','TP3') THEN 1 ELSE 0 END) / COUNT(*), 2) as win_rate
FROM senales
WHERE estado != 'ACTIVA'
GROUP BY simbolo;
```

### Señales del día:
```sql
SELECT * FROM senales 
WHERE DATE(timestamp) = DATE('now')
ORDER BY timestamp DESC;
```

---

## ⚠️ Solución de Problemas

### Error: "TURSO_DATABASE_URL y TURSO_AUTH_TOKEN deben estar definidos"

**Solución:** Verifica que agregaste las variables a tu archivo `.env`

### Error: "ModuleNotFoundError: No module named 'libsql_client'"

**Solución:** 
```powershell
.\venv\Scripts\python.exe -m pip install libsql-client
```

### Error al conectar a Turso

**Solución:** Verifica que:
1. Tu URL de Turso es correcta (debe empezar con `libsql://`)
2. Tu token de autenticación es válido
3. Tienes conexión a internet

### Las señales no se guardan

**Solución:**
1. Verifica que `db_manager.py` esté importado correctamente
2. Revisa los logs en consola para ver errores específicos
3. Verifica que las tablas existan en Turso

---

## 📈 Próximos Pasos

Una vez el sistema esté funcionando correctamente:

1. ✅ Monitorear durante 1 semana para acumular datos
2. ✅ Revisar estadísticas con `stats_dashboard.py`
3. ✅ Ajustar parámetros basándote en win rate real
4. ✅ Considerar implementar web dashboard (Fase 2)
5. ✅ Exportar datos regularmente como backup

---

## 📝 Notas Importantes

- El monitor revisa señales **cada 5 minutos**
- Los detectores revisan mercados **cada 14 minutos**
- Señales con más de **7 días** se cierran automáticamente
- Máximo **50 señales activas** simultáneas
- Sistema anti-duplicados: **2 horas** por defecto

---

## ✅ Checklist Final

- [ ] Dependencias instaladas (`libsql-client`, `plotly`, `tabulate`, `schedule`)
- [ ] Variables `TURSO_DATABASE_URL` y `TURSO_AUTH_TOKEN` en `.env`
- [ ] Tablas creadas en base de datos Turso
- [ ] Test de conexión exitoso (`python db_manager.py`)
- [ ] Sistema ejecutándose (`python run_detectors.py`)
- [ ] Primera señal guardada exitosamente en DB
- [ ] Monitor detectando y actualizando señales

**¡Una vez completado este checklist, el sistema estará completamente funcional! 🎉**
