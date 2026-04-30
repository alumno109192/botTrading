"""
Script de prueba para verificar el bloqueo de trading por eventos FOMC
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.economic_calendar import debe_bloquear_trading, proximos_eventos
from datetime import datetime, timezone

print("=" * 60)
print("TEST - BLOQUEO DE TRADING POR EVENTOS CRÍTICOS")
print("=" * 60)

# Hora actual
ahora = datetime.now(timezone.utc)
print(f"\n⏰ Hora actual (UTC): {ahora.strftime('%Y-%m-%d %H:%M:%S')}")

# Verificar si hay bloqueo activo
print("\n🔍 Verificando si hay eventos críticos...")
bloqueado, descripcion, minutos = debe_bloquear_trading(90)

if bloqueado:
    print(f"\n🚫 TRADING BLOQUEADO")
    print(f"   Evento: {descripcion}")
    if minutos > 0:
        print(f"   Tiempo restante: {minutos} minutos")
    elif minutos == 0:
        print(f"   Evento: AHORA")
    else:
        print(f"   Evento ocurrió hace: {abs(minutos)} minutos")
else:
    print(f"\n✅ Trading permitido - No hay eventos críticos")

# Mostrar próximos eventos
print("\n📅 Próximos 5 eventos programados:")
print("-" * 60)
eventos = proximos_eventos(5)
for evento_dt, desc in eventos:
    print(f"  {evento_dt.strftime('%Y-%m-%d %H:%M')} UTC | {desc}")

print("\n" + "=" * 60)
