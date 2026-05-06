from dotenv import load_dotenv
import os, requests

load_dotenv()
TOKEN = os.environ.get('TELEGRAM_TOKEN')

# Obtener nombre del bot para instrucciones
me = requests.get(f"https://api.telegram.org/bot{TOKEN}/getMe", timeout=10).json()
username = me['result'].get('username', 'el_bot')

print(f"Bot: @{username}")
print()
print("Para obtener los IDs, menciona al bot en cada subcanal:")
print(f"  Escribe '@{username} swing' en el subcanal Swing/Trading")
print(f"  Escribe '@{username} intraday' en el subcanal Intraday")
print(f"  Escribe '@{username} scalping' en el subcanal Scalping")
print()
input("Cuando hayas enviado los 3 mensajes, pulsa ENTER...")
print()

# Limpiar updates previos y obtener los nuevos
r = requests.get(f"https://api.telegram.org/bot{TOKEN}/getUpdates?limit=50&timeout=5", timeout=15)
data = r.json()

topics = {}
for u in data.get('result', []):
    msg = u.get('message') or u.get('channel_post') or {}
    thread_id = msg.get('message_thread_id')
    text      = (msg.get('text') or '')[:60]
    if thread_id:
        topics[thread_id] = text or topics.get(thread_id, '')

if topics:
    print("✅ Topics encontrados:")
    for tid, sample in sorted(topics.items()):
        print(f"  message_thread_id = {tid}  →  '{sample}'")
    print()
    print("Añade estas líneas a tu .env y a las variables de Render:")
    for tid, sample in sorted(topics.items()):
        hint = 'SWING' if 'swing' in sample.lower() else 'INTRADAY' if 'intraday' in sample.lower() else 'SCALPING' if 'scalping' in sample.lower() else '???'
        print(f"  THREAD_ID_{hint}={tid}")
else:
    print("❌ Aún no se encontraron mensajes con message_thread_id.")
    print("   El bot necesita ser ADMINISTRADOR del grupo para recibir mensajes.")
    print()
    print("   Alternativa rápida — abre Telegram Web (web.telegram.org),")
    print("   entra en cada subcanal y mira la URL:")
    print("   https://web.telegram.org/k/#-XXXXXXXXX_THREADID  ← ese número final es el ID")
