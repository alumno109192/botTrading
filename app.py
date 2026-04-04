import imaplib
import email
import time
import json
import requests
import os
from email.header import decode_header

# ══════════════════════════════════════
# CONFIGURACIÓN
# ══════════════════════════════════════
GMAIL_USER     = os.environ.get('GMAIL_USER')      # tu@gmail.com
GMAIL_PASSWORD = os.environ.get('GMAIL_PASSWORD')  # contraseña de app Google
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')

CHECK_INTERVAL = 30  # segundos entre comprobaciones

# ══════════════════════════════════════
# ENVIAR A TELEGRAM
# ══════════════════════════════════════
def enviar_telegram(asunto, cuerpo):
    # Detectar tipo de señal por el asunto
    if 'SELL' in asunto.upper():
        if 'MÁXIMA' in asunto.upper() or 'MAXIMA' in asunto.upper():
            emoji = '⚡'
            tipo  = 'SELL MÁXIMA'
        elif 'FUERTE' in asunto.upper():
            emoji = '🔴'
            tipo  = 'SELL FUERTE'
        elif 'MEDIA' in asunto.upper():
            emoji = '⚠️'
            tipo  = 'SELL MEDIA'
        elif 'PREP' in asunto.upper():
            emoji = '🔔'
            tipo  = 'PREPARAR SELL'
        elif 'CANCEL' in asunto.upper():
            emoji = '❌'
            tipo  = 'CANCELAR SELL'
        else:
            emoji = '👀'
            tipo  = 'SELL ALERTA'
    elif 'BUY' in asunto.upper():
        if 'MÁXIMA' in asunto.upper() or 'MAXIMA' in asunto.upper():
            emoji = '⚡'
            tipo  = 'BUY MÁXIMA'
        elif 'FUERTE' in asunto.upper():
            emoji = '🟢'
            tipo  = 'BUY FUERTE'
        elif 'MEDIA' in asunto.upper():
            emoji = '⚠️'
            tipo  = 'BUY MEDIA'
        elif 'PREP' in asunto.upper():
            emoji = '🔔'
            tipo  = 'PREPARAR BUY'
        elif 'CANCEL' in asunto.upper():
            emoji = '❌'
            tipo  = 'CANCELAR BUY'
        else:
            emoji = '👀'
            tipo  = 'BUY ALERTA'
    else:
        emoji = '📊'
        tipo  = 'SEÑAL'

    mensaje = f"""
{emoji} <b>{tipo}</b> {emoji}
━━━━━━━━━━━━━━━━━━━━
{cuerpo}
    """

    url     = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id":    TELEGRAM_CHAT_ID,
        "text":       mensaje,
        "parse_mode": "HTML"
    }
    response = requests.post(url, json=payload)
    print(f"Telegram enviado: {tipo} → {response.status_code}")

# ══════════════════════════════════════
# LEER EMAIL
# ══════════════════════════════════════
def leer_emails():
    try:
        # Conectar a Gmail via IMAP
        mail = imaplib.IMAP4_SSL('imap.gmail.com')
        mail.login(GMAIL_USER, GMAIL_PASSWORD)
        mail.select('inbox')

        # Buscar emails NO leídos de TradingView
        _, mensajes = mail.search(None, 'UNSEEN FROM "noreply@tradingview.com"')

        for num in mensajes[0].split():
            # Obtener el email
            _, datos = mail.fetch(num, '(RFC822)')
            msg = email.message_from_bytes(datos[0][1])

            # Decodificar asunto
            asunto_raw = decode_header(msg['Subject'])[0]
            asunto = asunto_raw[0].decode(asunto_raw[1] or 'utf-8') \
                     if isinstance(asunto_raw[0], bytes) else asunto_raw[0]

            # Obtener cuerpo del email
            cuerpo = ''
            if msg.is_multipart():
                for parte in msg.walk():
                    if parte.get_content_type() == 'text/plain':
                        cuerpo = parte.get_payload(decode=True).decode('utf-8', errors='ignore')
                        break
            else:
                cuerpo = msg.get_payload(decode=True).decode('utf-8', errors='ignore')

            print(f"Email recibido: {asunto}")
            print(f"Cuerpo: {cuerpo[:200]}")

            # Enviar a Telegram
            enviar_telegram(asunto, cuerpo.strip())

            # Marcar como leído
            mail.store(num, '+FLAGS', '\\Seen')

        mail.close()
        mail.logout()

    except Exception as e:
        print(f"Error leyendo email: {e}")

# ══════════════════════════════════════
# BUCLE PRINCIPAL
# ══════════════════════════════════════
def main():
    print("🚀 Bot de alertas iniciado")
    print(f"📧 Monitorizando: {GMAIL_USER}")
    print(f"⏱️  Revisando cada {CHECK_INTERVAL} segundos")

    while True:
        print(f"🔍 Revisando emails...")
        leer_emails()
        time.sleep(CHECK_INTERVAL)

if __name__ == '__main__':
    main()