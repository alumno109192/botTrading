# Implementación de Timeframes Múltiples (1H y 4H)

## 📊 Estado Actual

Actualmente el sistema opera exclusivamente en **timeframe 1D (diario)**:
- Análisis cada 14 minutos
- Señales basadas en velas diarias
- Indicadores configurados para timeframe diario
- 1-2 señales por activo por semana (baja frecuencia)

## 🎯 Objetivo

Implementar señales de **igual calidad** en timeframes menores:
- **1H (1 hora)**: Mayor frecuencia de señales, movimientos intradiarios
- **4H (4 horas)**: Balance entre frecuencia y fiabilidad
- **Mantener filtros de calidad** para evitar ruido

---

## 📐 Ajustes por Timeframe

### 1. Periodos de Indicadores

Los periodos de indicadores deben ajustarse proporcionalmente al timeframe:

| Indicador | 1D (Actual) | 4H | 1H | Justificación |
|-----------|-------------|-----|-----|---------------|
| **EMA Rápida** | 9 | 18 | 72 | Equivalente a ~9 días |
| **EMA Lenta** | 21 | 42 | 168 | Equivalente a ~21 días |
| **RSI** | 14 | 28 | 112 | Equivalente a ~14 días |
| **MACD (12,26,9)** | 12,26,9 | 24,52,18 | 96,208,72 | Proporcional x2 y x8 |
| **ADX** | 14 | 28 | 112 | Equivalente a ~14 días |
| **ATR** | 14 | 28 | 112 | Equivalente a ~14 días |
| **Bollinger** | 20, 2std | 40, 2std | 160, 2std | Proporcional |
| **OBV** | N/A | N/A | N/A | Sin ajuste (acumulativo) |

**Fórmula de conversión:**
```
Periodo_TF = Periodo_1D × (1440 / minutos_TF)
- 1H: Periodo_1D × 24 (simplificado a x8 para practicidad)
- 4H: Periodo_1D × 6 (simplificado a x2)
```

---

### 2. Ajustes en Stop Loss y Take Profits

El ATR debe ajustarse al timeframe para mantener proporciones:

#### **1D (Actual)**
```python
sl_compra = buy_limit - (2.5 * atr)
sl_venta = sell_limit + (2.5 * atr)
tp1 = 1.5 * riesgo
tp2 = 2.5 * riesgo
tp3 = 4.0 * riesgo
```

#### **4H (Recomendado)**
```python
atr_4h = calcular_atr(periodo=28)  # x2 del periodo 1D
sl_compra = buy_limit - (2.0 * atr_4h)  # Menos agresivo
sl_venta = sell_limit + (2.0 * atr_4h)
tp1 = 1.2 * riesgo  # Objetivos más cercanos
tp2 = 2.0 * riesgo
tp3 = 3.0 * riesgo
```

#### **1H (Recomendado)**
```python
atr_1h = calcular_atr(periodo=56)  # Periodo más largo
sl_compra = buy_limit - (1.5 * atr_1h)  # SL más ajustado
sl_venta = sell_limit + (1.5 * atr_1h)
tp1 = 1.0 * riesgo  # Objetivos muy cercanos
tp2 = 1.5 * riesgo
tp3 = 2.0 * riesgo
```

**Razonamiento:** Timeframes menores tienen más ruido → SL/TP más conservadores.

---

### 3. Scoring Ajustado

El sistema de puntuación debe ser **más estricto** en timeframes menores:

| Timeframe | Score Mínimo Alerta | Score Mínimo Señal Media | Score Mínimo Señal Fuerte | Score Máximo |
|-----------|---------------------|--------------------------|---------------------------|--------------|
| **1D** | 3 | 7 | 10 | 15 |
| **4H** | 5 | 9 | 12 | 15 |
| **1H** | 7 | 11 | 13 | 15 |

