"""
telegram — Envío centralizado de mensajes a Telegram.
Cada detector solo necesita: from adapters.telegram import enviar_telegram
"""

import os
import time
import requests
from dotenv import load_dotenv

load_dotenv()

_TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
_TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')


def enviar_telegram(mensaje, thread_id=None):
    """Envía mensaje a Telegram con 3 reintentos y backoff exponencial."""
    url = f"https://api.telegram.org/bot{_TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": _TELEGRAM_CHAT_ID, "text": mensaje, "parse_mode": "HTML"}
    if thread_id:
        payload["message_thread_id"] = thread_id
    for intento in range(1, 4):
        try:
            r = requests.post(url, json=payload, timeout=10)
            if r.status_code == 200:
                print(f"✅ Telegram enviado (intento {intento})")
                return True
            else:
                print(f"❌ Telegram intento {intento} → HTTP {r.status_code}: {r.text[:80]}")
        except Exception as e:
            print(f"❌ Telegram intento {intento} → excepción: {e}")
        if intento < 3:
            time.sleep(2 ** intento)
    print("❌ Telegram: falló tras 3 intentos")
    return False
