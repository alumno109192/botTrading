"""
api/routes.py — Rutas Flask (extraídas de app.py)
"""
from flask import Flask, jsonify, request, render_template
from datetime import datetime, timedelta, timezone
import logging
import os
import pathlib

logger = logging.getLogger('bottrading')

_FRONTEND_DIR = pathlib.Path(__file__).parent.parent / 'frontend'


def create_app(estado_sistema, threads_detectores):
    """Factory pattern para crear la app Flask."""
    app = Flask(
        __name__,
        template_folder=str(_FRONTEND_DIR / 'templates'),
        static_folder=str(_FRONTEND_DIR / 'static'),
        static_url_path='/static',
    )
    CRON_TOKEN = os.environ.get('CRON_TOKEN', '')

    @app.route('/')
    def home():
        """Endpoint principal — redirige al dashboard si es un navegador."""
        from flask import redirect, request as freq
        accept = freq.headers.get('Accept', '')
        if 'text/html' in accept:
            return redirect('/dashboard', code=302)
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

    # ─────────────────────────────────────────────────────────────────────────
    # DASHBOARD FRONTEND
    # ─────────────────────────────────────────────────────────────────────────

    @app.route('/dashboard')
    @app.route('/dashboard/activas')
    def dashboard_activas():
        return render_template('dashboard.html', vista_inicial='activas')

    @app.route('/dashboard/historial')
    def dashboard_historial():
        return render_template('dashboard.html', vista_inicial='historial')

    @app.route('/dashboard/stats')
    def dashboard_stats():
        return render_template('dashboard.html', vista_inicial='stats')

    @app.route('/dashboard/pendientes')
    def dashboard_pendientes():
        return render_template('dashboard.html', vista_inicial='pendientes')

    # ─────────────────────────────────────────────────────────────────────────
    # API v1 — señales
    # ─────────────────────────────────────────────────────────────────────────

    @app.route('/api/v1/senales/activas')
    def v1_senales_activas():
        """Señales activas + pendientes con precio actual calculado."""
        def _f(v, default=0.0):
            """Convierte cualquier valor (incluyendo dicts Turso no procesados) a float."""
            if v is None:
                return default
            if isinstance(v, (int, float)):
                return float(v)
            if isinstance(v, dict):
                # dict Turso crudo: {"type": "float", "value": "2345.5"}
                raw = v.get('value')
                if raw is None:
                    return default
                try:
                    return float(raw)
                except (TypeError, ValueError):
                    return default
            try:
                return float(v)
            except (TypeError, ValueError):
                return default

        try:
            from adapters.database import get_db
            db = get_db()
            if db is None:
                return jsonify({'error': 'BD no disponible'}), 503

            activas   = db.obtener_senales_activas()    # ACTIVA + PENDIENTE_CONFIRM
            esperando = db.obtener_senales_esperando()    # BUY/SELL LIMIT pendientes de entrada
            senales   = activas + esperando

            tf_filter = request.args.get('tf')
            if tf_filter:
                senales = [s for s in senales if s.get('timeframe') == tf_filter]

            # Calcular PnL en tiempo real para cada señal
            for s in senales:
                entrada = _f(s.get('precio_entrada'))
                actual  = _f(s.get('precio_actual')) or entrada
                if entrada and actual:
                    if s.get('direccion') == 'COMPRA':
                        s['pnl_pct'] = round((actual - entrada) / entrada * 100, 3)
                    else:
                        s['pnl_pct'] = round((entrada - actual) / entrada * 100, 3)
                else:
                    s['pnl_pct'] = 0.0

                # Calcular progreso hacia TP1
                tp1 = _f(s.get('tp1'))
                sl  = _f(s.get('sl'))
                if tp1 and sl and entrada:
                    rango  = abs(tp1 - entrada)
                    avance = abs(actual - entrada) if actual else 0
                    s['tp1_progreso'] = min(round(avance / rango * 100, 1), 100) if rango else 0
                else:
                    s['tp1_progreso'] = 0

            return jsonify({
                'timestamp': datetime.utcnow().isoformat() + 'Z',
                'total': len(senales),
                'senales': senales,
            })
        except Exception as e:
            logger.error(f"❌ /api/v1/senales/activas error: {e}")
            return jsonify({'error': str(e)}), 500

    @app.route('/api/v1/senales/historial')
    def v1_senales_historial():
        """Últimas N señales cerradas. Parámetros: tf, limit (def 30), horas (def 168)."""
        try:
            from adapters.database import get_db
            db = get_db()
            if db is None:
                return jsonify({'error': 'BD no disponible'}), 503

            horas = int(request.args.get('horas', 168))   # 7 días por defecto
            limit = int(request.args.get('limit', 30))
            tf_filter = request.args.get('tf')

            from adapters.database import DatabaseManager
            query = f"""
            SELECT id, timestamp, simbolo, asset, timeframe, direccion,
                   precio_entrada, tp1, tp2, tp3, sl, score, estado,
                   beneficio_final_pct, nivel, fecha_cierre
            FROM senales
            WHERE estado IN ('TP1','TP2','TP3','SL','CANCELADA','CADUCADA')
              AND timestamp >= datetime('now', '-{int(horas)} hours')
            {'AND timeframe = ?' if tf_filter else ''}
            ORDER BY timestamp DESC
            LIMIT {int(limit)}
            """
            params = (tf_filter,) if tf_filter else ()
            result = db.ejecutar_query(query, params)
            senales = [dict(row) for row in result.rows] if result.rows else []

            return jsonify({
                'timestamp': datetime.utcnow().isoformat() + 'Z',
                'total': len(senales),
                'senales': senales,
            })
        except Exception as e:
            logger.error(f"❌ /api/v1/senales/historial error: {e}")
            return jsonify({'error': str(e)}), 500

    # ─────────────────────────────────────────────────────────────────────────
    # API v1 — stats
    # ─────────────────────────────────────────────────────────────────────────

    @app.route('/api/v1/scores')
    def v1_scores():
        """Scores SELL/BUY más recientes por símbolo y timeframe."""
        try:
            from services import tf_bias
            return jsonify({
                'timestamp': datetime.utcnow().isoformat() + 'Z',
                'scores': tf_bias.obtener_todos_scores(),
            })
        except Exception as e:
            logger.error(f"❌ /api/v1/scores error: {e}")
            return jsonify({'error': str(e)}), 500

    @app.route('/api/v1/stats/global')
    def v1_stats_global():
        """Win rate global, P&L, señales hoy/semana/mes."""
        try:
            from adapters.database import get_db
            db = get_db()
            if db is None:
                return jsonify({'error': 'BD no disponible'}), 503

            ahora = datetime.now(timezone.utc)
            stats_30d = db.obtener_estadisticas_periodo(ahora - timedelta(days=30), ahora)

            # Señales hoy
            inicio_hoy = ahora.replace(hour=0, minute=0, second=0, microsecond=0)
            stats_hoy = db.obtener_estadisticas_periodo(inicio_hoy, ahora)

            # Señales semana
            stats_semana = db.obtener_estadisticas_periodo(ahora - timedelta(days=7), ahora)

            return jsonify({
                'timestamp': datetime.utcnow().isoformat() + 'Z',
                'global_30d': stats_30d,
                'hoy': stats_hoy,
                'semana': stats_semana,
            })
        except Exception as e:
            logger.error(f"❌ /api/v1/stats/global error: {e}")
            return jsonify({'error': str(e)}), 500

    @app.route('/api/v1/stats/equity_curve')
    def v1_equity_curve():
        """Serie temporal de P&L acumulado (últimos 30 días)."""
        try:
            from adapters.database import get_db
            db = get_db()
            if db is None:
                return jsonify({'error': 'BD no disponible'}), 503

            query = """
            SELECT DATE(timestamp) as fecha,
                   SUM(beneficio_final_pct) as pnl_dia,
                   COUNT(*) as señales
            FROM senales
            WHERE estado IN ('TP1','TP2','TP3','SL')
              AND timestamp >= datetime('now', '-30 days')
              AND beneficio_final_pct IS NOT NULL
            GROUP BY DATE(timestamp)
            ORDER BY fecha ASC
            """
            result = db.ejecutar_query(query)
            puntos = [dict(row) for row in result.rows] if result.rows else []

            # Calcular acumulado
            acumulado = 0.0
            for p in puntos:
                acumulado += (p.get('pnl_dia') or 0)
                p['pnl_acumulado'] = round(acumulado, 4)

            return jsonify({
                'timestamp': datetime.utcnow().isoformat() + 'Z',
                'puntos': puntos,
            })
        except Exception as e:
            logger.error(f"❌ /api/v1/stats/equity_curve error: {e}")
            return jsonify({'error': str(e)}), 500

    @app.route('/api/v1/stats/por_tf')
    def v1_stats_por_tf():
        """Stats de win rate agrupadas por timeframe."""
        try:
            from adapters.database import get_db
            db = get_db()
            if db is None:
                return jsonify({'error': 'BD no disponible'}), 503

            kpis = db.obtener_kpis_performance()
            return jsonify({
                'timestamp': datetime.utcnow().isoformat() + 'Z',
                'por_timeframe': kpis.get('por_timeframe', []),
            })
        except Exception as e:
            logger.error(f"❌ /api/v1/stats/por_tf error: {e}")
            return jsonify({'error': str(e)}), 500

    # ─────────────────────────────────────────────────────────────────────────
    # API v1 — sistema
    # ─────────────────────────────────────────────────────────────────────────

    @app.route('/api/v1/status')
    def v1_status():
        """Estado del sistema: threads, evento macro próximo."""
        try:
            threads_estado = {
                n: ('vivo' if t.is_alive() else 'muerto')
                for n, t in threads_detectores.items()
            }
            vivos = len([v for v in threads_estado.values() if v == 'vivo'])

            # Evento macro próximo (best-effort)
            evento_proximo = None
            try:
                from services.economic_calendar import EconomicCalendar
                cal = EconomicCalendar()
                eventos = cal.proximos_eventos(horas=24)
                if eventos:
                    evento_proximo = eventos[0]
            except Exception:
                pass

            return jsonify({
                'timestamp': datetime.utcnow().isoformat() + 'Z',
                'iniciado': estado_sistema.get('iniciado'),
                'threads': threads_estado,
                'threads_vivos': vivos,
                'threads_totales': len(threads_detectores),
                'evento_proximo': evento_proximo,
            })
        except Exception as e:
            logger.error(f"❌ /api/v1/status error: {e}")
            return jsonify({'error': str(e)}), 500

    # Mapeo de nombre de símbolo al ticker real almacenado en ohlcv (Yahoo Finance)
    _SYMBOL_TO_OHLCV = {
        'XAUUSD': 'GC=F',
        'GOLD':   'GC=F',
        'EURUSD': 'EURUSD=X',
    }

    @app.route('/api/v1/precio/<symbol>')
    def v1_precio(symbol: str):
        """Precio actual de un símbolo.

        Prioridades:
          1. Cache WebSocket (tiempo real, latencia ~0)
          2. TwelveData /price REST (cache 4s)
          3. Última vela OHLCV en BD (fallback)
        """
        try:
            sym_upper = symbol.upper()
            ohlcv_sym = _SYMBOL_TO_OHLCV.get(sym_upper, sym_upper)

            # ── Prioridad 1: cache WebSocket (precio recibido en tiempo real) ──
            try:
                from services.ws_price_feed import get_precio_ws
                precio_ws = get_precio_ws(sym_upper)
                if precio_ws is not None:
                    return jsonify({'symbol': sym_upper, 'precio': precio_ws,
                                    'timestamp': None, 'fuente': 'websocket'})
            except Exception:
                pass

            # ── Prioridad 2: TwelveData /price REST (cache 4s) ────────────────
            try:
                from adapters.data_provider import get_precio_tiempo_real
                precio_rt = get_precio_tiempo_real(ohlcv_sym)
                if precio_rt is not None:
                    return jsonify({'symbol': sym_upper, 'precio': precio_rt,
                                    'timestamp': None, 'fuente': 'realtime'})
            except Exception:
                pass

            # ── Prioridad 3: última vela de ohlcv en BD ───────────────────────
            from adapters.database import get_db
            db = get_db()
            if db is None:
                return jsonify({'error': 'BD no disponible'}), 503

            query = """
            SELECT close, timestamp FROM ohlcv
            WHERE symbol = ?
            ORDER BY timestamp DESC LIMIT 1
            """
            result = db.ejecutar_query(query, (ohlcv_sym,))
            if result.rows:
                row = dict(result.rows[0])
                return jsonify({'symbol': sym_upper, 'precio': row['close'],
                                'timestamp': row['timestamp'], 'fuente': 'ohlcv'})
            return jsonify({'symbol': sym_upper, 'precio': None, 'timestamp': None})
        except Exception as e:
            logger.error(f"❌ /api/v1/precio/{symbol} error: {e}")
            return jsonify({'error': str(e)}), 500

    @app.route('/api/v1/keys/uso')
    def v1_keys_uso():
        """Uso de API keys Twelve Data hoy."""
        try:
            from adapters.database import get_db
            db = get_db()
            if db is None:
                return jsonify({'error': 'BD no disponible'}), 503
            detalle = db.obtener_uso_keys_detalle_hoy()
            return jsonify({
                'timestamp': datetime.utcnow().isoformat() + 'Z',
                'keys': detalle,
            })
        except Exception as e:
            logger.error(f"❌ /api/v1/keys/uso error: {e}")
            return jsonify({'error': str(e)}), 500

    # ─────────────────────────────────────────────────────────────────────────
    # Web Push Notifications (VAPID)
    # ─────────────────────────────────────────────────────────────────────────

    @app.route('/api/v1/push/test', methods=['GET', 'POST'])
    def push_test():
        """Envía una notificación push de prueba a todos los suscriptores y devuelve el resultado."""
        from services.push_notifications import enviar_push_senal, _push_disponible
        from adapters.database import get_db
        import json as _json

        db = get_db()
        result = db.ejecutar_query('SELECT endpoint, created_at FROM push_subscriptions')
        subs = [dict(r) for r in result.rows] if result.rows else []

        enviados = enviar_push_senal(
            senal_id=0,
            titulo='🧪 Test push — BotTrading',
            cuerpo='Si ves esto, las notificaciones funcionan correctamente.',
        )
        return jsonify({
            'ok': True,
            'suscriptores_en_bd': len(subs),
            'enviados': enviados,
            'suscriptores': [
                {'endpoint': s['endpoint'][:80] + '...', 'created_at': s['created_at']}
                for s in subs
            ],
        })

    @app.route('/api/v1/push/vapid-public-key')
    def push_vapid_public_key():
        """Devuelve la clave pública VAPID para que el navegador pueda suscribirse."""
        key = os.environ.get('VAPID_PUBLIC_KEY', '')
        if not key:
            return jsonify({'error': 'VAPID no configurado'}), 503
        return jsonify({'publicKey': key})

    @app.route('/api/v1/push/subscribe', methods=['POST'])
    def push_subscribe():
        """Guarda la suscripción push del navegador en BD."""
        try:
            sub = request.get_json(force=True)
            if not sub or 'endpoint' not in sub:
                return jsonify({'error': 'Payload inválido'}), 400
            from adapters.database import get_db
            db = get_db()
            if db is None:
                return jsonify({'error': 'BD no disponible'}), 503
            import json as _json
            db.ejecutar_query(
                """INSERT OR REPLACE INTO push_subscriptions
                   (endpoint, subscription_json, created_at)
                   VALUES (?, ?, datetime('now'))""",
                (sub['endpoint'], _json.dumps(sub))
            )
            logger.info(f"📲 Push: nueva suscripción registrada ({sub['endpoint'][:60]}...)")
            return jsonify({'ok': True}), 201
        except Exception as e:
            logger.error(f"❌ /api/v1/push/subscribe error: {e}")
            return jsonify({'error': str(e)}), 500

    @app.route('/api/v1/push/unsubscribe', methods=['POST'])
    def push_unsubscribe():
        """Elimina la suscripción push del navegador de la BD."""
        try:
            sub = request.get_json(force=True)
            if not sub or 'endpoint' not in sub:
                return jsonify({'error': 'Payload inválido'}), 400
            from adapters.database import get_db
            db = get_db()
            if db is None:
                return jsonify({'error': 'BD no disponible'}), 503
            db.ejecutar_query(
                "DELETE FROM push_subscriptions WHERE endpoint = ?",
                (sub['endpoint'],)
            )
            return jsonify({'ok': True})
        except Exception as e:
            logger.error(f"❌ /api/v1/push/unsubscribe error: {e}")
            return jsonify({'error': str(e)}), 500

    # ─────────────────────────────────────────────────────────────────────────
    # Server-Sent Events (SSE) — eventos en tiempo real al frontend
    # ─────────────────────────────────────────────────────────────────────────

    @app.route('/api/v1/events')
    def sse_events():
        """Stream SSE de eventos en tiempo real (señales y precios).

        El frontend se conecta una vez y recibe:
          - event: senal  → nueva señal activada o actualización de estado
          - event: precio → actualización de precio de un activo
        """
        from flask import Response, stream_with_context
        from bridge.sse_broker import broker

        def _generar():
            import queue as _queue
            q = broker.suscribir()
            try:
                # Comentario inicial para que el navegador reconozca el stream
                yield ": conectado\n\n"
                while True:
                    try:
                        msg = q.get(timeout=25)
                        yield msg
                    except _queue.Empty:
                        # Timeout de 25 s → enviar heartbeat para mantener la conexión
                        yield ": ping\n\n"
            except GeneratorExit:
                pass
            finally:
                broker.desuscribir(q)

        return Response(
            stream_with_context(_generar()),
            mimetype='text/event-stream',
            headers={
                'Cache-Control':      'no-cache',
                'X-Accel-Buffering':  'no',     # Nginx: deshabilitar buffer
                'Content-Encoding':   'identity', # fuerza no-compresión → flush inmediato
                'Transfer-Encoding':  'chunked',  # cada yield → chunk inmediato al browser
            },
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Cancelar señal manualmente (requiere CANCEL_KEY)
    # ─────────────────────────────────────────────────────────────────────────

    @app.route('/api/v1/senales/<int:senal_id>/cancelar', methods=['POST'])
    def cancelar_senal_manual(senal_id):
        """Cancela manualmente una señal activa o pendiente.

        Body JSON: { "clave": "<CANCEL_KEY>" }
        Requiere que la variable de entorno CANCEL_KEY esté configurada.
        """
        import hmac
        CANCEL_KEY = os.environ.get('CANCEL_KEY', '')
        if not CANCEL_KEY:
            return jsonify({'error': 'Función no habilitada (CANCEL_KEY no configurada)'}), 503

        try:
            body = request.get_json(force=True) or {}
            clave = str(body.get('clave', ''))
            # Comparación en tiempo constante para evitar timing attacks
            if not hmac.compare_digest(clave, CANCEL_KEY):
                logger.warning(f"⚠️ Intento de cancelación con clave incorrecta (señal #{senal_id})")
                return jsonify({'error': 'Clave incorrecta'}), 403

            from adapters.database import get_db
            db = get_db()
            if db is None:
                return jsonify({'error': 'BD no disponible'}), 503

            # Verificar que la señal existe y está en estado cancelable
            r = db.ejecutar_query(
                "SELECT id, simbolo, direccion, estado FROM senales WHERE id = ?",
                (senal_id,)
            )
            if not r.rows:
                return jsonify({'error': f'Señal #{senal_id} no encontrada'}), 404

            senal = r.rows[0]
            estado_actual = senal['estado']
            ESTADOS_CANCELABLES = ('ACTIVA', 'PENDIENTE_CONFIRM', 'ESPERANDO')
            if estado_actual not in ESTADOS_CANCELABLES:
                return jsonify({
                    'error': f'La señal está en estado {estado_actual} — no se puede cancelar'
                }), 409

            ahora = datetime.now(timezone.utc).isoformat()
            db.ejecutar_query(
                "UPDATE senales SET estado = 'CANCELADA', ciclo_vida = 'CANCELADA', fecha_cierre = ? WHERE id = ?",
                (ahora, senal_id)
            )
            logger.info(f"🗑️  Señal #{senal_id} ({senal['simbolo']} {senal['direccion']}) cancelada manualmente")
            return jsonify({'ok': True, 'senal_id': senal_id, 'estado_previo': estado_actual})

        except Exception as e:
            logger.error(f"❌ /api/v1/senales/{senal_id}/cancelar error: {e}")
            return jsonify({'error': str(e)}), 500

    # ─────────────────────────────────────────────────────────────────────────
    # Editar estado de señal manualmente (requiere CANCEL_KEY)
    # ─────────────────────────────────────────────────────────────────────────

    @app.route('/api/v1/senales/<int:senal_id>/estado', methods=['PUT'])
    def editar_estado_senal(senal_id):
        """Actualiza manualmente el estado de una señal.

        Body JSON: { "clave": "<CANCEL_KEY>", "estado": "<NUEVO_ESTADO>" }
        Requiere que la variable de entorno CANCEL_KEY esté configurada.
        """
        import hmac
        CANCEL_KEY = os.environ.get('CANCEL_KEY', '')
        if not CANCEL_KEY:
            return jsonify({'error': 'Función no habilitada (CANCEL_KEY no configurada)'}), 503

        ESTADOS_VALIDOS = ('ACTIVA', 'PENDIENTE_CONFIRM', 'ESPERANDO',
                           'TP1', 'TP2', 'TP3', 'SL', 'CANCELADA', 'CADUCADA')

        try:
            body   = request.get_json(force=True) or {}
            clave  = str(body.get('clave', ''))
            estado = str(body.get('estado', '')).upper()

            if not hmac.compare_digest(clave, CANCEL_KEY):
                logger.warning(f"⚠️ Intento de edición con clave incorrecta (señal #{senal_id})")
                return jsonify({'error': 'Clave incorrecta'}), 403

            if estado not in ESTADOS_VALIDOS:
                return jsonify({'error': f'Estado no válido: {estado}'}), 400

            from adapters.database import get_db
            db = get_db()
            if db is None:
                return jsonify({'error': 'BD no disponible'}), 503

            r = db.ejecutar_query(
                "SELECT id, simbolo, direccion, estado, tp2, tp3, precio_entrada FROM senales WHERE id = ?",
                (senal_id,)
            )
            if not r.rows:
                return jsonify({'error': f'Señal #{senal_id} no encontrada'}), 404

            senal         = dict(r.rows[0])
            estado_previo = senal['estado']

            ahora = datetime.now(timezone.utc).isoformat()

            # Helper para detectar si la señal tiene más TPs por delante
            def _tiene_tp(campo):
                v = senal.get(campo)
                try:
                    return v is not None and float(v) > 0
                except (TypeError, ValueError):
                    return False

            # ── TP1: si hay TP2, NO cerrar — comportamiento idéntico al monitor automático
            if estado == 'TP1':
                if _tiene_tp('tp2'):
                    # Multi-TP: marcar hit, mover SL a breakeven, mantener ACTIVA
                    db.ejecutar_query(
                        """UPDATE senales
                           SET tp1_alcanzado = 1, fecha_tp1 = ?,
                               sl = precio_entrada,
                               estado = 'ACTIVA', ciclo_vida = 'ACTIVA', fecha_cierre = NULL
                           WHERE id = ?""",
                        (ahora, senal_id)
                    )
                    estado = 'ACTIVA'  # para el log/respuesta
                else:
                    # Single-TP: cerrar señal
                    db.ejecutar_query(
                        """UPDATE senales
                           SET estado = 'TP1', ciclo_vida = 'COMPLETA', fecha_cierre = ?,
                               tp1_alcanzado = 1, fecha_tp1 = ?
                           WHERE id = ?""",
                        (ahora, ahora, senal_id)
                    )

            # ── TP2: si hay TP3, NO cerrar
            elif estado == 'TP2':
                if _tiene_tp('tp3'):
                    db.ejecutar_query(
                        """UPDATE senales
                           SET tp2_alcanzado = 1, fecha_tp2 = ?,
                               sl = tp1,
                               estado = 'ACTIVA', ciclo_vida = 'ACTIVA', fecha_cierre = NULL
                           WHERE id = ?""",
                        (ahora, senal_id)
                    )
                    estado = 'ACTIVA'
                else:
                    db.ejecutar_query(
                        """UPDATE senales
                           SET estado = 'TP2', ciclo_vida = 'COMPLETA', fecha_cierre = ?,
                               tp2_alcanzado = 1, fecha_tp2 = ?
                           WHERE id = ?""",
                        (ahora, ahora, senal_id)
                    )

            # ── TP3: siempre cierra
            elif estado == 'TP3':
                db.ejecutar_query(
                    """UPDATE senales
                       SET estado = 'TP3', ciclo_vida = 'COMPLETA', fecha_cierre = ?,
                           tp3_alcanzado = 1, fecha_tp3 = ?
                       WHERE id = ?""",
                    (ahora, ahora, senal_id)
                )

            elif estado == 'SL':
                db.ejecutar_query(
                    """UPDATE senales
                       SET estado = 'SL', ciclo_vida = 'COMPLETA', fecha_cierre = ?,
                           sl_alcanzado = 1, fecha_sl = ?
                       WHERE id = ?""",
                    (ahora, ahora, senal_id)
                )

            elif estado in ('CANCELADA', 'CADUCADA'):
                db.ejecutar_query(
                    "UPDATE senales SET estado = ?, ciclo_vida = 'CANCELADA', fecha_cierre = ? WHERE id = ?",
                    (estado, ahora, senal_id)
                )

            else:
                # ACTIVA, PENDIENTE_CONFIRM, ESPERANDO — reactivar: limpiar fecha_cierre
                _ciclo_map = {
                    'ACTIVA': 'ACTIVA', 'PENDIENTE_CONFIRM': 'PREPARADA', 'ESPERANDO': 'PREPARADA',
                }
                ciclo_vida = _ciclo_map.get(estado, 'PREPARADA')
                db.ejecutar_query(
                    "UPDATE senales SET estado = ?, ciclo_vida = ?, fecha_cierre = NULL WHERE id = ?",
                    (estado, ciclo_vida, senal_id)
                )

            logger.info(f"✏️  Señal #{senal_id} ({senal['simbolo']} {senal['direccion']}) "
                        f"editada: {estado_previo} → {estado}")
            return jsonify({'ok': True, 'senal_id': senal_id,
                            'estado_previo': estado_previo, 'estado_nuevo': estado})

        except Exception as e:
            logger.error(f"❌ /api/v1/senales/{senal_id}/estado error: {e}")
            return jsonify({'error': str(e)}), 500

    # Servir el Service Worker desde la raíz (requerido por la spec)
    @app.route('/sw.js')
    def service_worker():
        from flask import send_from_directory
        return send_from_directory(
            str(_FRONTEND_DIR / 'static' / 'js'),
            'sw.js',
            mimetype='application/javascript'
        )

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