**Cambios en código:**
```python
# detector_bitcoin_4h.py
senal_sell_alerta = score_sell >= 5  # Más estricto que 1D
senal_sell_media = score_sell >= 9
senal_sell_fuerte = score_sell >= 12

# detector_bitcoin_1h.py
senal_sell_alerta = score_sell >= 7  # Muy estricto
senal_sell_media = score_sell >= 11
senal_sell_fuerte = score_sell >= 13
```

---

### 4. Frecuencia de Análisis

Ajustar intervalos de revisión según timeframe:

| Timeframe | Intervalo de Análisis | Justificación |
|-----------|----------------------|---------------|
| **1D** | 14 minutos (actual) | 1 vela = 24h, revisar frecuentemente |
| **4H** | 7 minutos | 1 vela = 4h, revisar 2x por hora |
| **1H** | 3 minutos | 1 vela = 1h, revisar 20x por hora |

```python
# detector_bitcoin_4h.py
CHECK_INTERVAL = 7 * 60  # 7 minutos

# detector_bitcoin_1h.py
CHECK_INTERVAL = 3 * 60  # 3 minutos
```

---

## 🔧 Implementación Práctica

### Paso 1: Crear Detectores por Timeframe

```bash
# Estructura de archivos
detector_bitcoin.py       # 1D (actual)
detector_bitcoin_4h.py    # 4H (nuevo)
detector_bitcoin_1h.py    # 1H (nuevo)

detector_gold.py          # 1D (actual)
detector_gold_4h.py       # 4H (nuevo)
detector_gold_1h.py       # 1H (nuevo)

detector_spx.py           # 1D (actual)
detector_spx_4h.py        # 4H (nuevo)
detector_spx_1h.py        # 1H (nuevo)
```

### Paso 2: Modificar parámetros en detector_bitcoin_4h.py

```python
# ══════════════════════════════════════
# PARÁMETROS 4H
# ══════════════════════════════════════

CHECK_INTERVAL = 7 * 60  # 7 minutos

# Descargar datos con intervalo 4h
df = yf.download(params['ticker_yf'], period='90d', interval='4h', progress=False)

# Indicadores ajustados
ema_fast = df['Close'].ewm(span=18).mean()  # 9D × 2 = 18
ema_slow = df['Close'].ewm(span=42).mean()  # 21D × 2 = 42

# RSI con periodo ajustado
rsi_period = 28  # 14D × 2

# MACD ajustado
macd_fast = 24   # 12D × 2
macd_slow = 52   # 26D × 2
macd_signal = 18 # 9D × 2

# ATR ajustado
atr_period = 28  # 14D × 2
atr = ta.atr(df['High'], df['Low'], df['Close'], length=atr_period)

# Bollinger Bands ajustadas
bb_period = 40  # 20D × 2
bb_std = 2

# Scoring más estricto
senal_sell_alerta = score_sell >= 5
senal_sell_media = score_sell >= 9
senal_sell_fuerte = score_sell >= 12
senal_sell_maxima = score_sell >= 14

# SL/TP ajustados
sl_venta = sell_limit + (2.0 * atr)  # Menos agresivo
tp1_v = int(sell_limit - (1.2 * (sl_venta - sell_limit)))
tp2_v = int(sell_limit - (2.0 * (sl_venta - sell_limit)))
tp3_v = int(sell_limit - (3.0 * (sl_venta - sell_limit)))
```

### Paso 3: Mensajes diferenciados por Timeframe

```python
# Añadir identificador de timeframe en mensajes
msg = (f"{nivel} — <b>BITCOIN 4H</b> {nivel.split()[0]}\n"  # ← Agregar "4H"
       f"━━━━━━━━━━━━━━━━━━━━\n"
       f"💰 <b>Precio:</b>     ${round(close, 0):,}\n"
       f"📌 <b>SELL LIMIT:</b> ${round(sell_limit, 0):,}\n"
       f"🛑 <b>Stop Loss:</b>  ${round(sl_venta, 0):,}\n"
       f"🎯 <b>TP1:</b> ${tp1_v:,}  R:R {rr(sell_limit, sl_venta, tp1_v)}:1\n"
       f"🎯 <b>TP2:</b> ${tp2_v:,}  R:R {rr(sell_limit, sl_venta, tp2_v)}:1\n"
       f"🎯 <b>TP3:</b> ${tp3_v:,}  R:R {rr(sell_limit, sl_venta, tp3_v)}:1\n"
       f"━━━━━━━━━━━━━━━━━━━━\n"
       f"📊 <b>Score:</b> {score_sell}/15  📉 <b>RSI:</b> {round(rsi, 1)}\n"
       f"⏱️ <b>TF:</b> 4H  📅 {fecha}")  # ← Cambiar "1D" a "4H"
```

