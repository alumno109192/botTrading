"""
adapters/ctrader_broker.py — Ejecución automática vía cTrader Open API (Pepperstone).

Usa WebSocket + JSON (puerto 5036) para conectarse al backend de cTrader sin
necesidad de MetaTrader5. Compatible con Render (Linux).

FLUJO DE AUTENTICACIÓN:
  1. WebSocket abierto → ProtoOAApplicationAuthReq (clientId + clientSecret)
  2. ProtoOAApplicationAuthRes → ProtoOAAccountAuthReq (ctidTraderAccountId + accessToken)
  3. ProtoOAAccountAuthRes → ProtoOAGetSymbolsReq (para obtener symbolId de XAUUSD)
  4. Ya listo para operar → ProtoOANewOrderReq / ProtoOAClosePositionReq

OBTENER LAS CREDENCIALES:
  1. Registrarse en Pepperstone y abrir una cuenta demo cTrader
  2. Ir a https://openapi.ctrader.com → Crear aplicación
  3. En el Playground de la app obtener el access_token y el ctidTraderAccountId
  4. Configurar las variables de entorno (ver abajo)

Variables de entorno requeridas:
  CTRADER_AUTO_TRADE    — 'true' para activar ejecución real (default: false)
  CTRADER_CLIENT_ID     — Client ID de la aplicación Open API
  CTRADER_CLIENT_SECRET — Client Secret de la aplicación Open API
  CTRADER_ACCESS_TOKEN  — Access token OAuth2 (del Playground, dura ~30 días)
  CTRADER_REFRESH_TOKEN — Refresh token (para renovar el access token)
  CTRADER_ACCOUNT_ID    — ctidTraderAccountId de la cuenta Pepperstone
  CTRADER_SYMBOL        — Nombre del símbolo en cTrader (ej. XAUUSD)
  CTRADER_VOLUME        — Volumen en unidades cTrader (ver nota abajo)
  CTRADER_DEMO          — 'true' para cuenta demo, 'false' para live (default: true)

NOTA SOBRE VOLUMEN (CTRADER_VOLUME):
  cTrader Open API usa "volume" en unidades. Para XAUUSD en Pepperstone:
    100  = 0.01 lote (1 oz de oro)
    1000 = 0.10 lote (10 oz de oro)
  Usar 100 como punto de partida; verificar el tamaño mínimo con Pepperstone.
"""

import os
import json
import time
import uuid
import logging
import threading
from typing import Optional

try:
    import websocket  # websocket-client
    _WS_DISPONIBLE = True
except ImportError:
    _WS_DISPONIBLE = False

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger('bottrading.ctrader')


# ─── Tipos de payload cTrader Open API ────────────────────────────────────────

class _PayloadType:
    """Constantes para los payloadType del protocolo cTrader Open API."""
    HEARTBEAT                     = 51
    APP_AUTH_REQ                  = 2100
    APP_AUTH_RES                  = 2101
    ACCOUNT_AUTH_REQ              = 2102
    ACCOUNT_AUTH_RES              = 2103
    NEW_ORDER_REQ                 = 2106
    CLOSE_POSITION_REQ            = 2109
    GET_SYMBOLS_REQ               = 2114
    GET_SYMBOLS_RES               = 2115
    EXECUTION_EVENT               = 2126
    SUBSCRIBE_SPOTS_REQ           = 2127
    SUBSCRIBE_SPOTS_RES           = 2128
    UNSUBSCRIBE_SPOTS_REQ         = 2129
    SPOT_EVENT                    = 2131
    GET_ACCOUNT_LIST_BY_TOKEN_REQ = 2150
    GET_ACCOUNT_LIST_BY_TOKEN_RES = 2151
    ERROR_RES                     = 2142
    REFRESH_TOKEN_REQ             = 2162
    REFRESH_TOKEN_RES             = 2163


PT = _PayloadType()


# ─── Adaptador cTrader ─────────────────────────────────────────────────────────

