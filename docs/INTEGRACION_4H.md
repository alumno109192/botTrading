# Integración Detector Bitcoin 4H en app.py

## ✅ Completado

### 1. Estructura de Directorios
```
detectors/
├── bitcoin/
│   ├── detector_bitcoin_1d.py  ✅ Copiado
│   └── detector_bitcoin_4h.py  ✅ NUEVO (implementado)
├── gold/
│   ├── detector_gold_1d.py     ✅ Copiado
│   └── detector_gold_4h.py     ✅ NUEVO (implementado)
└── spx/
    ├── detector_spx_1d.py      ✅ Copiado
    └── detector_spx_4h.py      ✅ NUEVO (implementado)
```

### 2. Detector Bitcoin 4H - Cambios Implementados

| Parámetro | 1D (Original) | 4H (NUEVO) | Razón |
|-----------|---------------|------------|-------|
| **CHECK_INTERVAL** | 14 min | 7 min | Mayor frecuencia para velas 4H |
| **yfinance interval** | '1d' | '4h' | Datos de 4 horas |
| **yfinance period** | '2y' | '90d' | Suficiente historia |
| **EMA rápida** | 9 | 18 | 9D × 2 |
| **EMA lenta** | 21 | 42 | 21D × 2 |
| **EMA trend** | 200 | 400 | 200D × 2 |
| **RSI** | 14 | 28 | 14D × 2 |
| **MACD fast** | 12 | 24 | 12D × 2 |
| **MACD slow** | 26 | 52 | 26D × 2 |
| **MACD signal** | 9 | 18 | 9D × 2 |
| **ATR** | 14 | 28 | 14D × 2 |
| **Bollinger** | 20 | 40 | 20D × 2 |
| **SL multiplier** | 2.5x | 2.0x | Menos agresivo |
| **Score alerta** | 3 | 5 | Más estricto |
| **Score media** | 7 | 9 | Más estricto |
| **Score fuerte** | 10 | 12 | Más estricto |
| **Score máxima** | 13 | 14 | Más estricto |

### 3. Archivos Creados

- ✅ `detectors/bitcoin/detector_bitcoin_4h.py` - Detector completo
- ✅ `detectors/gold/detector_gold_4h.py` - Detector completo
- ✅ `detectors/spx/detector_spx_4h.py` - Detector completo
- ✅ `detectors/README.md` - Documentación de estructura
- ✅ `test_detector_4h.py` - Script de prueba (Bitcoin)

---

## 🧪 Prueba Local

Antes de integrar en producción, probar localmente:

```bash
# Opción 1: Prueba única Bitcoin (sin loop infinito)
python test_detector_4h.py

# Opción 2: Ejecutar detectores en modo producción
python detectors/bitcoin/detector_bitcoin_4h.py
python detectors/gold/detector_gold_4h.py
python detectors/spx/detector_spx_4h.py
```

**Nota**: Necesitas tener configurado `.env` con:
- `TELEGRAM_TOKEN`
- `TELEGRAM_CHAT_ID`
- `TURSO_DATABASE_URL` (opcional)
- `TURSO_AUTH_TOKEN` (opcional)

---

## 🔧 Integración en app.py

### Paso 1: Actualizar Imports

```python
# Imports de detectores (al inicio de app.py)
import detector_bitcoin  # Mantener por compatibilidad
import detector_gold
import detector_spx

# NUEVOS: Importar versiones organizadas
from detectors.bitcoin import detector_bitcoin_1d
from detectors.gold import detector_gold_4h
from detectors.spx import detector_spx_1d
from detectors.spx import detector_spx_4htcoin_4h
from detectors.gold import detector_gold_1d
from detectors.spx import detector_spx_1d
```

### Paso 2: Modificar iniciar_detectores()

Buscar la función `iniciar_detectores()` y añadir el nuevo hilo:

```python
def iniciar_detectores():
    global hilos_activos
    hilos = []

    # ──────────────────────────────────────
    # DETECTORES 1D (EXISTENTES)
    # ──────────────────────────────────────
    
    # Bitcoin 1D
    hilo_btc_1d = threading.Thread(
        target=ejecutar_detector,
        args=("DETECTOR BITCOIN 1D", detector_bitcoin_1d, "bitcoin_1d"),
        name="DetectorBitcoin1D",
        daemon=True
    )
    hilos.append(hilo_btc_1d)
    threads_detectores['bitcoin_1d'] = hilo_btc_1d

    # Gold 1D
    hilo_gold_1d = threading.Thread(
        target=ejecutar_detector,
        args=("DETECTOR GOLD 1D", detector_gold_1d, "gold_1d"),
        name="DetectorGold1D",
        daemon=True
    )
    hilos.append(hilo_gold_1d)
    threads_detectores['gold_1d'] = hilo_gold_1d

    # SPX 1D
    hilo_spx_1d = threading.Thread(
        target=ejecutar_detector,
        args=("DETECTOR SPX 1D", detector_spx_1d, "spx_1d"),
        name="DetectorSPX1D",
        daemon=True
    )
    hilos.append(hilo_spx_1d)
    threads_detectores['spx_1d'] = hilo_spx_1d

    # ──────────────────────────────────────
    # DETECTORES 4H (NUEVOS) ✨
    # ──────────────────────────────────────
    
    hilo_btc_4h = threading.Thread(
        target=ejecutar_detector,
        args=("DETECTOR BITCOIN 4H", detector_bitcoin_4h, "bitcoin_4h"),
        name="DetectorBitcoin4H",
        daemon=True
    )

    hilo_gold_4h = threading.Thread(
        target=ejecutar_detector,
        args=("DETECTOR GOLD 4H", detector_gold_4h, "gold_4h"),
        name="DetectorGold4H",
        daemon=True
    )
    hilos.append(hilo_gold_4h)
    threads_detectores['gold_4h'] = hilo_gold_4h

    hilo_spx_4h = threading.Thread(
        target=ejecutar_detector,
        args=("DETECTOR SPX 4H", detector_spx_4h, "spx_4h"),
        name="DetectorSPX4H",
        daemon=True
    )
    hilos.append(hilo_spx_4h)
    threads_detectores['spx_4h'] = hilo_spx_4h
    hilos.append(hilo_btc_4h)
    threads_detectores['bitcoin_4h'] = hilo_btc_4h

    # Iniciar todos los hilos
    fgold_4h': None,     # NUEVO ✨
    'spx_1d': None,
    'spx_4h': None,      # NUEVO ✨
        h.start()
        print(f"✅ {h.name} iniciado")
        sys.stdout.flush()

    hilos_activos = hilos
    return hilos
```

### Paso 3: Actualizar threads_detectores global

Añadir al diccionario global:

```python
threads_detectores = {
    'bitcoin_1d': None,
    'bitcoin_gold_4h": threads_detectores.get('gold_4h').is_alive() if threads_detectores.get('gold_4h') else False,          # NUEVO ✨
            "spx_1d": threads_detectores.get('spx_1d').is_alive() if threads_detectores.get('spx_1d') else False,
            "spx_4h": threads_detectores.get('spx_4h').is_alive() if threads_detectores.get('spx_4h') else False,            # NUEVO ✨
    'gold_1d': None,
    'spx_1d': None,
}
```

### Paso 4: Actualizar endpoint /status

Modificar el endpoint para mostrar nuevos detectores:

```python
@app.route('/status')
def status():
    info = {
        "status": "running",
        "uptime": time.time() - start_time,
        "detectores": {
            "bitcoin_1d": threads_detectores.get('bitcoin_1d').is_alive() if threads_detectores.get('bitcoin_1d') else False,
            "bitcoin_4h": threads_detectores.get('bitcoin_4h').is_alive() if threads_detectores.get('bitcoin_4h') else False,  # NUEVO ✨
            "gold_1d": threads_detectores.get('gold_1d').is_alive() if threads_detectores.get('gold_1d') else False,
            "spx_1d": threads_detectores.get('spx_1d').is_alive() if threads_detectores.get('spx_1d') else False,
        },
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
    return jsonify(info)
```

### Paso 5: Actualizar endpoint /cron

Modificar logging para incluir 4H:

```python
@app.route('/cron')
def cron_ping():
    print(f"\n{'='*60}")
    print(f"🔔 CRON PING recibido: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print(f"{'='*60}")
    
    # ... código existente ...
    
    status = []
    for nombre, hilo in threads_detectores.items():
        if hilo:
            estado = "🟢 ACTIVO" if hilo.is_alive() else "🔴 INACTIVO"
            status.append(f"{nombre}: {estado}")
    
    status_str = "\n   ".join(status)
    print(f"   📊 Estado hilos:\n   {status_str}")
    
    # ... resto del código ...
```

---

## 📊 Verificaciones Pre-Deploy

Antes de desplegar a Render:

- [ ] Probar localmente con `python test_detector_4h.py`
- [ ] Verificar que no hay errores de sintaxis
- [ ] Confirmar que recibe señales de Telegram (si hay condiciones)
- [ ] Verificar que guarda en BD correctamente (si Turso configurado)
- [ ] Revisar logs para confirmar periodicidad de 7 min

---

## 🚀 Deploy a Render

### 1. Commit y Pushes 4H para Bitcoin, Gold y SPX