### Paso 4: Integrar en app.py

```python
# Importar nuevos detectores
import detector_bitcoin_4h
import detector_bitcoin_1h
import detector_gold_4h
import detector_gold_1h
import detector_spx_4h
import detector_spx_1h

def iniciar_detectores():
    # ... código existente ...
    
    # Hilos para 4H
    hilo_btc_4h = threading.Thread(
        target=ejecutar_detector,
        args=("DETECTOR BITCOIN 4H", detector_bitcoin_4h, "bitcoin_4h"),
        name="DetectorBitcoin4H",
        daemon=True
    )
    hilos.append(hilo_btc_4h)
    threads_detectores['bitcoin_4h'] = hilo_btc_4h
    
    # Hilos para 1H
    hilo_btc_1h = threading.Thread(
        target=ejecutar_detector,
        args=("DETECTOR BITCOIN 1H", detector_bitcoin_1h, "bitcoin_1h"),
        name="DetectorBitcoin1H",
        daemon=True
    )
    hilos.append(hilo_btc_1h)
    threads_detectores['bitcoin_1h'] = hilo_btc_1h
    
    # ... iniciar threads ...
```

---

## 📈 Volumen de Señales Esperado

| Timeframe | Señales/Semana (Estimado) | Total con 3 activos | Observaciones |
|-----------|---------------------------|---------------------|---------------|
| **1D** | 1-2 | 3-6 | Actual (baja frecuencia) |
| **4H** | 3-5 | 9-15 | 6 velas por día |
| **1H** | 8-12 | 24-36 | 24 velas por día |
| **TOTAL** | 12-19 | **36-57** | Puede ser excesivo |

⚠️ **Riesgo de saturación:** 36-57 señales/semana puede ser abrumador.

---

## 🎯 Estrategia Recomendada

### Opción 1: Implementación Gradual
1. **Fase 1:** Implementar solo **4H** (balance frecuencia/calidad)
2. **Fase 2:** Evaluar rendimiento durante 2 semanas
3. **Fase 3:** Si funciona bien, agregar **1H** con filtros MUY estrictos

### Opción 2: Priorización de Señales
Usar un sistema de **ranking multi-timeframe**:

```python
# Prioridad de señales
prioridad = {
    '1D_MAXIMA': 1,    # Máxima prioridad
    '4H_MAXIMA': 2,
    '1D_FUERTE': 3,
    '1H_MAXIMA': 4,
    '4H_FUERTE': 5,
    '1D_MEDIA': 6,
    '1H_FUERTE': 7,
    '4H_MEDIA': 8,
    # ... etc
}

# Solo enviar a Telegram si:
# - Es prioridad 1-5, O
# - No hay señales de mayor prioridad en últimas 4 horas
```

### Opción 3: Diferentes Canales de Telegram
- **Canal 1D:** Señales de largo plazo (actuales)
- **Canal 4H:** Señales de medio plazo
- **Canal 1H:** Señales intradiarias (opcional)

---

## 🔍 Filtros Adicionales para Timeframes Menores

### 1. Confirmación de Timeframe Superior
```python
def confirmar_con_tf_superior(simbolo, direccion):
    """
    Verificar que el timeframe superior también esté alineado
    """
    # Si es señal 1H COMPRA
    df_4h = yf.download(simbolo, period='30d', interval='4h')
    ema_4h = calcular_ema(df_4h, 18)
    
    # Solo validar si 4H también es alcista
    if ema_4h[-1] > ema_4h[-2]:
        return True
    return False
```

