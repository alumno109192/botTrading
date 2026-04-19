"""
Script de testing para verificar envío de mensajes a Telegram
"""
import requests
import os
from datetime import datetime
from dotenv import load_dotenv

# Cargar variables de entorno
load_dotenv()

TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')

def test_telegram_simple():
    """Test básico: mensaje de texto plano"""
    print("=" * 60)
    print("TEST 1: Mensaje simple (texto plano)")
    print("=" * 60)
    
    mensaje = f"🧪 Test de conectividad - {datetime.now().strftime('%H:%M:%S')}"
    
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": mensaje
        }
        
        print(f"📤 Enviando: {mensaje}")
        r = requests.post(url, json=payload, timeout=10)
        
        if r.status_code == 200:
            print(f"✅ Éxito → Status {r.status_code}")
            print(f"   Respuesta: {r.json()}")
        else:
            print(f"❌ Error → Status {r.status_code}")
            print(f"   Respuesta: {r.text}")
    except Exception as e:
        print(f"❌ Excepción: {e}")
    
    print()

def test_telegram_html():
    """Test con formato HTML (como usan los detectores)"""
    print("=" * 60)
    print("TEST 2: Mensaje con formato HTML")
    print("=" * 60)
    
    mensaje = (
        f"🧪 <b>Test HTML — BotTrading</b> 🧪\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"⏰ <b>Hora:</b> {datetime.now().strftime('%H:%M:%S')}\n"
        f"📊 <b>Score:</b> 8/21\n"
        f"💰 <b>Precio:</b> $71,500\n"
        f"🎯 <b>TP1:</b> $75,000\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"✅ Sistema operativo"
    )
    
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": mensaje,
            "parse_mode": "HTML"
        }
        
        print(f"📤 Enviando mensaje con HTML...")
        print(f"   Primeros 100 chars: {mensaje[:100]}...")
        r = requests.post(url, json=payload, timeout=10)
        
        if r.status_code == 200:
            print(f"✅ Éxito → Status {r.status_code}")
            print(f"   Respuesta: {r.json()}")
        else:
            print(f"❌ Error → Status {r.status_code}")
            print(f"   Respuesta: {r.text}")
    except Exception as e:
        print(f"❌ Excepción: {e}")
    
    print()

def test_telegram_largo():
    """Test con mensaje muy largo para verificar límites"""
    print("=" * 60)
    print("TEST 3: Mensaje largo (verificar límite 4096 chars)")
    print("=" * 60)
    
    # Telegram tiene límite de 4096 caracteres
    mensaje_base = "🧪 <b>Test de mensaje largo</b>\n━━━━━━━━━━━━━━━━━━━━\n"
    relleno = "📊 Línea de prueba " * 200  # Crear mensaje muy largo
    mensaje = mensaje_base + relleno
    
    print(f"📏 Longitud del mensaje: {len(mensaje)} caracteres")
    
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": mensaje,
            "parse_mode": "HTML"
        }
        
        print(f"📤 Enviando...")
        r = requests.post(url, json=payload, timeout=10)
        
        if r.status_code == 200:
            print(f"✅ Éxito → Status {r.status_code}")
        else:
            print(f"❌ Error → Status {r.status_code}")
            print(f"   Respuesta: {r.text}")
            if len(mensaje) > 4096:
                print(f"   ⚠️ Mensaje excede límite Telegram (4096 chars)")
    except Exception as e:
        print(f"❌ Excepción: {e}")
    
    print()

def test_credenciales():
    """Verificar que las credenciales están configuradas"""
    print("=" * 60)
    print("VERIFICACIÓN DE CREDENCIALES")
    print("=" * 60)
    
    if not TELEGRAM_TOKEN:
        print("❌ TELEGRAM_TOKEN no configurado en .env")
        return False
    else:
        token_preview = f"{TELEGRAM_TOKEN[:10]}...{TELEGRAM_TOKEN[-5:]}" if len(TELEGRAM_TOKEN) > 15 else "***"
        print(f"✅ TELEGRAM_TOKEN: {token_preview}")
    
    if not TELEGRAM_CHAT_ID:
        print("❌ TELEGRAM_CHAT_ID no configurado en .env")
        return False
    else:
        print(f"✅ TELEGRAM_CHAT_ID: {TELEGRAM_CHAT_ID}")
    
    print()
    return True

if __name__ == "__main__":
    print("\n🧪 INICIANDO TESTS DE TELEGRAM\n")
    
    # Verificar credenciales
    if not test_credenciales():
        print("❌ Faltan credenciales. Configura .env y vuelve a intentar.")
        exit(1)
    
    # Ejecutar tests
    test_telegram_simple()
    test_telegram_html()
    test_telegram_largo()
    
    print("=" * 60)
    print("✅ TESTS COMPLETADOS")
    print("=" * 60)
    print("\nRevisa tu chat de Telegram para confirmar la recepción de mensajes.")
