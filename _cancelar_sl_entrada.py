"""
_cancelar_sl_entrada.py — Cierra inmediatamente todas las señales
activas donde SL ≈ precio de entrada (señales inoperables).

Uso:
    .venv\Scripts\python.exe _cancelar_sl_entrada.py
"""
import os
from dotenv import load_dotenv
load_dotenv()

from adapters.database import DatabaseManager

SL_MIN_DIST = 0.5   # misma constante que en signal_monitor y guardar_senal

db = DatabaseManager()

activas = db.obtener_senales_activas()
esperando = db.obtener_senales_esperando()
todas = activas + esperando

malas = [
    s for s in todas
    if s.get('precio_entrada') and s.get('sl')
    and abs(float(s['sl']) - float(s['precio_entrada'])) < SL_MIN_DIST
]

if not malas:
    print("✅ No hay señales con SL ≈ entrada. Nada que cancelar.")
else:
    print(f"⛔ Encontradas {len(malas)} señal(es) inoperables:\n")
    for s in malas:
        sid   = s['id']
        sym   = s['simbolo']
        ent   = float(s['precio_entrada'])
        sl    = float(s['sl'])
        estado = s.get('estado', '?')
        print(f"  #{sid} | {sym} | entrada={ent:.2f} | SL={sl:.2f} | estado={estado}")
        db.cerrar_senal(sid, 'CANCELADA')
        print(f"  → CANCELADA ✅")
    print(f"\n{len(malas)} señal(es) canceladas en BD.")
