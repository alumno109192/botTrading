"""
bridge/mt5_bridge.py — Servidor puente local Windows ↔ Render

Corre en tu PC Windows con MT5 abierto. El bot de Render le manda
órdenes por HTTP y este script las ejecuta en MT5.

Arranque:
    cd C:\\PythonProjects\\BotTrading
    .venv\\Scripts\\python.exe bridge\\mt5_bridge.py

Exposición a internet (necesario para que Render lo alcance):
    1. Descargar ngrok: https://ngrok.com/download (gratis)
    2. En otra terminal: ngrok http 5001
    3. Copiar la URL https://xxxx.ngrok-free.app → MT5_BRIDGE_URL en Render

Variables de entorno requeridas (en .env local):
    MT5_LOGIN, MT5_PASSWORD, MT5_SERVER, MT5_SYMBOL, MT5_LOTES
    MT5_BRIDGE_TOKEN  — token secreto compartido con Render (invéntate uno)
"""

import os
import sys
import logging

# Añadir raíz del proyecto al path
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from flask import Flask, request, jsonify
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [MT5Bridge] %(levelname)s %(message)s'
)
logger = logging.getLogger('mt5bridge')

# ── Auth ────────────────────────────────────────────────────────────────────
BRIDGE_TOKEN = os.getenv('MT5_BRIDGE_TOKEN', '')
if not BRIDGE_TOKEN:
    logger.warning("⚠️  MT5_BRIDGE_TOKEN no configurado — cualquiera puede mandar órdenes!")

# ── Importar broker (usa MT5 local) ─────────────────────────────────────────
# Forzar modo local: MT5_AUTO_TRADE=true aquí porque este script ES el broker local
os.environ['MT5_AUTO_TRADE'] = 'true'
from adapters.mt5_broker import MT5Broker

broker = MT5Broker()
_conectado = broker.conectar()
if _conectado:
    logger.info("✅ MT5 conectado")
else:
    logger.error("❌ No se pudo conectar a MT5 — verifica que MT5 esté abierto y .env correcto")

# ── App Flask ────────────────────────────────────────────────────────────────
app = Flask(__name__)


def _verificar_token():
    """Comprueba el token en la cabecera X-Bridge-Token."""
    if not BRIDGE_TOKEN:
        return True  # sin token configurado, pasa todo (solo para desarrollo)
    token = request.headers.get('X-Bridge-Token', '')
    return token == BRIDGE_TOKEN


@app.route('/ping', methods=['GET'])
def ping():
    """Health check — devuelve estado de cuenta MT5."""
    if not _verificar_token():
        return jsonify({'error': 'token inválido'}), 401
    estado = broker.estado_cuenta()
    return jsonify({
        'ok': True,
        'conectado': broker._activo,
        'cuenta': estado
    })


@app.route('/abrir', methods=['POST'])
def abrir():
    """
    Abre una posición en MT5.

    Body JSON:
        direccion  : 'BUY' | 'SELL'
        entry      : float
        sl         : float
        tp1        : float
        timeframe  : str  (opcional, ej. '5m')
        score      : int  (opcional)
    """
    if not _verificar_token():
        return jsonify({'error': 'token inválido'}), 401

    data = request.get_json(silent=True)
    if not data:
        return jsonify({'error': 'body JSON requerido'}), 400

    campos = ['direccion', 'entry', 'sl', 'tp1']
    for campo in campos:
        if campo not in data:
            return jsonify({'error': f'campo requerido: {campo}'}), 400

    logger.info(f"📥 Orden recibida: {data.get('direccion')} entry={data.get('entry')} sl={data.get('sl')} tp1={data.get('tp1')}")

    ticket = broker.abrir_operacion(data)
    if ticket:
        logger.info(f"✅ Orden ejecutada — ticket {ticket}")
        return jsonify({'ok': True, 'ticket': ticket})
    else:
        logger.error("❌ No se pudo abrir la orden")
        return jsonify({'ok': False, 'error': 'MT5 rechazó la orden'}), 500


@app.route('/cerrar', methods=['POST'])
def cerrar():
    """
    Cierra una posición abierta.

    Body JSON:
        ticket : int
    """
    if not _verificar_token():
        return jsonify({'error': 'token inválido'}), 401

    data = request.get_json(silent=True)
    if not data or 'ticket' not in data:
        return jsonify({'error': 'campo requerido: ticket'}), 400

    ticket = int(data['ticket'])
    logger.info(f"📥 Cierre recibido — ticket {ticket}")

    ok = broker.cerrar_operacion(ticket)
    if ok:
        logger.info(f"✅ Posición {ticket} cerrada")
        return jsonify({'ok': True})
    else:
        logger.error(f"❌ No se pudo cerrar posición {ticket}")
        return jsonify({'ok': False, 'error': 'no se pudo cerrar'}), 500


@app.route('/estado', methods=['GET'])
def estado():
    """Devuelve posiciones abiertas por el bot."""
    if not _verificar_token():
        return jsonify({'error': 'token inválido'}), 401
    return jsonify(broker.estado_cuenta())


if __name__ == '__main__':
    port = int(os.getenv('MT5_BRIDGE_PORT', '5001'))
    logger.info(f"🚀 MT5 Bridge corriendo en http://0.0.0.0:{port}")
    logger.info("💡 En otra terminal ejecuta: ngrok http 5001")
    logger.info("💡 Copia la URL ngrok → MT5_BRIDGE_URL en Render")
    app.run(host='0.0.0.0', port=port, debug=False)
