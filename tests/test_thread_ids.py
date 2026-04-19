"""
test_thread_ids.py — Verifica los Thread IDs de Telegram
Envía un mensaje de prueba a cada thread configurado y permite confirmar
cuál tópico del grupo recibe cada mensaje.

Uso:
    python test_thread_ids.py
"""
import os
import requests
from dotenv import load_dotenv

load_dotenv()

TOKEN   = os.environ.get('TELEGRAM_TOKEN')
CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')

THREADS = {
    'SWING (1D/4H/1H)':  os.environ.get('THREAD_ID_SWING'),
    'SCALPING (15M/5M)': os.environ.get('THREAD_ID_SCALPING'),
    'INTRADAY':          os.environ.get('THREAD_ID_INTRADAY'),
}

def enviar_test(nombre, thread_id):
    if not thread_id or thread_id in ('', '???', 'None'):
        print(f"  ⏭️  {nombre}: no configurado (THREAD_ID vacío o '???')")
        return

    try:
        thread_id = int(thread_id)
    except (ValueError, TypeError):
        print(f"  ❌ {nombre}: thread_id inválido → '{thread_id}'")
        return

    url     = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {
        "chat_id":           CHAT_ID,
        "message_thread_id": thread_id,
        "text":              f"🧪 <b>Test Thread ID</b>\n\nEste mensaje corresponde al tópico: <b>{nombre}</b>\nThread ID: <code>{thread_id}</code>",
        "parse_mode":        "HTML",
    }

    try:
        r = requests.post(url, json=payload, timeout=10)
        if r.status_code == 200:
            print(f"  ✅ {nombre}: mensaje enviado al thread {thread_id}")
        else:
            print(f"  ❌ {nombre}: HTTP {r.status_code} → {r.text[:120]}")
    except Exception as e:
        print(f"  ❌ {nombre}: excepción → {e}")


if __name__ == "__main__":
    if not TOKEN or not CHAT_ID:
        print("❌ TELEGRAM_TOKEN o TELEGRAM_CHAT_ID no configurados en .env")
        exit(1)

    print(f"\n📤 Enviando mensajes de prueba...")
    print(f"   Chat ID: {CHAT_ID}\n")

    for nombre, thread_id in THREADS.items():
        enviar_test(nombre, thread_id)

    print("\n✅ Hecho. Revisa en Telegram qué tópico recibió cada mensaje.")
    print("   Si un mensaje aparece en el tópico equivocado, actualiza el .env correspondiente.")
