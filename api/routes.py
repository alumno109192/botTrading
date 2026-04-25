"""
api/routes.py — Rutas Flask (extraídas de app.py)
"""
from flask import Flask, jsonify, request
from datetime import datetime
import logging
import os

logger = logging.getLogger('bottrading')


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
        """Endpoint para CRON jobs - Mantiene el servicio activo y verifica threads.

        Autenticación: requerida solo si CRON_TOKEN está configurado.
        Si CRON_TOKEN está vacío (modo dev/no configurado) se permite acceso libre.
        Cuando se detectan threads muertos, los reinicia automáticamente.
        """
        if CRON_TOKEN:
            token = request.headers.get('X-Cron-Token', '')
            if token != CRON_TOKEN:
                return jsonify({'error': 'Unauthorized'}), 401
        ahora = datetime.now()
        estado_sistema['ultima_actividad_cron'] = ahora.isoformat()

        # Verificar si hay threads registrados
        if not threads_detectores:
            logger.warning("🔔 CRON ping - ⚠️ NO HAY THREADS REGISTRADOS (error en inicialización)")
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
        threads_muertos = [n for n, t in threads_detectores.items() if not t.is_alive()]

        vivos = len(threads_detectores) - len(threads_muertos)
        logger.info(f"🔔 CRON ping - Threads vivos: {vivos}/{len(threads_detectores)}")

        if threads_muertos:
            logger.warning(f"⚠️ Threads muertos detectados: {', '.join(threads_muertos)}")
            try:
                from services.orchestrator import reiniciar_detector
                for clave in threads_muertos:
                    reiniciar_detector(clave, estado_sistema, threads_detectores)
            except Exception as _e:
                logger.error(f"❌ Error al reiniciar threads muertos: {_e}")

        # Recalcular estado final (puede haber cambiado tras reinicios)
        threads_estado = {
            n: ('vivo' if t.is_alive() else 'muerto')
            for n, t in threads_detectores.items()
        }
        vivos_final = len([v for v in threads_estado.values() if v == 'vivo'])

        return jsonify({
            'status': 'alive',
            'timestamp': ahora.isoformat(),
            'threads': threads_estado,
            'threads_activos': vivos_final,
            'threads_totales': len(threads_detectores),
            'alerta': 'Hay threads muertos' if threads_muertos else None,
            'detectores': estado_sistema['detectores']
        }), 200

    return app
