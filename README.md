# Bot Trading - TradingView a Telegram

Bot Flask que recibe señales de trading desde TradingView mediante webhook y las envía a Telegram.

## Características

- Recibe señales de TradingView vía webhook POST
- Envía notificaciones formateadas a un chat de Telegram
- Soporta múltiples parámetros: límite, stop loss, take profits, puntuación y hora

## Requisitos

- Python 3.8+
- Bot de Telegram creado con @BotFather
- ID del chat de Telegram donde se enviarán las señales

## Instalación

1. Clonar o descargar este repositorio

2. Instalar las dependencias:
```bash
pip install -r requirements.txt
```

3. Configurar las variables de entorno:
   - Copiar `.env.example` a `.env`
   - Editar `.env` con tu token de bot de Telegram y chat ID

```env
TELEGRAM_TOKEN=tu_token_del_bot_aqui
TELEGRAM_CHAT_ID=tu_chat_id_aqui
```

## Uso

### Ejecutar el servidor Flask

```bash
python app.py
```

El servidor estará disponible en `http://0.0.0.0:5000`

### Configurar TradingView

1. En TradingView, crea una alerta
2. En el webhook URL, usa: `http://tu-servidor:5000/webhook`
3. En el mensaje de la alerta, envía un JSON con el siguiente formato:

```json
{
  "mensaje": "LONG BTC/USDT",
  "limit": "45000",
  "sl": "44000",
  "tp1": "46000",
  "tp2": "47000",
  "tp3": "48000",
  "score": "12",
  "time": "{{timenow}}"
}
```

## Formato del mensaje en Telegram

Las señales se envían con el siguiente formato:

```
🚨 SEÑAL DE TRADING 🚨

LONG BTC/USDT

📌 Orden Límite: 45000
🛑 Stop Loss: 44000
🎯 TP1: 46000
🎯 TP2: 47000
🎯 TP3: 48000
📊 Puntuación: 12/15
⏰ Hora: 2026-04-03 10:30:00
```

## Despliegue

Para desplegar en producción, se recomienda usar:
- Gunicorn o uWSGI como servidor WSGI
- Nginx como proxy inverso
- SSL/TLS para conexiones seguras

Ejemplo con Gunicorn:
```bash
pip install gunicorn
gunicorn -w 4 -b 0.0.0.0:5000 app:app
```

## Licencia

MIT
