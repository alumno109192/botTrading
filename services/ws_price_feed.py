"""
services/ws_price_feed.py — Feed de precios en tiempo real (Twelve Data WebSocket)

Conecta al WebSocket de Twelve Data (plan Grow: 8 créditos WS), suscribe los
símbolos de interés y publica cada tick al frontend vía SSE.

También mantiene un cache en memoria para que el endpoint
/api/v1/precio/{symbol} devuelva el precio más fresco sin consultar Turso.

Uso (desde app.py al arrancar):
    from services.ws_price_feed import iniciar_feed
    iniciar_feed()
"""

import json
import logging
import os
import threading
import time

import websocket

logger = logging.getLogger('bottrading')

# ── Mapa símbolo Twelve Data → símbolo interno ────────────────────────────────
_TD_TO_INTERNO: dict[str, str] = {
    'XAU/USD': 'XAUUSD',
    'EUR/USD': 'EURUSD',
    'GBP/USD': 'GBPUSD',
}

# ── Cache en memoria ──────────────────────────────────────────────────────────
# { 'XAUUSD': (precio_float, timestamp_unix) }
_precio_cache: dict[str, tuple[float, float]] = {}
_cache_lock = threading.Lock()

_MAX_EDAD_CACHE = 60  # segundos; si el precio es más viejo se considera stale


def get_precio_ws(simbolo: str) -> float | None:
    """Devuelve el último precio recibido por WS o None si es stale (>60 s)."""
    with _cache_lock:
        entry = _precio_cache.get(simbolo)
    if entry is None:
        return None
    precio, ts = entry
    if time.time() - ts > _MAX_EDAD_CACHE:
        return None
    return precio


# ── Feed ─────────────────────────────────────────────────────────────────────

class TDWebSocketFeed:
    """Cliente WebSocket Twelve Data — precios en tiempo real."""

    _TD_WS_URL          = 'wss://ws.twelvedata.com/v1/quotes/price'
    _HEARTBEAT_INTERVAL = 10   # segundos entre pings manuales
    _RECONNECT_DELAY    = 5    # segundos de espera antes de reconectar

    def __init__(self, api_key: str, symbols: list[str]):
        self._api_key  = api_key
        self._symbols  = symbols   # formato TD: ['XAU/USD', 'EUR/USD']
        self._ws       = None
        self._running  = False

    def start(self) -> None:
        """Arranca el feed en un thread daemon (no bloquea)."""
        self._running = True
        t = threading.Thread(target=self._run_loop, name='WS-PriceFeed', daemon=True)
        t.start()
        logger.info('🔌 [WS] Price feed iniciado — símbolos: %s', self._symbols)

    def stop(self) -> None:
        self._running = False
        if self._ws:
            self._ws.close()

    # ── Loop de reconexión ────────────────────────────────────────────────

    def _run_loop(self) -> None:
        while self._running:
            try:
                url = f"{self._TD_WS_URL}?apikey={self._api_key}"
                self._ws = websocket.WebSocketApp(
                    url,
                    on_open    = self._on_open,
                    on_message = self._on_message,
                    on_error   = self._on_error,
                    on_close   = self._on_close,
                )
                # ping_interval=0: gestionamos heartbeat manualmente
                self._ws.run_forever(ping_interval=0)
            except Exception as exc:
                logger.error('❌ [WS] Error en run_forever: %s', exc)

            if self._running:
                logger.info('🔄 [WS] Reconectando en %ds…', self._RECONNECT_DELAY)
                time.sleep(self._RECONNECT_DELAY)

    # ── Callbacks ─────────────────────────────────────────────────────────

    def _on_open(self, ws) -> None:
        logger.info('✅ [WS] Conectado a Twelve Data')
        ws.send(json.dumps({
            'action': 'subscribe',
            'params': {'symbols': ','.join(self._symbols)},
        }))
        # Heartbeat en thread aparte para no bloquear el loop principal
        t = threading.Thread(target=self._heartbeat_loop, args=(ws,), daemon=True)
        t.start()

    def _on_message(self, ws, raw: str) -> None:
        try:
            msg = json.loads(raw)
        except Exception:
            return

        event = msg.get('event')

        if event == 'price':
            td_sym = msg.get('symbol', '')
            precio = msg.get('price')
            if precio is None or td_sym not in _TD_TO_INTERNO:
                return

            precio  = float(precio)
            simbolo = _TD_TO_INTERNO[td_sym]

            # Actualizar cache
            with _cache_lock:
                _precio_cache[simbolo] = (precio, time.time())

            # Publicar por SSE → frontend actualiza en tiempo real
            try:
                from bridge.sse_broker import broker
                broker.publicar_precio(symbol=simbolo, precio=precio)
            except Exception as exc:
                logger.debug('[WS] SSE publish error: %s', exc)

        elif event == 'subscribe-status':
            ok   = msg.get('success', [])
            fail = msg.get('fails', [])
            logger.info('📡 [WS] Suscripción — OK: %s | FAIL: %s', ok, fail)

        elif event == 'heartbeat':
            pass  # el servidor envía su propio heartbeat; lo ignoramos

        else:
            logger.debug('[WS] mensaje desconocido: %s', msg)

    def _on_error(self, ws, error) -> None:
        logger.warning('⚠️ [WS] Error: %s', error)

    def _on_close(self, ws, code, msg) -> None:
        logger.info('🔌 [WS] Conexión cerrada (code=%s)', code)

    def _heartbeat_loop(self, ws) -> None:
        """Envía heartbeat cada N segundos para mantener la conexión viva."""
        while self._running:
            time.sleep(self._HEARTBEAT_INTERVAL)
            try:
                ws.send(json.dumps({'action': 'heartbeat'}))
            except Exception:
                break  # ws ya cerrado; el loop principal reconectará


# ── Inicialización (singleton) ────────────────────────────────────────────────

_feed_instance: TDWebSocketFeed | None = None
_feed_lock = threading.Lock()


def iniciar_feed(
    api_key: str        = None,
    symbols: list[str]  = None,
) -> bool:
    """Arranca el feed WS (idempotente). Devuelve True si arrancó ahora.

    Args:
        api_key: clave Twelve Data (por defecto lee TWELVE_DATA_API_KEY del env).
        symbols: lista de símbolos en formato TD, p.ej. ['XAU/USD', 'EUR/USD'].
                 Por defecto suscribe ambos.
    """
    global _feed_instance
    with _feed_lock:
        if _feed_instance is not None:
            logger.info('ℹ️ [WS] Feed ya activo — no se duplica')
            return False

        key = api_key or os.environ.get('TWELVE_DATA_API_KEY', '').strip()
        if not key:
            logger.warning('⚠️ [WS] TWELVE_DATA_API_KEY no configurada — feed desactivado')
            return False

        syms = symbols or ['XAU/USD', 'EUR/USD']
        _feed_instance = TDWebSocketFeed(api_key=key, symbols=syms)
        _feed_instance.start()
        return True
