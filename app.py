from flask import Flask, request
import requests
import os
from dotenv import load_dotenv

# Cargar variables de entorno desde .env
load_dotenv()

app = Flask(__name__)

# Configuración
TELEGRAM_TOKEN   = os.environ.get('TELEGRAM_TOKEN')   # Tu token del bot
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID') # Tu chat ID

def enviar_telegram(mensaje):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text":    mensaje,
        "parse_mode": "HTML"
    }
    requests.post(url, json=payload)

@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.json

    # Formatear el mensaje recibido de TradingView
    mensaje = f"""
🚨 <b>SEÑAL DE TRADING</b> 🚨

{data.get('mensaje', 'Sin mensaje')}

📌 <b>Orden Límite:</b> {data.get('limit',  'N/A')}
🛑 <b>Stop Loss:</b>   {data.get('sl',     'N/A')}
🎯 <b>TP1:</b>        {data.get('tp1',    'N/A')}
🎯 <b>TP2:</b>        {data.get('tp2',    'N/A')}
🎯 <b>TP3:</b>        {data.get('tp3',    'N/A')}
📊 <b>Puntuación:</b> {data.get('score',  'N/A')}/15
⏰ <b>Hora:</b>       {data.get('time',   'N/A')}
    """
    enviar_telegram(mensaje)
    return {"status": "ok"}, 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
