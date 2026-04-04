# Bot Trading - Detectores de Señales Técnicas

Sistema automatizado de detección de señales de trading que analiza múltiples instrumentos financieros usando indicadores técnicos y envía alertas a Telegram.

## 🎯 Características

- 📊 **Análisis técnico completo**: RSI, EMA (9/21/200), ATR, volumen
- 🕯️ **Patrones de velas japonesas**: Shooting Star, Hammer, Engulfing, Marubozu, Doji
- ⚡ **Sistema de scoring** (0-15 puntos) con 4 niveles de alerta
- 🔕 **Anti-spam inteligente**: Solo notifica en velas nuevas o cambios significativos
- 💚 **Servidor siempre activo**: Revisión cada 14 minutos
- 🔄 **Multi-threading**: Ejecuta múltiples detectores simultáneamente
- 📱 **Alertas a Telegram**: Notificaciones formateadas con SL, TP y R:R

## 📈 Instrumentos monitorizados

- 🥇 **XAUUSD** (Oro) - `detector_gold.py`
- 📊 **SPX500** (S&P 500) - `detector_spx.py`

## 🔧 Requisitos

- Python 3.8+
- Bot de Telegram creado con @BotFather
- ID del chat de Telegram donde se enviarán las señales

## 📦 Instalación

1. **Clonar el repositorio**
```bash
git clone https://github.com/alumno109192/botTrading.git
cd botTrading
```

2. **Crear entorno virtual e instalar dependencias**
```bash
python -m venv venv
.\venv\Scripts\python.exe -m pip install -r requirements.txt
```

3. **Configurar variables de entorno**
   - Copiar `.env.example` a `.env`
   - Editar `.env` con tus credenciales

```env
TELEGRAM_TOKEN=tu_token_del_bot_aqui
TELEGRAM_CHAT_ID=tu_chat_id_aqui
```

## 🚀 Uso

### Ejecutar todos los detectores (RECOMENDADO)

```bash
.\venv\Scripts\python.exe run_detectors.py
```

Esto iniciará:
- 🥇 Detector de Oro (XAUUSD)
- 📈 Detector de SPX500

Ambos se ejecutan en **hilos separados** y de forma independiente.

### Ejecutar detectores individuales

**Solo Oro:**
```bash
.\venv\Scripts\python.exe detector_gold.py
```

**Solo SPX500:**
```bash
.\venv\Scripts\python.exe detector_spx.py
```

Ver más detalles en [EJECUTAR.md](EJECUTAR.md)

## 📊 Niveles de Señal

El sistema asigna un **score de 0-15 puntos** basado en múltiples condiciones técnicas:

- ⚡ **MÁXIMA** (10+ puntos): Confluencia muy fuerte de indicadores
- 🔴/🟢 **FUERTE** (8-9 puntos): Alta probabilidad
- ⚠️ **MEDIA** (6-7 puntos): Probabilidad moderada  
- 👀 **ALERTA** (4-5 puntos): Observar, posible oportunidad

## 🛑 Detener la ejecución

Presiona **Ctrl + C** para detener todos los detectores de forma segura.
## 📱 Formato de Alertas en Telegram

Las señales se envían con información completa:

```
⚡ SELL MÁXIMA ⚡
━━━━━━━━━━━━━━━━━━━━
📈 Símbolo:    XAUUSD
💰 Precio:     4783.2
📌 SELL LIMIT: 4765.0
🛑 Stop Loss:  4825.0
🎯 TP1: 4627  R:R 2.3:1
🎯 TP2: 4374  R:R 6.5:1
🎯 TP3: 4099  R:R 11.1:1
━━━━━━━━━━━━━━━━━━━━
📊 Score: 12/15
📉 RSI: 68.2
⏱️ TF: 1D  📅 2026-04-01
```

## 🔒 Seguridad

- ✅ El archivo `.env` está en `.gitignore` (no se sube a GitHub)
- ✅ Usa contraseñas de aplicación de Google (no contraseña normal)
- ✅ Tokens y credenciales se cargan desde variables de entorno

## 🤝 Cómo obtener credenciales

### TELEGRAM_TOKEN
1. Abre Telegram y busca **@BotFather**
2. Envía `/newbot` y sigue las instrucciones
3. Copia el token que te proporciona

### TELEGRAM_CHAT_ID
1. Busca **@userinfobot** en Telegram
2. Inicia conversación
3. Te dará tu chat_id (para grupos, añade el bot y usa `getUpdates`)

## 📄 Licencia

MIT
