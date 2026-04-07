# Detectores Organizados por Activo y Timeframe

## 📁 Estructura

```
detectors/
├── bitcoin/
│   ├── detector_bitcoin_1d.py  # Timeframe Diario (original)
│   └── detector_bitcoin_4h.py  # Timeframe 4 Horas (NUEVO ✨)
├── gold/
│   ├── detector_gold_1d.py     # Timeframe Diario
│   └── detector_gold_4h.py     # Timeframe 4 Horas (NUEVO ✨)
└── spx/
    ├── detector_spx_1d.py      # Timeframe Diario
    └── detector_spx_4h.py      # Timeframe 4 Horas (NUEVO ✨)
```

## 🎯 Timeframes Implementados

### Bitcoin
- ✅ **1D (Diario)** - Detector original
  - Frecuencia: Cada 14 minutos
  - Score mínimo: 3 (alerta), 7 (media), 10 (fuerte), 13 (máxima)
  - Indicadores: EMA 9/21, RSI 14, MACD 12/26/9, ATR 14
  
- ✅ **4H (4 Horas)** - NUEVO implementado
  - Frecuencia: Cada 7 minutos
  - Score mínimo: 5 (alerta), 9 (media), 12 (fuerte), 14 (máxima) - MÁS ESTRICTO
  - Indicadores ajustados: EMA 18/42, RSI 28, MACD 24/52/18, ATR 28
  - SL multiplier: 2.0x ATR (menos agresivo que 1D)
  - Interval yfinance: '4h' con period='90d'

### Gold
- ✅ **1D (Diario)** - Detector original
- ✅ **4H (4 Horas)** - NUEVO implementado
  - Frecuencia: Cada 7 minutos
  - Score mínimo: 5 (alerta), 9 (media), 12 (fuerte), 14 (máxima)
  - Indicadores ajustados: EMA 18/42, RSI 28, MACD 24/52/18, ATR 28
  - SL multiplier: 1.2x ATR (menos agresivo)

### SPX
- ✅ **1D (Diario)** - Detector original
- ✅ **4H (4 Horas)** - NUEVO implementado
  - Frecuencia: Cada 7 minutos
  - Score mínimo: 5 (alerta), 9 (media), 12 (fuerte), 14 (máxima)
  - Indicadores ajustados: EMA 18/42, RSI 28, MACD 24/52/18, ATR 28
  - SL multiplier: 1.6x ATR (menos agresivo)

## 🔧 Diferencias Clave 1D vs 4H

| Característica | 1D | 4H |
|----------------|----|----|
| **Revisión** | 14 min | 7 min |
| **Periodo datos** | 2y | 90d |
| **EMA rápida** | 9 | 18 |
| **EMA lenta** | 21 | 42 |
| **EMA trend** | 200 | 400 |
| **RSI** | 14 | 28 |
| **MACD** | 12/26/9 | 24/52/18 |
| **ATR** | 14 | 28 |
| **Bollinger** | 20 | 40 |
| **Score Alerta** | 3 | 5 |
| **Score Media** | 7 | 9 |
| **Score Fuerte** | 10 | 12 |
| **Score Máxima** | 13 | 14 |
| **SL multiplier** | 2.5x | 2.0x |
| **Señales/semana** | 1-2 | 3-5 |

## 🚀 Ejecución

### Individual (desarrollo/testing)

``Gold 4H
python detectors/gold/detector_gold_4h.py

# SPX 1D
python detectors/spx/detector_spx_1d.py

# SPX 4H
python detectors/spx/detector_spx_4h
python detectors/bitcoin/detector_bitcoin_1d.py

# Bitcoin 4H
python detectors/bitcoin/detector_bitcoin_4h.py

# Gold 1D
python detectors/gold/detector_gold_1d.py

# SPX 1D
python detectors/spx/detector_spx_1d.py
```

### Producción (app.py)
import detectors.gold.detector_gold_1d as detector_gold_1d
import detectors.gold.detector_gold_4h as detector_gold_4h
import detectors.spx.detector_spx_1d as detector_spx_1d
import detectors.spx.detector_spx_4h as detector_spx_4h

Los detectores se integran en `app.py` como hilos:

```python
import detectors.bitcoin.detector_bitcoin_1d as detector_bitcoin_1d
import detectors.bitcoin.detector_bitcoin_4h as detector_bitcoin_4h
# ...
```

## 📊 Volumen Esperado de Señales
3-5 | 4-7 |
| SPX | 1-2 | 3-5 | 4-7 |
| **TOTAL** | **3-6** | **9-15** | **12-2--------------|-------|
| Bitcoin | 1-2 | 3-5 | 4-7 |
| Gold | 1-2 | - | 1-2 |
| SPX | 1-2 | - | 1-2 |
| *x] Implementar Bitcoin 4H ✅
- [x] Implementar Gold 4H ✅
- [x] Implementar SPX 4H ✅
- [ ] Actualizar app.py para incluir todos los detectores 4H
- [ ] Evaluar rendimiento 4H durante 2 semanas
- [ ] Considerar implementación de timeframe 1H (solo si 4H tiene éxito y win rate > 60%)
- [ ] Implementar SPX 4H
- [ ] Evaluar rendimiento Bitcoin 4H durante 2 semanas
- [ ] Considerar implementación de timeframe 1H (solo si 4H tiene éxito)
- [ ] Actualizar app.py para incluir Bitcoin 4H

## 🗄️ Base de Datos

Los detectores 4H guardan señales con:
- Campo `timeframe`: '4H'
- Símbolo: `BTCUSD_4H` (añade sufijo)
- Version: `4H-v1.0`

## ⚠️ Consideraciones

- **Filtros más estrictos en 4H**: Evita ruido del mercado
- **Menor SL/TP**: Ajustado a la volatilidad del timeframe
- **Mayor frecuencia de análisis**: 7 min vs 14 min del 1D
- **Scoring más exigente**: Requiere 5+ puntos para señal mínima
