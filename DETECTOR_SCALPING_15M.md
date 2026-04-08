# Detector GOLD 15M - Scalping

## 🎯 Descripción
Detector especializado en **scalping** para XAUUSD (Oro) en timeframe de **15 minutos**.
Optimizado para capturar movimientos rápidos con alta frecuencia de análisis.

## ⚡ Características

### Timeframe
- **Intervalo**: 15 minutos
- **Frecuencia de análisis**: Cada 2 minutos
- **Período de datos**: 5 días (suficiente para indicadores de corto plazo)

### Objetivos de Ganancia (TPs)
- **TP1**: $30 (conservador, ~1%)
- **TP2**: $50 (medio, ~1.5%)
- **TP3**: $80 (agresivo, ~2.5%)

### Stop Loss
- **Método**: ATR dinámico
- **Multiplicador**: 1.5x ATR (más ajustado que swing trading)
- **Tipo**: Protección conservadora para minimizar pérdidas

### Sistema de Scoring
- **Score máximo**: 15 puntos
- **Niveles de señal**:
  - ⚡ **SCALP**: ≥3 puntos (señales rápidas)
  - ⚠️ **MEDIA**: ≥5 puntos (señales confiables)
  - 🔥 **FUERTE**: ≥8 puntos (señales de alta calidad)

### Indicadores Utilizados

#### Indicadores Técnicos
1. **RSI (9 períodos)** - Más sensible que RSI(14)
   - Sobreventa: ≤35
   - Sobrecompra: ≥65

2. **EMAs Rápidas**
   - EMA 5 (ultra rápida)
   - EMA 13 (confirmación)
   - EMA 50 (tendencia corto plazo)

3. **ATR (10 períodos)** - Volatilidad adaptativa
   - SL: 1.5x ATR
   - Más sensible a cambios de volatilidad

4. **ADX** - Fuerza de tendencia
   - ADX > 25: Tendencia fuerte

5. **Volumen**
   - Multiplier: 1.2x promedio
   - Confirma movimientos auténticos

#### Price Action Específica para Scalping
1. **Momentum de vela actual**
   - Cuerpo > 1.3x vela anterior

2. **Ruptura de máximos/mínimos recientes**
   - Últimas 5 velas analizadas

3. **Secuencia de velas**
   - 3 velas consecutivas en misma dirección

4. **Patrones de velas**
   - Envolvente alcista/bajista
   - Doji (indecisión)

### 🛡️ Protecciones y Gestión de Riesgo

#### Control de Pérdidas Consecutivas
- **Máximo**: 3 pérdidas consecutivas
- **Acción**: Pausar trading automáticamente
- **Reanudación**: Solo con señales FUERTES (≥8 puntos)

#### Anti-Spam
- No envía señales duplicadas en la misma vela
- Verifica BD para evitar señales recientes (1 hora)
- Solo alertas en velas nuevas con cambios significativos

### 📊 Ventajas del Scalping 15M

✅ **Mayor frecuencia de operaciones**
- Más oportunidades de trading
- Captura movimientos pequeños pero frecuentes

✅ **Menor exposición al mercado**
- Operaciones rápidas (minutos/horas)
- Menor riesgo de gaps nocturnos

✅ **Versatilidad**
- Funciona en mercados laterales y con tendencia
- Aprovecha volatilidad intradiaria

⚠️ **Consideraciones**
- Requiere mayor disciplina
- Spreads más significativos (porcentualmente)
- Más tiempo de monitoreo

## 🚀 Ejecución

### Ejecutar solo detector de scalping:
```bash
python run_scalping_15m.py
```

### Ejecutar con todos los detectores:
```bash
python run_detectors.py
```

## 📈 Rendimiento Esperado

### Win Rate Objetivo
- **Scalping general**: 55-65%
- **Señales MEDIA**: 65-75%
- **Señales FUERTE**: 75-85%

### Operaciones Esperadas
- **Por día**: 4-8 señales
- **Por semana**: 20-40 señales
- **Ratio señal/ruido**: Optimizado por score mínimo

### Risk:Reward
- **TP1**: ~2:1
- **TP2**: ~3:1
- **TP3**: ~4:1

## 📝 Formato de Mensajes Telegram

```
⚡ SCALP BUY — GOLD 15M SCALPING
━━━━━━━━━━━━━━━━━━━━
💰 Precio:     $3,285.40
📌 BUY LIMIT:  $3,280.50
🛑 Stop Loss:  $3,270.00
🎯 TP1: $3,330  R:R 2.1:1
🎯 TP2: $3,350  R:R 3.1:1
🎯 TP3: $3,380  R:R 4.2:1
━━━━━━━━━━━━━━━━━━━━
📊 Score: 7/15 | Calidad: ⚡ SCALP
📉 RSI: 38.2 | ADX: 28.5
⏱️ TF: 15M  📅 2026-04-08 14:30
```

## 🔧 Configuración Avanzada

### Ajustar Agresividad
En `detector_gold_15m.py`, línea ~60:
```python
'min_score_scalping': 3,  # Reducir = más señales (menos calidad)
                          # Aumentar = menos señales (más calidad)
```

### Ajustar Objetivos TP
```python
'tp1_compra': 3330.0,  # Modificar según volatilidad del mercado
'tp2_compra': 3350.0,
'tp3_compra': 3380.0,
```

### Ajustar Stop Loss
```python
'atr_sl_mult': 1.5,  # Aumentar = SL más amplio (menos stops)
                     # Reducir = SL más ajustado (más stops)
```

## 📊 Monitoreo

### Logs a revisar
- `📊 Score SELL/BUY`: Fuerza de la señal
- `📉 RSI | ADX`: Indicadores clave
- `⛔ Trading pausado`: Control de pérdidas activo
- `✅ Señal fuerte detectada`: Reanudación automática

### Base de Datos
- Símbolo guardado: `XAUUSD_15M`
- Timeframe: `15M`
- Tracking automático de TPs y SL
- Historial de todas las operaciones

## 🎯 Casos de Uso Óptimos

### Mejor momento para usar 15M Scalping:
- **Sesión de Londres**: 08:00-16:00 GMT (alta volatilidad oro)
- **Sesión de Nueva York**: 13:00-21:00 GMT (noticias USD)
- **Overlap London/NY**: 13:00-16:00 GMT (máxima liquidez)

### Evitar:
- **Sesión asiática**: Baja volatilidad, spreads altos
- **Viernes tarde**: Cierre semanal, movimientos erráticos
- **Festivos**: Liquidez reducida

## 📚 Referencias

- **RSI**: Wilder's Relative Strength Index (período adaptado)
- **EMA**: Exponential Moving Average (períodos de scalping)
- **ATR**: Average True Range (Wilder)
- **ADX**: Average Directional Index

## ⚙️ Integración con Sistema Completo

El detector se integra automáticamente con:
- ✅ **Base de datos Turso**: Tracking completo
- ✅ **Monitor de señales**: Notificaciones de TP/SL
- ✅ **Anti-spam**: No duplicados
- ✅ **Telegram**: Alertas instantáneas

---

**Versión**: 1.0
**Última actualización**: 2026-04-08
**Autor**: Sistema BotTrading