class CTraderBroker:
    """
    Adaptador para cTrader Open API. Mantiene una conexión WebSocket persistente
    en un hilo de fondo y expone métodos síncronos compatibles con MT5Broker.
    """

    TIMEOUT_OP = 15   # segundos para esperar respuesta de una operación
    TIMEOUT_READY = 30  # segundos para esperar autenticación completa

    def __init__(self):
        self.auto_trade     = os.getenv('CTRADER_AUTO_TRADE', 'false').lower() == 'true'
        self.client_id      = os.getenv('CTRADER_CLIENT_ID', '')
        self.client_secret  = os.getenv('CTRADER_CLIENT_SECRET', '')
        self.access_token   = os.getenv('CTRADER_ACCESS_TOKEN', '')
        self.refresh_token  = os.getenv('CTRADER_REFRESH_TOKEN', '')
        self.account_id     = int(os.getenv('CTRADER_ACCOUNT_ID', '0'))
        self.symbol_name    = os.getenv('CTRADER_SYMBOL', 'XAUUSD')
        self.volume         = int(os.getenv('CTRADER_VOLUME', '100'))
        demo                = os.getenv('CTRADER_DEMO', 'true').lower() == 'true'

        host = 'demo.ctraderapi.com' if demo else 'live.ctraderapi.com'
        self._ws_url = f"wss://{host}:5036"

        self._ws: Optional[object] = None
        self._ws_thread: Optional[threading.Thread] = None
        self._ws_lock = threading.Lock()

        # Peticiones pendientes: clientMsgId → {'event': Event, 'result': Any}
        self._pending: dict = {}
        self._pending_lock = threading.Lock()

        # Estado de la sesión
        self._autenticado = False
        self._conectado = False
        self._symbol_id: Optional[int] = None

        # Último precio spot recibido (para get_precio_actual)
        self._ultimo_bid: Optional[float] = None
        self._spot_event = threading.Event()

        # Heartbeat
        self._heartbeat_thread: Optional[threading.Thread] = None
        self._parar_heartbeat = threading.Event()

        if not self.auto_trade:
            logger.debug("CTraderBroker: auto_trade=false, modo simulación")
            return

        if not _WS_DISPONIBLE:
            logger.error(
                "❌ CTraderBroker: websocket-client no instalado. "
                "Ejecuta: pip install websocket-client"
            )
            self.auto_trade = False
            return

        if not self.client_id or not self.client_secret or not self.access_token or not self.account_id:
            logger.error(
                "❌ CTraderBroker: credenciales incompletas. "
                "Configura CTRADER_CLIENT_ID, CTRADER_CLIENT_SECRET, "
                "CTRADER_ACCESS_TOKEN y CTRADER_ACCOUNT_ID"
            )
            self.auto_trade = False
            return

        logger.info(f"✅ CTraderBroker: iniciando → {self._ws_url} | cuenta {self.account_id}")
        self._iniciar_conexion()

    # ──────────────────────────────────────────────────────────────────────────
    # WebSocket — gestión de la conexión
    # ──────────────────────────────────────────────────────────────────────────

    def _iniciar_conexion(self):
        """Lanza el hilo de reconexión automática."""
        self._ws_thread = threading.Thread(
            target=self._run_ws_loop,
            daemon=True,
            name="ctrader-ws"
        )
        self._ws_thread.start()

    def _run_ws_loop(self):
        """Loop de reconexión automática en hilo de fondo."""
        while self.auto_trade:
            try:
                logger.info(f"CTraderBroker: conectando a {self._ws_url}...")
                ws = websocket.WebSocketApp(
                    self._ws_url,
                    on_open=self._on_open,
                    on_message=self._on_message,
                    on_error=self._on_error,
                    on_close=self._on_close,
                )
                with self._ws_lock:
                    self._ws = ws
                ws.run_forever(ping_interval=0)
            except Exception as exc:
                logger.error(f"CTraderBroker: error en loop WebSocket: {exc}")

            if not self.auto_trade:
                break

            self._autenticado = False
            self._conectado = False
            logger.info("CTraderBroker: reconectando en 5s...")
            time.sleep(5)

    def _on_open(self, ws):
        logger.info("CTraderBroker: WebSocket abierto — autenticando aplicación...")
        self._conectado = True
        self._autenticado = False
        self._symbol_id = None
        self._parar_heartbeat.clear()

        # Arrancar heartbeat
        self._heartbeat_thread = threading.Thread(
            target=self._run_heartbeat,
            daemon=True,
            name="ctrader-heartbeat"
        )
        self._heartbeat_thread.start()

        # Paso 1: autenticar la aplicación
        self._enviar(PT.APP_AUTH_REQ, {
            "clientId": self.client_id,
            "clientSecret": self.client_secret,
        }, msg_id="app_auth")

    def _on_message(self, ws, raw):
        try:
            data = json.loads(raw)
        except Exception:
            return

        payload_type = data.get("payloadType")
        payload      = data.get("payload") or {}
        msg_id       = data.get("clientMsgId", "")

        # ── Heartbeat ──────────────────────────────────────────────────────
        if payload_type == PT.HEARTBEAT:
            return

        # ── Autenticación de la aplicación ────────────────────────────────
        elif payload_type == PT.APP_AUTH_RES:
            logger.info("CTraderBroker: aplicación autenticada — autenticando cuenta...")
            self._enviar(PT.ACCOUNT_AUTH_REQ, {
                "ctidTraderAccountId": self.account_id,
                "accessToken": self.access_token,
            }, msg_id="acc_auth")

        # ── Autenticación de la cuenta ────────────────────────────────────
        elif payload_type == PT.ACCOUNT_AUTH_RES:
            logger.info(f"CTraderBroker: cuenta {self.account_id} autenticada — obteniendo símbolos...")
            self._autenticado = True
            self._enviar(PT.GET_SYMBOLS_REQ, {
                "ctidTraderAccountId": self.account_id,
            }, msg_id="get_symbols")

        # ── Lista de símbolos ─────────────────────────────────────────────
        elif payload_type == PT.GET_SYMBOLS_RES:
            self._procesar_simbolos(payload)

        # ── Evento de ejecución (respuesta a NewOrder / ClosePosition) ────
        elif payload_type == PT.EXECUTION_EVENT:
            logger.info(f"CTraderBroker: ExecutionEvent | {payload}")
            self._resolver_pendiente(msg_id, payload)

        # ── Error de la API ───────────────────────────────────────────────
        elif payload_type == PT.ERROR_RES:
            error_code = payload.get("errorCode", "?")
            desc = payload.get("description", "")
            logger.error(f"❌ CTraderBroker: API error: {error_code} — {desc}")
            self._resolver_pendiente(msg_id, None)

            # Renovar token si está expirado
            if "OA_AUTH_TOKEN_EXPIRED" in str(error_code) and self.refresh_token:
                self._renovar_token()

        # ── Spot (precio en tiempo real) ──────────────────────────────────
        elif payload_type == PT.SPOT_EVENT:
            bid_raw = payload.get("bid")
            if bid_raw:
                # cTrader devuelve precio × 100000 en formato entero
                self._ultimo_bid = bid_raw / 100000.0
                self._spot_event.set()
                self._spot_event.clear()

        # ── Refresh token ─────────────────────────────────────────────────
        elif payload_type == PT.REFRESH_TOKEN_RES:
            nuevo_token = payload.get("accessToken", "")
            nuevo_refresh = payload.get("refreshToken", "")
            if nuevo_token:
                self.access_token = nuevo_token
                logger.info("CTraderBroker: access_token renovado")
            if nuevo_refresh:
                self.refresh_token = nuevo_refresh

    def _on_error(self, ws, error):
        logger.error(f"CTraderBroker: WebSocket error: {error}")
        self._autenticado = False
        self._conectado = False
        self._parar_heartbeat.set()
        # Resolver todos los pendientes con error
        with self._pending_lock:
            for entry in self._pending.values():
                entry['result'] = None
                entry['event'].set()

    def _on_close(self, ws, code, reason):
        logger.warning(f"CTraderBroker: conexión cerrada (code={code}, reason={reason})")
        self._autenticado = False
        self._conectado = False
        self._parar_heartbeat.set()

    # ──────────────────────────────────────────────────────────────────────────
    # Heartbeat — mantener conexión viva
    # ──────────────────────────────────────────────────────────────────────────

    def _run_heartbeat(self):
        """Envía un heartbeat cada 10 segundos para mantener la conexión activa."""
        while not self._parar_heartbeat.wait(timeout=10):
            if self._conectado and self._ws:
                try:
                    self._enviar(PT.HEARTBEAT, {})
                except Exception as exc:
                    logger.debug(f"CTraderBroker: error enviando heartbeat: {exc}")
                    break

    # ──────────────────────────────────────────────────────────────────────────
    # Helpers internos
    # ──────────────────────────────────────────────────────────────────────────

    def _enviar(self, payload_type: int, payload: dict, msg_id: str = "") -> str:
        """Serializa y envía un mensaje JSON. Retorna el clientMsgId usado."""
        if not msg_id:
            msg_id = str(uuid.uuid4())
        msg = json.dumps({
            "clientMsgId": msg_id,
            "payloadType": payload_type,
            "payload": payload,
        })
        with self._ws_lock:
            if self._ws:
                self._ws.send(msg)
        return msg_id

    def _resolver_pendiente(self, msg_id: str, resultado):
        """Resuelve una petición pendiente por su clientMsgId."""
        if not msg_id:
            return
        with self._pending_lock:
            entry = self._pending.get(msg_id)
        if entry:
            entry['result'] = resultado
            entry['event'].set()

    def _enviar_y_esperar(self, payload_type: int, payload: dict) -> Optional[dict]:
        """
        Envía un mensaje y bloquea hasta recibir respuesta o timeout.
        Retorna el payload de la respuesta, o None si falla.
        """
        msg_id = str(uuid.uuid4())
        ev = threading.Event()
        with self._pending_lock:
            self._pending[msg_id] = {'event': ev, 'result': None}
        try:
            self._enviar(payload_type, payload, msg_id=msg_id)
            if ev.wait(timeout=self.TIMEOUT_OP):
                with self._pending_lock:
                    return self._pending[msg_id]['result']
            logger.warning(f"CTraderBroker: timeout esperando respuesta (msgId={msg_id})")
            return None
        finally:
            with self._pending_lock:
                self._pending.pop(msg_id, None)

    def _procesar_simbolos(self, payload: dict):
        """Extrae el symbolId del símbolo configurado de la respuesta de GET_SYMBOLS."""
        simbolos = payload.get("symbol", [])
        for sym in simbolos:
            nombre = sym.get("symbolName", "")
            if nombre.upper() == self.symbol_name.upper():
                self._symbol_id = sym.get("symbolId")
                logger.info(
                    f"✅ CTraderBroker: symbolId de {self.symbol_name} = {self._symbol_id}"
                )
                return
        # Si no se encontró con nombre exacto, buscar con startswith
        for sym in simbolos:
            nombre = sym.get("symbolName", "")
            if nombre.upper().startswith(self.symbol_name.upper()):
                self._symbol_id = sym.get("symbolId")
                logger.info(
                    f"✅ CTraderBroker: symbolId de {self.symbol_name} (aprox) = {self._symbol_id} ({nombre})"
                )
                return
        logger.warning(
            f"⚠️ CTraderBroker: símbolo {self.symbol_name!r} no encontrado entre "
            f"{len(simbolos)} símbolos disponibles"
        )

    def _esperar_listo(self) -> bool:
        """Espera a que la conexión esté autenticada y el symbolId disponible."""
        inicio = time.time()
        while time.time() - inicio < self.TIMEOUT_READY:
            if self._autenticado and self._symbol_id is not None:
                return True
            time.sleep(0.2)
        logger.warning(
            f"CTraderBroker: timeout esperando estado listo "
            f"(autenticado={self._autenticado}, symbolId={self._symbol_id})"
        )
        return False

    def _renovar_token(self):
        """Intenta renovar el access_token usando el refresh_token."""
        if not self.refresh_token:
            logger.warning("CTraderBroker: no hay refresh_token para renovar")
            return
        logger.info("CTraderBroker: renovando access_token...")
        self._enviar(PT.REFRESH_TOKEN_REQ, {"refreshToken": self.refresh_token})

    # ──────────────────────────────────────────────────────────────────────────
    # API pública — misma interfaz que MT5Broker
    # ──────────────────────────────────────────────────────────────────────────

    def calcular_lotes(self, entry: float, sl: float) -> int:
        """Devuelve el volumen configurado en CTRADER_VOLUME (en unidades cTrader)."""
        return self.volume

    def abrir_operacion(self, senal_data: dict) -> Optional[str]:
        """
        Abre una orden de mercado en cTrader.

        Campos esperados en senal_data:
            direccion  : 'BUY' | 'SELL'
            entry      : float — precio de entrada estimado
            sl         : float — stop loss en precio absoluto
            tp1        : float — take profit en precio absoluto
            timeframe  : str   — ej. '1H', '15M'
            score      : int   — puntuación de la señal

        Retorna el positionId como string, o None si falló.
        """
        if not self.auto_trade:
            return None

        direccion = str(senal_data.get('direccion', '')).upper()
        if direccion not in ('BUY', 'SELL'):
            logger.warning(f"CTraderBroker: dirección inválida {direccion!r}")
            return None

        if not self._esperar_listo():
            logger.error("CTraderBroker: sistema no listo para operar (sin autenticación)")
            return None

        entry = float(senal_data.get('entry', 0))
        sl    = float(senal_data.get('sl', 0))
        tp1   = float(senal_data.get('tp1', 0))
        tf    = str(senal_data.get('timeframe', ''))
        score = int(senal_data.get('score', 0))

        logger.info(
            f"CTraderBroker: abriendo {direccion} {self.volume} unidades "
            f"@ {entry} | SL {sl} | TP1 {tp1} | TF {tf} | Score {score}"
        )

        payload = {
            "ctidTraderAccountId": self.account_id,
            "symbolId": self._symbol_id,
            "orderType": "MARKET",
            "tradeSide": direccion,       # "BUY" o "SELL"
            "volume": self.volume,
            "stopLoss": sl,
            "takeProfit": tp1,
            "comment": f"BotGold {tf} sc{score}",
        }

        resultado = self._enviar_y_esperar(PT.NEW_ORDER_REQ, payload)

        if resultado is None:
            logger.error("❌ CTraderBroker: no se recibió confirmación de la orden")
            return None

        # El ExecutionEvent puede contener 'position' o 'positionId' directamente
        posicion = resultado.get("position") or {}
        position_id = (
            posicion.get("positionId")
            or resultado.get("positionId")
        )

        if position_id:
            logger.info(
                f"✅ CTrader orden abierta | positionId={position_id} | "
                f"{direccion} vol={self.volume} @ {entry} | SL {sl} | TP1 {tp1}"
            )
            return str(position_id)

        logger.warning(f"⚠️ CTraderBroker: orden ejecutada pero sin positionId: {resultado}")
        return None

    def cerrar_operacion(self, position_id: str) -> bool:
        """
        Cierra una posición abierta por su positionId.

        Retorna True si el cierre fue exitoso.
        """
        if not self.auto_trade:
            return False

        if not self._esperar_listo():
            logger.error("CTraderBroker: sistema no listo para cerrar posición")
            return False

        logger.info(f"CTraderBroker: cerrando posición {position_id}...")

        resultado = self._enviar_y_esperar(PT.CLOSE_POSITION_REQ, {
            "ctidTraderAccountId": self.account_id,
            "positionId": int(position_id),
            "volume": self.volume,
        })

        if resultado is None:
            logger.error(f"❌ CTraderBroker: cierre de posición {position_id} falló (timeout)")
            return False

        logger.info(f"✅ CTrader posición {position_id} cerrada")
        return True

    def get_precio_actual(self) -> Optional[float]:
        """
        Obtiene el precio bid actual del símbolo via suscripción spot momentánea.
        Retorna el precio en formato decimal, o None si no está disponible.
        """
        if not self.auto_trade or not self._autenticado or self._symbol_id is None:
            return None

        # Limpiar event anterior y suscribirse
        self._spot_event.clear()
        self._ultimo_bid = None

        self._enviar(PT.SUBSCRIBE_SPOTS_REQ, {
            "ctidTraderAccountId": self.account_id,
            "symbolId": [self._symbol_id],
        })

        # Esperar el primer SpotEvent
        recibido = self._spot_event.wait(timeout=5.0)

        # Desuscribirse siempre
        try:
            self._enviar(PT.UNSUBSCRIBE_SPOTS_REQ, {
                "ctidTraderAccountId": self.account_id,
                "symbolId": [self._symbol_id],
            })
        except Exception:
            pass

        if recibido and self._ultimo_bid:
            return self._ultimo_bid

        logger.warning("CTraderBroker: timeout obteniendo precio spot")
        return None

    def estado(self) -> dict:
        """Retorna el estado actual de la conexión (útil para diagnóstico)."""
        return {
            "auto_trade": self.auto_trade,
            "conectado": self._conectado,
            "autenticado": self._autenticado,
            "symbol_id": self._symbol_id,
            "symbol": self.symbol_name,
            "account_id": self.account_id,
            "volume": self.volume,
            "ultimo_bid": self._ultimo_bid,
            "ws_url": self._ws_url,
        }


# ─── Instancia singleton ───────────────────────────────────────────────────────
broker = CTraderBroker()
