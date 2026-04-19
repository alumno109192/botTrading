"""
api/routes.py — Rutas Flask (extraídas de app.py)
"""
from flask import Flask, jsonify, request
from datetime import datetime
import os
import sys


def create_app(estado_sistema, threads_detectores):
    """Factory pattern para crear la app Flask."""
    app = Flask(__name__)
    CRON_TOKEN = os.environ.get('CRON_TOKEN', '')

    @app.route('/')
    def home():
        """Endpoint principal - Health check"""
        return jsonify({
            'status': 'online',
            'servicio': 'Bot Trading - Detectores de Señales',
            'iniciado': estado_sistema['iniciado'],
            'detectores': estado_sistema['detectores']
        })

    @app.route('/health')
    def health():
        """Health check para Render"""
        return jsonify({'status': 'healthy'}), 200

    @app.route('/status')
    def status():
        """Estado detallado del sistema"""
        token = request.headers.get('X-Cron-Token', '')
        if not CRON_TOKEN or token != CRON_TOKEN:
            return jsonify({'error': 'Unauthorized'}), 401
        return jsonify(estado_sistema)

    @app.route('/cron')
    def cron_ping():
        """Endpoint para CRON jobs - Mantiene el servicio activo y verifica threads"""
        if not CRON_TOKEN:
            return jsonify({'error': 'Unauthorized'}), 401
        token = request.headers.get('X-Cron-Token', '')
        if token != CRON_TOKEN:
            return jsonify({'error': 'Unauthorized'}), 401
        ahora = datetime.now()
        estado_sistema['ultima_actividad_cron'] = ahora.isoformat()

        # Verificar si hay threads registrados
        if not threads_detectores:
            print(f"[{ahora.strftime('%H:%M:%S')}] 🔔 CRON ping recibido - ⚠️ NO HAY THREADS REGISTRADOS (error en inicialización)")
            sys.stdout.flush()
            return jsonify({
                'status': 'alive_sin_detectores',
                'timestamp': ahora.isoformat(),
                'error': 'No hay threads de detectores registrados',
                'threads': {},
                'threads_activos': 0,
                'threads_totales': 0,
                'alerta': 'Sistema arrancó sin detectores - revisar logs de inicio'
            }), 200

        # Verificar estado de threads
        threads_vivos = {}
        threads_muertos = []

        for nombre, thread in threads_detectores.items():
            if thread.is_alive():
                threads_vivos[nombre] = 'vivo'
            else:
                threads_vivos[nombre] = 'muerto'
                threads_muertos.append(nombre)

        # Log de actividad
        print(f"[{ahora.strftime('%H:%M:%S')}] 🔔 CRON ping recibido - Threads vivos: {len([t for t in threads_vivos.values() if t == 'vivo'])}/{len(threads_detectores)}")
        sys.stdout.flush()

        if threads_muertos:
            print(f"[{ahora.strftime('%H:%M:%S')}] ⚠️ Threads muertos detectados: {', '.join(threads_muertos)}")
            sys.stdout.flush()

        return jsonify({
            'status': 'alive',
            'timestamp': ahora.isoformat(),
            'threads': threads_vivos,
            'threads_activos': len([t for t in threads_vivos.values() if t == 'vivo']),
            'threads_totales': len(threads_detectores),
            'alerta': 'Hay threads muertos' if threads_muertos else None,
            'detectores': estado_sistema['detectores']
        }), 200

    return app
