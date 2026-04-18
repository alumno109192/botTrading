# Ejecutar Detectores

## Uso

### Opción 1: Ejecutar todos los detectores simultáneamente (RECOMENDADO)

```bash
.\venv\Scripts\python.exe run_detectors.py
```

Este comando ejecutará:
- 🥇 **detector_gold.py** - Análisis de XAUUSD (Oro)
- 📈 **detector_spx.py** - Análisis de SPX500 (S&P 500)

Ambos detectores se ejecutan en **hilos separados** y funcionan de forma independiente.

### Opción 2: Ejecutar detectores individuales

**Solo Oro:**
```bash
.\venv\Scripts\python.exe detector_gold.py
```

**Solo SPX500:**
```bash
.\venv\Scripts\python.exe detector_spx.py
```

## Características

- ⏱️ **Revisión cada 14 minutos** - Mantiene el servidor activo
- 🔕 **Sistema anti-spam** - Solo notifica en velas nuevas o cambios significativos
- 🔄 **Auto-reinicio** - Si un detector falla, se reinicia automáticamente
- 📊 **Análisis técnico completo** - RSI, EMA, ATR, patrones de velas
- 📱 **Notificaciones a Telegram** - Alertas formateadas con niveles de señal

## Detener la ejecución

Presiona **Ctrl + C** para detener todos los detectores de forma segura.

## Monitorización

El sistema mostrará logs en tiempo real:
```
[10:30:15] 🔵 Iniciando DETECTOR GOLD (XAUUSD)...
[10:30:17] 🔵 Iniciando DETECTOR SPX (SPX500)...
[10:30:19] ✅ Todos los detectores están activos
[10:30:19] 📌 Presiona Ctrl+C para detener
```

## Configuración

Las variables de entorno se cargan automáticamente desde el archivo `.env`:
- `TELEGRAM_TOKEN` - Token del bot de Telegram
- `TELEGRAM_CHAT_ID` - ID del chat donde se envían las alertas

Asegúrate de que el archivo `.env` existe y contiene las credenciales correctas.
