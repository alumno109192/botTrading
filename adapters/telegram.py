"""
telegram — Envío centralizado de mensajes a Telegram.
Cada detector solo necesita: from adapters.telegram import enviar_telegram
"""

import os
import re
import time
import requests
from dotenv import load_dotenv

load_dotenv()

_TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
_TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')

# Expresiones regulares para extraer campos del mensaje de señal inicial
# (formato generado por base_detector.py)
_RE_SIMBOLO    = re.compile(r'📊\s+([A-Z0-9/=^-]+)\s*\|', re.IGNORECASE)
_RE_TIMEFRAME  = re.compile(r'\|\s*([1-9][0-9]*[mMhHdDwW])\b')
_RE_DIRECCION  = re.compile(r'\b(COMPRA|VENTA|BUY|SELL)\b', re.IGNORECASE)
_RE_ENTRADA    = re.compile(r'Entrada[:\s$€£]+([0-9]+(?:[.,][0-9]+)?)', re.IGNORECASE)


def _intentar_publicar_sse(mensaje: str) -> None:
    """Publica el mensaje como evento SSE de señal (best-effort, no bloquea)."""
    try:
        from bridge.sse_broker import broker
        if broker.num_clientes == 0:
            return
        simbolo   = (_RE_SIMBOLO.search(mensaje)   or [None, None])[1]
        tf_match  = _RE_TIMEFRAME.search(mensaje)
        dir_match = _RE_DIRECCION.search(mensaje)
        ent_match = _RE_ENTRADA.search(mensaje)
        if simbolo and tf_match and dir_match:
            direccion = dir_match.group(1).upper()
            if direccion == 'BUY':
                direccion = 'COMPRA'
            elif direccion == 'SELL':
                direccion = 'VENTA'
            precio = float(ent_match.group(1).replace(',', '.')) if ent_match else 0.0
            broker.publicar_senal(
                tipo='nueva',
                simbolo=simbolo,
                timeframe=tf_match.group(1).upper(),
                direccion=direccion,
                precio_entrada=precio,
            )
    except Exception:
        pass  # nunca interrumpir el envío a Telegram por un error SSE


def enviar_telegram(mensaje, thread_id=None, reply_to_message_id=None):
    """Envía mensaje a Telegram con 3 reintentos y backoff exponencial.
    Retorna el message_id del mensaje enviado, o None si falla."""
    url = f"https://api.telegram.org/bot{_TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": _TELEGRAM_CHAT_ID, "text": mensaje, "parse_mode": "HTML"}
    if thread_id:
        payload["message_thread_id"] = thread_id
    if reply_to_message_id:
        payload["reply_to_message_id"] = reply_to_message_id
    for intento in range(1, 4):
        try:
            r = requests.post(url, json=payload, timeout=10)
            if r.status_code == 200:
                print(f"✅ Telegram enviado (intento {intento})")
                result = r.json()
                msg_id = result.get('result', {}).get('message_id')
                # Publicar en SSE para que el frontend lo reciba en tiempo real
                _intentar_publicar_sse(mensaje)
                return msg_id
            else:
                print(f"❌ Telegram intento {intento} → HTTP {r.status_code}: {r.text[:80]}")
        except Exception as e:
            print(f"❌ Telegram intento {intento} → excepción: {e}")
        if intento < 3:
            time.sleep(2 ** intento)
    print("❌ Telegram: falló tras 3 intentos")
    return None
