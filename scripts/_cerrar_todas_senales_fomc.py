"""
Script de emergencia para cerrar todas las señales activas
USO: En eventos FOMC u otros de alta volatilidad
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from adapters.database import DatabaseManager
from adapters.telegram import enviar_telegram
from datetime import datetime, timezone

def cerrar_todas_senales_activas():
    """Cierra todas las señales activas marcándolas como CANCELADA por evento FOMC."""
    
    print("=" * 70)
    print("🚫 CIERRE DE EMERGENCIA - TODAS LAS SEÑALES ACTIVAS")
    print("=" * 70)
    
    db = DatabaseManager()
    
    # Obtener señales activas
    senales = db.obtener_senales_activas()
    
    if not senales:
        print("\n✅ No hay señales activas para cerrar")
        return
    
    print(f"\n📊 Señales activas encontradas: {len(senales)}")
    print("-" * 70)
    
    # Mostrar resumen
    for s in senales:
        simbolo = s['simbolo']
        direccion = s['direccion']
        entrada = s['precio_entrada']
        print(f"  • {simbolo} | {direccion} | Entrada: ${entrada:.2f}")
    
    # Confirmar acción
    print("\n⚠️  ¿Estás seguro de cerrar TODAS las señales activas?")
    print("   Motivo: Evento FOMC de alta volatilidad")
    confirmacion = input("\n   Escribe 'CERRAR' para confirmar: ")
    
    if confirmacion.strip().upper() != 'CERRAR':
        print("\n❌ Operación cancelada")
        return
    
    # Cerrar cada señal
    print("\n🔄 Cerrando señales...")
    cerradas = 0
    
    for senal in senales:
        try:
            senal_id = senal['id']
            simbolo = senal['simbolo']
            db.cerrar_senal(senal_id, 'CANCELADA', 0.0)
            print(f"  ✅ Cerrada: {simbolo} (ID {senal_id})")
            cerradas += 1
        except Exception as e:
            print(f"  ❌ Error cerrando señal {senal_id}: {e}")
    
    # Enviar alerta a Telegram
    if cerradas > 0:
        ahora_utc = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
        mensaje = f"""
🚫 <b>CIERRE MASIVO DE SEÑALES</b>

⚠️ Se han cerrado <b>{cerradas} señales activas</b> de forma preventiva.

📅 <b>Motivo:</b> Evento FOMC programado hoy
🕐 <b>Hora:</b> {ahora_utc}

💡 <b>Acción:</b>
• Todas las posiciones cerradas manualmente
• Trading automático continúa bloqueado hasta después del evento
• No se generarán nuevas señales durante la ventana de riesgo

━━━━━━━━━━━━━━━━━━━━
🔄 Sistema operativo - Esperando condiciones normales de mercado
"""
        
        try:
            enviar_telegram(mensaje)
            print(f"\n📱 Alerta enviada a Telegram")
        except Exception as e:
            print(f"\n⚠️  Error enviando alerta a Telegram: {e}")
    
    print("\n" + "=" * 70)
    print(f"✅ COMPLETADO - {cerradas} señales cerradas")
    print("=" * 70)


if __name__ == "__main__":
    try:
        cerrar_todas_senales_activas()
    except KeyboardInterrupt:
        print("\n\n❌ Operación interrumpida por el usuario")
    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