- Estructura organizada detectors/{bitcoin,gold,spx}/
- Parámetros ajustados para timeframe 4H (periodos x2)
- Scoring más estricto (5/9/12/14) vs 1D
- SL ajustados: BTC 2.0x, Gold 1.2x, SPX 1.6xetector Bitcoin 4H

- Estructura organizada detectors/bitcoin/
- Parámetros ajustados para timeframe 4H
- Scoring más estricto (5/9/12/14)
- Documentación completa en detectors/README.md
"
git push origin main
```

### 2. Actualizar app.py (commit separado)
es 4H en app.py

- Importar detector_bitcoin_4h, detector_gold_4h, detector_spx_4h
- Añadir hilos bitcoin_4h, gold_4h, spx_4h en iniciar_detectores()
- Actualizar endpoints /status y /cron
- 6 detectores total: 3×1D + 3×4H
- Importar detector_bitcoin_4h
- Añadir hilo bitcoin_4h en iniciar_detectores()
- Actualizar endpoints /status y /cron
"
git push origin main
```

### 3. Verificar Deploy en Render

1. Ir a dashboard de Render
2. Esperar deploy automático
3. Verificar logs en tiempo real:
   - Buscar "DETECTOR BITCOIN 4H iniciado"
   - Confirmar análisis cada 7 minutos
4. Llamar a endpoints:
   - `https://tu-app.onrender.com/status` → Ver estado detectores
   - `https://tu-app.onrender.com/cron` → Ver logs de hilos

---

## 📈 Monitoreo Post-Deploy

✅ DETECTOR GOLD 4H iniciado
✅ DETECTOR SPX 4H iniciado
🔄 CICLO #1 - Iniciando análisis BITCOIN 4H
🔄 CICLO #1 - Iniciando análisis GOLD 4H
🔄 CICLO #1 - Iniciando análisis SPX 4H
📊 Score SELL: X/15 | Score BUY: Y/15
⏳ Esperando 7 minutos hasta el próximo análisis...
```

### Métricas esperadas

| Métrica | Valor Esperado |
|---------|----------------|
| Frecuencia de análisis | Cada 7 minutos |
| Detectores activos | 6 (3×1D + 3×4H) |
| Señales por día (4H) | 1-2 por activo |
| Señales por semana (4H) | 3-5 por activo |
| **Total señales/semana** | **12-21** (3-6 de 1D + 9-15 de 4H) |
| Score mínimo para alerta | 5 |
| Uptime hilos | >99% cada unoisis | Cada 7 minutos |
| Señales por día | 1-2 |
| Señales por semana | 3-5 |
| Score mínimo para alerta | 5 |
| Uptime hilo | >99% |

---

## ⚠️ Troubleshooting

### Error: "Import could not be resolved"
**Solución**: Error de linter, ignorar. Funcionará en runtime.

### Error: "No module named 'detectors'"
**Solución**: Ejecutar desde raíz del proyecto, no desde subdirectorios.

### Detector no envía señales, 'gold_4h', 'spx_4h'
2. Revisar logs de inicio de cada
1. Score actual vs umbrales (5/9/12/14)
2. Condiciones de mercado (puede no haber señales válidas)
3. Variables de entorno TELEGRAM correctas

### Hilo no aparece en /status
**Solución**: 
1. Verificar que `threads_detectores` incluye 'bitcoin_4h'
2. Revisar logs de inicio del hilo
3. Comprobar que no hay excepciones en `ejecutar_detector()`

---

## 📝 Próximos Pasos (Opcional)

Una vez validado Bitcoin 4H:

1. **Semana 1-2**: Monitorear rendimiento Bitcoin 4H
2. **Semana 3**: Analizar win rate y calidad de señales
3. **Si exitoso**: Implementar Gold 4H y SPX 4H
4. **Evaluar**: Timeframe 1H (solo {bitcoin,gold,spx}/`
- Detector Bitcoin 4H implementado
- Detector Gold 4H implementado
- Detector SPX 4H implementado
- Parámetros técnicos ajustados según TIMEFRAMES_MULTIPLES.md
- Scoring más estricto (5/9/12/14)
- Documentación completa

🔄 **Pendiente**:
- Integrar todos los detectores 4H en app.py
- Probar localmente
- Deploy a Render
- Monitoreo de 2 semanas

📊 **Resultado esperado**:
- **12-21 señales totales por semana** (1D + 4H combinados)
- 9-15 señales 4H por semana (3-5 por activo)
- Mayor frecuencia que solo
- Probar localmente
- Deploy a Render
- Monitoreo de 2 semanas

📊 **Resultado esperado**:
- 3-5 señales Bitcoin 4H por semana
- Mayor frecuencia que 1D sin sacrificar calidad
- Filtros estrictos evitan ruido del mercado