### 2. Volumen Mínimo
```python
# Filtrar velas con bajo volumen (ruido)
volumen_promedio = df['Volume'].rolling(20).mean()
if df['Volume'].iloc[-1] < volumen_promedio.iloc[-1] * 0.7:
    print("⚠️ Volumen insuficiente - señal descartada")
    return  # No generar señal
```

### 3. Horario de Trading
```python
# Evitar señales en horarios de baja liquidez
hora_actual = datetime.now().hour

# Excluir fines de semana para Forex/Crypto
if datetime.now().weekday() >= 5:  # Sábado/Domingo
    return

# Excluir horarios nocturnos (baja liquidez)
if 1 <= hora_actual <= 6:  # 1AM - 6AM
    return
```

---

## 📊 Base de Datos: Ajustes Necesarios

### Agregar columna de timeframe

```sql
ALTER TABLE senales ADD COLUMN timeframe TEXT DEFAULT '1D';

-- Índice para consultas por timeframe
CREATE INDEX idx_timeframe ON senales(timeframe, timestamp);
```

### Modificar db_manager.py

```python
def guardar_senal(self, senal_data):
    # Agregar timeframe a la señal
    if 'timeframe' not in senal_data:
        senal_data['timeframe'] = '1D'  # Default
    
    # ... resto del código ...
```

---

## 🚀 Plan de Implementación

### **Semana 1: Fase de Preparación**
- [ ] Crear `detector_bitcoin_4h.py` con parámetros ajustados
- [ ] Adaptar scoring (mínimo 5 para alertas)
- [ ] Ajustar SL/TP (multiplicadores menores)
- [ ] Probar localmente con datos históricos

### **Semana 2: Deploy 4H**
- [ ] Integrar detector 4H en `app.py`
- [ ] Añadir columna `timeframe` en BD
- [ ] Desplegar en Render
- [ ] Monitorear volumen de señales durante 1 semana

### **Semana 3: Evaluación**
- [ ] Analizar calidad de señales 4H
- [ ] Calcular win rate por timeframe
- [ ] Decidir si implementar 1H

### **Semana 4: Expansión (Opcional)**
- [ ] Crear detectores 1H si resultados 4H son buenos
- [ ] Implementar sistema de priorización multi-TF
- [ ] Configurar canales de Telegram separados

---

## 📝 Checklist de Calidad

Antes de activar un nuevo timeframe, verificar:

- [x] Periodos de indicadores ajustados correctamente
- [x] Scoring más estricto que timeframe superior
- [x] SL/TP proporcionales al timeframe
- [x] Frecuencia de análisis adecuada
- [x] Filtros adicionales (volumen, horario)
- [x] Mensajes diferenciados por TF
- [x] Base de datos soporta timeframe
- [x] Probado con datos históricos

---

## 💡 Recomendación Final

**Comenzar solo con 4H:**
- Mejor balance entre frecuencia y calidad
- Menos riesgo de saturación
- Más fácil de gestionar manualmente
- Permite evaluar el sistema sin abrumar

**Implementar 1H solo si:**
- Win rate 4H > 60%
- Usuario puede gestionar 30+ señales/semana
- Sistema de priorización automática implementado

---

## 📚 Recursos Adicionales

### Archivos a crear:
- `detector_bitcoin_4h.py` (copia ajustada de `detector_bitcoin.py`)
- `detector_gold_4h.py`
- `detector_spx_4h.py`
- `TIMEFRAMES_CONFIG.md` (configuración específica por activo)

### Comandos útiles:
```bash
# Probar detector 4H localmente
python detector_bitcoin_4h.py

# Ver logs de 4H específicamente
grep "BITCOIN 4H" logs.txt

# Comparar señales 1D vs 4H
python compare_signals.py --tf1 1D --tf2 4H --days 7
```

---

**Última actualización:** Abril 2026  
**Versión:** 1.0  
**Estado:** Propuesta - Pendiente de implementación
