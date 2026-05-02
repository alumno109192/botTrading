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

    # ─────────────────────────────────────────────────────────────────────────
    # PERFORMANCE DASHBOARD
    # ─────────────────────────────────────────────────────────────────────────

    @app.route('/api/performance')
    def performance_kpis():
        """KPIs de performance agrupados por nivel, timeframe y asset.

        Devuelve un JSON con tres secciones:
          - por_nivel    : ALERTA / MEDIA / FUERTE / MAXIMA
          - por_timeframe: 1D / 4H / 1H / 15M / 5M
          - por_asset    : GOLD / BTC / ...

        Para cada grupo: total señales, wins, losses, win_rate, avg_pnl,
        profit_factor.
        """
        try:
            from adapters.database import get_db
            db = get_db()
            if db is None:
                return jsonify({'error': 'Base de datos no disponible'}), 503
            kpis = db.obtener_kpis_performance()
            return jsonify({
                'timestamp': datetime.utcnow().isoformat() + 'Z',
                'kpis': kpis,
            })
        except Exception as e:
            logger.error(f"❌ /api/performance error: {e}")
            return jsonify({'error': str(e)}), 500

    @app.route('/api/performance/dashboard')
    def performance_dashboard():
        """Dashboard HTML con KPIs de performance en tabla."""
        try:
            from adapters.database import get_db
            db = get_db()
            if db is None:
                return "<h2>Base de datos no disponible</h2>", 503
            kpis = db.obtener_kpis_performance()
            html = _render_performance_html(kpis)
            return html
        except Exception as e:
            logger.error(f"❌ /api/performance/dashboard error: {e}")
            return f"<h2>Error: {e}</h2>", 500

    return app


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS HTML
# ─────────────────────────────────────────────────────────────────────────────

def _render_performance_html(kpis: dict) -> str:
    """Genera el HTML del dashboard de performance."""
    NIVEL_ORDER = ['MAXIMA', 'FUERTE', 'MEDIA', 'ALERTA']
    NIVEL_COLOR = {
        'MAXIMA': '#ff4444', 'FUERTE': '#ff8800',
        'MEDIA': '#ffcc00',  'ALERTA': '#88cc44',
    }

    def _tabla(titulo: str, filas: list, orden: list | None = None) -> str:
        if not filas:
            return f"<h3>{titulo}</h3><p style='color:#888'>Sin datos cerrados aún</p>"
        if orden:
            key_map = {f['grupo']: f for f in filas}
            filas = [key_map[k] for k in orden if k in key_map] + \
                    [f for f in filas if f['grupo'] not in orden]
        rows_html = ''
        for f in filas:
            g = f.get('grupo') or '—'
            pf = f.get('profit_factor')
            pf_str = f"{pf:.2f}" if pf is not None else '—'
            avg = f.get('avg_pnl')
            avg_str = f"{avg:+.3f}%" if avg is not None else '—'
            color = NIVEL_COLOR.get(str(g), '#cccccc')
            badge = f"<span style='background:{color};color:#111;padding:2px 8px;border-radius:4px;font-weight:bold'>{g}</span>"
            rows_html += (
                f"<tr>"
                f"<td style='padding:8px'>{badge}</td>"
                f"<td style='padding:8px;text-align:center'>{f.get('total', 0)}</td>"
                f"<td style='padding:8px;text-align:center'>{f.get('wins', 0)}</td>"
                f"<td style='padding:8px;text-align:center'>{f.get('losses', 0)}</td>"
                f"<td style='padding:8px;text-align:center'><b>{f.get('win_rate', 0):.1f}%</b></td>"
                f"<td style='padding:8px;text-align:center'>{pf_str}</td>"
                f"<td style='padding:8px;text-align:center'>{avg_str}</td>"
                f"</tr>"
            )
        return (
            f"<h3 style='margin-top:28px'>{titulo}</h3>"
            f"<table style='border-collapse:collapse;width:100%;background:#1e1e1e;color:#eee'>"
            f"<thead><tr style='background:#333'>"
            f"<th style='padding:8px;text-align:left'>Grupo</th>"
            f"<th style='padding:8px'>Total</th><th style='padding:8px'>Wins</th>"
            f"<th style='padding:8px'>Losses</th><th style='padding:8px'>Win Rate</th>"
            f"<th style='padding:8px'>Profit Factor</th><th style='padding:8px'>Avg PnL</th>"
            f"</tr></thead><tbody>{rows_html}</tbody></table>"
        )

    body = (
        _tabla("📊 Por Nivel de Señal", kpis.get('por_nivel', []), orden=NIVEL_ORDER)
        + _tabla("⏱️ Por Timeframe", kpis.get('por_timeframe', []))
        + _tabla("📈 Por Activo", kpis.get('por_asset', []))
    )
    now = datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')
    return f"""<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Performance Dashboard — BotTrading</title>
  <style>
    body {{ font-family: Arial, sans-serif; background: #121212; color: #eee;
            max-width: 900px; margin: 0 auto; padding: 20px; }}
    h1 {{ color: #fff; border-bottom: 2px solid #444; padding-bottom: 8px; }}
    h3 {{ color: #aaa; }}
    table {{ font-size: 14px; }}
    a {{ color: #88aaff; }}
  </style>
</head>
<body>
  <h1>📊 Performance Dashboard</h1>
  <p style="color:#888">Actualizado: {now} &nbsp;|&nbsp; <a href="/api/performance">JSON raw</a></p>
  {body}
</body>
</html>"""
