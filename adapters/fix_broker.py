"""
adapters/fix_broker.py — Ejecución automática vía cTrader FIX API (Pepperstone).

Usa el protocolo FIX 4.4 sobre SSL/TCP para conectarse directamente al servidor
de cTrader sin necesidad de MetaTrader5 ni de la Open API WebSocket.

CONEXIONES:
  - Quote  (precios):  demo-us-eqx-01.p.c-trader.com:5211 (SSL)
  - Trade  (órdenes):  demo-us-eqx-01.p.c-trader.com:5212 (SSL)

FLUJO DE SESIÓN:
  1. Abrir socket SSL → enviar Logon (35=A)
  2. Recibir Logon de respuesta → sesión activa
  3. Enviar Heartbeat (35=0) cada HeartBtInt segundos
  4. Enviar NewOrderSingle (35=D) para abrir posiciones
  5. Recibir ExecutionReport (35=8) como confirmación
  6. Enviar OrderCancelRequest (35=F) para cerrar

Variables de entorno requeridas:
  FIX_AUTO_TRADE   — 'true' para activar ejecución real (default: false)
  FIX_HOST         — Hostname del servidor FIX (default: demo-us-eqx-01.p.c-trader.com)
  FIX_QUOTE_PORT   — Puerto Quote SSL (default: 5211)
  FIX_TRADE_PORT   — Puerto Trade SSL (default: 5212)
  FIX_SENDER_COMP  — SenderCompID (ej: demo.pepperstone.5288500)
  FIX_TARGET_COMP  — TargetCompID (default: cServer)
  FIX_USERNAME     — Número de cuenta (ej: 5288500)
  FIX_PASSWORD     — Contraseña de la cuenta
  FIX_SYMBOL       — Símbolo (default: XAUUSD)
  FIX_LOTES        — Lotes por operación (default: 0.01)
"""

import os
import ssl
import socket
import threading
import time
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

try:
    import simplefix
    _SIMPLEFIX_OK = True
except ImportError:
    _SIMPLEFIX_OK = False

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger('bottrading.fix')

# Separador FIX (SOH)
SOH = b'\x01'

# ─── Constantes FIX 4.4 ───────────────────────────────────────────────────────
BEGIN_STRING   = b'FIX.4.4'
MSGTYPE_LOGON  = b'A'
MSGTYPE_LOGOUT = b'5'
MSGTYPE_HB     = b'0'
MSGTYPE_TEST   = b'1'
MSGTYPE_RESEND = b'2'
MSGTYPE_REJECT = b'3'
MSGTYPE_ORDER  = b'D'   # NewOrderSingle
MSGTYPE_CANCEL = b'F'   # OrderCancelRequest
MSGTYPE_EXEC   = b'8'   # ExecutionReport

SIDE_BUY  = b'1'
SIDE_SELL = b'2'
ORD_TYPE_MARKET = b'1'
ORD_TYPE_LIMIT  = b'2'
ORD_TYPE_STOP   = b'3'


def _fix_utcnow() -> bytes:
    """Timestamp FIX en formato YYYYMMDD-HH:MM:SS.sss (UTC)."""
    now = datetime.now(timezone.utc)
    return now.strftime('%Y%m%d-%H:%M:%S.%f')[:23].encode()


def _fix_utcdate() -> bytes:
    """Fecha FIX en formato YYYYMMDD."""
    return datetime.now(timezone.utc).strftime('%Y%m%d').encode()


class FIXSession:
    """
    Sesión FIX 4.4 de bajo nivel sobre SSL/TCP.
    Gestiona reconexión, números de secuencia y heartbeats en un hilo de fondo.
    """

    RECV_BUFSIZE = 4096
    HB_INTERVAL  = 30   # segundos entre heartbeats
    TIMEOUT_RESP = 15   # segundos esperando respuesta a una orden

    def __init__(
        self,
        host: str,
        port: int,
        sender_comp: str,
        target_comp: str,
        sender_sub: str,
        username: str,
        password: str,
        on_exec_report=None,
        on_quote=None,
    ):
        self.host        = host
        self.port        = port
        self.sender_comp = sender_comp.encode()
        self.target_comp = target_comp.encode()
        self.sender_sub  = sender_sub.encode()
        self.username    = username.encode()
        self.password    = password.encode()

        self._on_exec_report = on_exec_report  # callback(fields: dict)
        self._on_quote       = on_quote        # callback(symbol: str, bid: float, ask: float)

        self._sock: Optional[ssl.SSLSocket] = None
        self._lock = threading.Lock()

        self._seq_out = 1      # próximo número de secuencia de salida
        self._seq_in  = 1      # próximo número de secuencia de entrada esperado

        self.activo      = False   # True una vez logoneado
        self._corriendo  = False   # True mientras el loop de reconexión está vivo
        self._recv_buf   = b''

        # Peticiones pendientes: clOrdID → {'event': Event, 'result': dict|None}
        self._pending: dict = {}
        self._pending_lock  = threading.Lock()

        self._hb_thread: Optional[threading.Thread] = None
        self._parar_hb = threading.Event()

    # ─────────────────────────────────────────────────────────────────────────
    # Construcción de mensajes FIX
    # ─────────────────────────────────────────────────────────────────────────

    def _build_msg(self, msg_type: bytes, fields: list[tuple]) -> bytes:
        """
        Construye un mensaje FIX 4.4 completo con cabecera, cuerpo y checksum.

        fields: lista de (tag_int, value_bytes)
        """
        now = _fix_utcnow()
        with self._lock:
            seq = self._seq_out
            self._seq_out += 1

        # Campos de cabecera (excluyendo BeginString, BodyLength, MsgType)
        header_fields = [
            (49, self.sender_comp),
            (56, self.target_comp),
            (34, str(seq).encode()),
            (52, now),
        ]
        if self.sender_sub:
            header_fields.insert(2, (50, self.sender_sub))

        # Cuerpo = header_fields + 35=MsgType + fields de usuario
        body_parts = []
        body_parts.append(b'35=' + msg_type + SOH)
        for tag, val in header_fields:
            body_parts.append(str(tag).encode() + b'=' + val + SOH)
        for tag, val in fields:
            body_parts.append(str(tag).encode() + b'=' + val + SOH)

        body = b''.join(body_parts)
        body_len = str(len(body)).encode()

        # Frame completo
        head = b'8=' + BEGIN_STRING + SOH + b'9=' + body_len + SOH
        msg  = head + body

        # Checksum
        chk = sum(msg) % 256
        msg += b'10=' + f'{chk:03d}'.encode() + SOH

        return msg

    # ─────────────────────────────────────────────────────────────────────────
    # Envío
    # ─────────────────────────────────────────────────────────────────────────

    def _send(self, msg_type: bytes, fields: list[tuple]):
        """Construye y envía un mensaje FIX. Thread-safe."""
        msg = self._build_msg(msg_type, fields)
        with self._lock:
            if self._sock:
                try:
                    self._sock.sendall(msg)
                except OSError as exc:
                    logger.error(f"FIXSession({self.sender_sub.decode()}): error enviando: {exc}")
                    raise

    def _send_logon(self):
        self._send(MSGTYPE_LOGON, [
            (98,  b'0'),                       # EncryptMethod = None
            (108, str(self.HB_INTERVAL).encode()),  # HeartBtInt
            (141, b'Y'),                       # ResetOnLogon
            (553, self.username),              # Username
            (554, self.password),              # Password
        ])
        logger.info(f"FIXSession({self.sender_sub.decode()}): Logon enviado")

    def _send_heartbeat(self, test_req_id: bytes = b''):
        fields = []
        if test_req_id:
            fields.append((112, test_req_id))  # TestReqID
        try:
            self._send(MSGTYPE_HB, fields)
        except Exception:
            pass

    def _send_logout(self):
        try:
            self._send(MSGTYPE_LOGOUT, [])
        except Exception:
            pass

    # ─────────────────────────────────────────────────────────────────────────
    # Recepción y parsing
    # ─────────────────────────────────────────────────────────────────────────

    def _parse_fields(self, raw: bytes) -> dict:
        """
        Convierte un mensaje FIX crudo en un dict {tag_int: value_bytes}.
        """
        fields = {}
        for part in raw.split(SOH):
            if b'=' in part:
                tag_b, _, val = part.partition(b'=')
                try:
                    fields[int(tag_b)] = val
                except ValueError:
                    pass
        return fields

    def _recv_loop(self):
        """Loop de recepción en hilo de fondo. Lee y despacha mensajes FIX."""
        while self._corriendo and self._sock:
            try:
                data = self._sock.recv(self.RECV_BUFSIZE)
                if not data:
                    logger.warning(f"FIXSession({self.sender_sub.decode()}): conexión cerrada por servidor")
                    break
                self._recv_buf += data
                self._dispatch_messages()
            except ssl.SSLWantReadError:
                time.sleep(0.01)
            except OSError:
                break

        self.activo = False
        logger.info(f"FIXSession({self.sender_sub.decode()}): recv_loop finalizado")

    def _dispatch_messages(self):
        """Extrae y procesa todos los mensajes FIX completos del buffer."""
        while True:
            # Un mensaje FIX termina con "10=xxx\x01"
            idx = self._recv_buf.find(b'10=')
            if idx == -1:
                break
            end = self._recv_buf.find(SOH, idx)
            if end == -1:
                break
            raw_msg = self._recv_buf[:end + 1]
            self._recv_buf = self._recv_buf[end + 1:]
            self._handle_message(raw_msg)

    def _handle_message(self, raw: bytes):
        """Procesa un único mensaje FIX recibido."""
        fields = self._parse_fields(raw)
        msg_type = fields.get(35, b'')

        if msg_type == MSGTYPE_LOGON:
            logger.info(f"✅ FIXSession({self.sender_sub.decode()}): Logon confirmado")
            self.activo = True

        elif msg_type == MSGTYPE_LOGOUT:
            reason = fields.get(58, b'').decode()
            logger.warning(f"FIXSession({self.sender_sub.decode()}): Logout recibido — {reason}")
            self.activo = False

        elif msg_type == MSGTYPE_HB:
            pass  # heartbeat del servidor, nada que hacer

        elif msg_type == MSGTYPE_TEST:
            # TestRequest → responder con Heartbeat
            test_id = fields.get(112, b'')
            self._send_heartbeat(test_id)

        elif msg_type == MSGTYPE_EXEC:
            self._handle_exec_report(fields)

        elif msg_type == MSGTYPE_REJECT:
            text = fields.get(58, b'').decode()
            logger.error(f"❌ FIXSession: mensaje rechazado — {text}")

        # Precio Spot (MarketDataSnapshotFullRefresh = 35=W)
        elif msg_type == b'W':
            if self._on_quote:
                symbol = fields.get(55, b'').decode()
                bid = self._parse_price(fields.get(270, b''))
                ask = self._parse_price(fields.get(270, b''))   # simplificado
                self._on_quote(symbol, bid, ask)

    def _handle_exec_report(self, fields: dict):
        cl_ord_id = fields.get(11, b'').decode()
        ord_status = fields.get(39, b'').decode()
        exec_type  = fields.get(150, b'').decode()
        order_id   = fields.get(37, b'').decode()
        text       = fields.get(58, b'').decode()

        logger.info(
            f"FIXSession ExecutionReport | ClOrdID={cl_ord_id} "
            f"OrdStatus={ord_status} ExecType={exec_type} OrderID={order_id}"
        )

        if self._on_exec_report:
            self._on_exec_report(fields)

        # Resolver petición pendiente
        with self._pending_lock:
            entry = self._pending.get(cl_ord_id)
        if entry:
            entry['result'] = fields
            entry['event'].set()

    @staticmethod
    def _parse_price(val: bytes) -> Optional[float]:
        try:
            return float(val) if val else None
        except ValueError:
            return None

    # ─────────────────────────────────────────────────────────────────────────
    # Gestión de conexión
    # ─────────────────────────────────────────────────────────────────────────

    def conectar(self):
        """Inicia el loop de conexión+reconexión en un hilo de fondo."""
        if self._corriendo:
            return
        self._corriendo = True
        t = threading.Thread(target=self._connection_loop, daemon=True,
                             name=f"fix-{self.sender_sub.decode().lower()}")
        t.start()

    def _connection_loop(self):
        """Loop de reconexión automática."""
        ctx = ssl.create_default_context()
        # En entornos demo el certificado puede no estar en el almacén del sistema
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        while self._corriendo:
            try:
                logger.info(
                    f"FIXSession({self.sender_sub.decode()}): "
                    f"conectando a {self.host}:{self.port}..."
                )
                raw_sock = socket.create_connection((self.host, self.port), timeout=10)
                ssl_sock = ctx.wrap_socket(raw_sock, server_hostname=self.host)
                ssl_sock.settimeout(None)   # modo bloqueante para el recv_loop

                with self._lock:
                    self._sock = ssl_sock
                    self._seq_out = 1
                    self._seq_in  = 1
                    self._recv_buf = b''

                self._parar_hb.clear()

                # Hilo de recepción
                recv_t = threading.Thread(target=self._recv_loop, daemon=True,
                                          name=f"fix-recv-{self.sender_sub.decode().lower()}")
                recv_t.start()

                # Heartbeat
                self._hb_thread = threading.Thread(target=self._hb_loop, daemon=True,
                                                   name=f"fix-hb-{self.sender_sub.decode().lower()}")
                self._hb_thread.start()

                # Logon
                self._send_logon()

                # Esperar a que el hilo de recepción termine (desconexión)
                recv_t.join()

            except (OSError, ssl.SSLError, socket.timeout) as exc:
                logger.error(
                    f"FIXSession({self.sender_sub.decode()}): "
                    f"error de conexión: {exc}"
                )

            if not self._corriendo:
                break

            self.activo = False
            self._parar_hb.set()
            # Resolver todos los pendientes con error
            with self._pending_lock:
                for entry in self._pending.values():
                    entry['result'] = None
                    entry['event'].set()

            logger.info(
                f"FIXSession({self.sender_sub.decode()}): "
                "reconectando en 10s..."
            )
            time.sleep(10)

    def _hb_loop(self):
        """Envía Heartbeat cada HB_INTERVAL segundos."""
        while not self._parar_hb.wait(timeout=self.HB_INTERVAL):
            if self.activo:
                self._send_heartbeat()

    def desconectar(self):
        """Cierra la sesión limpiamente."""
        self._corriendo = False
        self._parar_hb.set()
        if self.activo:
            self._send_logout()
        with self._lock:
            if self._sock:
                try:
                    self._sock.close()
                except Exception:
                    pass
                self._sock = None
        self.activo = False

    # ─────────────────────────────────────────────────────────────────────────
    # Órdenes
    # ─────────────────────────────────────────────────────────────────────────

    def esperar_logon(self, timeout: float = 30.0) -> bool:
        """Bloquea hasta que la sesión esté autenticada o expire el timeout."""
        inicio = time.time()
        while time.time() - inicio < timeout:
            if self.activo:
                return True
            time.sleep(0.2)
        return False

    def nueva_orden_mercado(
        self,
        symbol: str,
        side: bytes,
        qty: float,
        sl: Optional[float] = None,
        tp: Optional[float] = None,
        comment: str = '',
    ) -> Optional[str]:
        """
        Envía NewOrderSingle de tipo Market y espera el ExecutionReport.

        Retorna el OrderID (37) del broker, o None si falló.
        """
        cl_ord_id = str(uuid.uuid4())[:20]
        ev = threading.Event()

        with self._pending_lock:
            self._pending[cl_ord_id] = {'event': ev, 'result': None}

        fields = [
            (11,  cl_ord_id.encode()),         # ClOrdID
            (55,  symbol.encode()),            # Symbol
            (54,  side),                       # Side  1=Buy 2=Sell
            (60,  _fix_utcnow()),              # TransactTime
            (40,  ORD_TYPE_MARKET),            # OrdType = Market
            (38,  f'{qty:.2f}'.encode()),      # OrderQty (lotes)
        ]
        if comment:
            fields.append((58, comment.encode()[:30]))  # Text

        # StopLoss y TakeProfit — tags custom cTrader FIX
        # cTrader FIX usa 1001=TakeProfit y 1002=StopLoss (precio absoluto)
        if tp is not None:
            fields.append((1001, f'{tp:.5f}'.encode()))
        if sl is not None:
            fields.append((1002, f'{sl:.5f}'.encode()))

        try:
            self._send(MSGTYPE_ORDER, fields)
        except Exception as exc:
            logger.error(f"FIXSession: error enviando NewOrderSingle: {exc}")
            with self._pending_lock:
                self._pending.pop(cl_ord_id, None)
            return None

        # Esperar ExecutionReport
        if ev.wait(timeout=self.TIMEOUT_RESP):
            with self._pending_lock:
                result = self._pending.pop(cl_ord_id, {}).get('result')
            if result:
                ord_status = result.get(39, b'').decode()
                if ord_status in ('0', '1', '2'):   # New, PartialFill, Fill
                    return result.get(37, b'').decode()  # OrderID
                text = result.get(58, b'').decode()
                logger.error(f"FIXSession: orden rechazada (OrdStatus={ord_status}) — {text}")
            return None

        logger.warning(f"FIXSession: timeout esperando ExecutionReport (ClOrdID={cl_ord_id})")
        with self._pending_lock:
            self._pending.pop(cl_ord_id, None)
        return None

    def cancelar_orden(self, orig_cl_ord_id: str, order_id: str, symbol: str, side: bytes) -> bool:
        """
        Envía OrderCancelRequest (35=F) para cerrar/cancelar una orden.
        """
        cl_ord_id = str(uuid.uuid4())[:20]
        ev = threading.Event()

        with self._pending_lock:
            self._pending[cl_ord_id] = {'event': ev, 'result': None}

        fields = [
            (41,  orig_cl_ord_id.encode()),    # OrigClOrdID
            (11,  cl_ord_id.encode()),          # ClOrdID
            (37,  order_id.encode()),           # OrderID
            (55,  symbol.encode()),             # Symbol
            (54,  side),                        # Side
            (60,  _fix_utcnow()),               # TransactTime
        ]

        try:
            self._send(MSGTYPE_CANCEL, fields)
        except Exception as exc:
            logger.error(f"FIXSession: error enviando OrderCancelRequest: {exc}")
            with self._pending_lock:
                self._pending.pop(cl_ord_id, None)
            return False

        if ev.wait(timeout=self.TIMEOUT_RESP):
            with self._pending_lock:
                result = self._pending.pop(cl_ord_id, {}).get('result')
            if result:
                exec_type = result.get(150, b'').decode()
                return exec_type == '4'  # Canceled
        logger.warning(f"FIXSession: timeout esperando cancelación de {order_id}")
        with self._pending_lock:
            self._pending.pop(cl_ord_id, None)
        return False


# ─── Adaptador de alto nivel ─────────────────────────────────────────────────

class FIXBroker:
    """
    Adaptador FIX para cTrader / Pepperstone.
    Interfaz compatible con MT5Broker y CTraderBroker.

    Mantiene dos sesiones FIX:
      - trade_session: para enviar órdenes (Trade, puerto 5212)
      - quote_session: para obtener precios (Quote, puerto 5211)  [opcional]
    """

    def __init__(self):
        self.auto_trade = os.getenv('FIX_AUTO_TRADE', 'false').lower() == 'true'
        self.host       = os.getenv('FIX_HOST',        'demo-us-eqx-01.p.c-trader.com')
        self.quote_port = int(os.getenv('FIX_QUOTE_PORT', '5211'))
        self.trade_port = int(os.getenv('FIX_TRADE_PORT', '5212'))
        self.sender_comp = os.getenv('FIX_SENDER_COMP', '')
        self.target_comp = os.getenv('FIX_TARGET_COMP', 'cServer')
        self.username    = os.getenv('FIX_USERNAME',    '')
        self.password    = os.getenv('FIX_PASSWORD',    '')
        self.symbol      = os.getenv('FIX_SYMBOL',      'XAUUSD')
        self.lotes       = float(os.getenv('FIX_LOTES', '0.01'))

        # Último precio bid recibido del Quote session
        self._ultimo_bid: Optional[float] = None

        self._trade_session: Optional[FIXSession] = None
        self._quote_session: Optional[FIXSession] = None

        if not self.auto_trade:
            logger.debug("FIXBroker: auto_trade=false, modo simulación")
            return

        if not self.sender_comp or not self.username or not self.password:
            logger.error(
                "❌ FIXBroker: credenciales incompletas. "
                "Configura FIX_SENDER_COMP, FIX_USERNAME y FIX_PASSWORD"
            )
            self.auto_trade = False
            return

        self._iniciar()

    def _iniciar(self):
        """Crea e inicia las sesiones FIX Trade y Quote."""
        self._trade_session = FIXSession(
            host=self.host,
            port=self.trade_port,
            sender_comp=self.sender_comp,
            target_comp=self.target_comp,
            sender_sub='TRADE',
            username=self.username,
            password=self.password,
            on_exec_report=self._on_exec_report,
        )

        self._quote_session = FIXSession(
            host=self.host,
            port=self.quote_port,
            sender_comp=self.sender_comp,
            target_comp=self.target_comp,
            sender_sub='QUOTE',
            username=self.username,
            password=self.password,
            on_quote=self._on_quote,
        )

        self._trade_session.conectar()
        self._quote_session.conectar()

        logger.info(
            f"✅ FIXBroker iniciando | host={self.host} "
            f"trade={self.trade_port} quote={self.quote_port} "
            f"sender={self.sender_comp}"
        )

    def _on_exec_report(self, fields: dict):
        """Callback genérico para loguear ExecutionReports no solicitados."""
        exec_type  = fields.get(150, b'').decode()
        ord_status = fields.get(39, b'').decode()
        symbol     = fields.get(55, b'').decode()
        order_id   = fields.get(37, b'').decode()
        text       = fields.get(58, b'').decode()
        logger.info(
            f"FIXBroker ExecutionReport | "
            f"ExecType={exec_type} Status={ord_status} "
            f"Symbol={symbol} OrderID={order_id} Text={text}"
        )

    def _on_quote(self, symbol: str, bid: Optional[float], ask: Optional[float]):
        """Callback que actualiza el último precio conocido."""
        if bid and bid > 0:
            self._ultimo_bid = bid

    # ─────────────────────────────────────────────────────────────────────────
    # API pública — compatible con MT5Broker / CTraderBroker
    # ─────────────────────────────────────────────────────────────────────────

    def abrir_operacion(self, senal_data: dict) -> Optional[str]:
        """
        Abre una orden de mercado vía FIX API.

        Campos esperados en senal_data:
            direccion  : 'BUY' | 'SELL'
            entry      : float — precio de entrada estimado
            sl         : float — stop loss (precio absoluto)
            tp1        : float — take profit (precio absoluto)
            timeframe  : str   — ej. '1H', '15M'
            score      : int   — puntuación de la señal

        Retorna el OrderID del broker como string, o None si falló.
        """
        if not self.auto_trade or not self._trade_session:
            return None

        if not self._trade_session.esperar_logon(timeout=30):
            logger.error("FIXBroker: sesión Trade no disponible (sin logon)")
            return None

        direccion = str(senal_data.get('direccion', '')).upper()
        if direccion not in ('BUY', 'SELL'):
            logger.warning(f"FIXBroker: dirección inválida {direccion!r}")
            return None

        side  = SIDE_BUY if direccion == 'BUY' else SIDE_SELL
        entry = float(senal_data.get('entry', 0))
        sl    = float(senal_data.get('sl', 0)) or None
        tp    = float(senal_data.get('tp1', 0)) or None
        tf    = str(senal_data.get('timeframe', ''))
        score = int(senal_data.get('score', 0))

        logger.info(
            f"FIXBroker: abriendo {direccion} {self.lotes} lotes "
            f"@ {entry} | SL {sl} | TP {tp} | TF {tf} | Score {score}"
        )

        order_id = self._trade_session.nueva_orden_mercado(
            symbol=self.symbol,
            side=side,
            qty=self.lotes,
            sl=sl if sl else None,
            tp=tp if tp else None,
            comment=f"BotGold {tf} sc{score}",
        )

        if order_id:
            logger.info(
                f"✅ FIX orden abierta | OrderID={order_id} | "
                f"{direccion} {self.lotes} lotes @ {entry} | SL {sl} | TP {tp}"
            )
        else:
            logger.error("❌ FIXBroker: orden no confirmada")

        return order_id

    def cerrar_operacion(self, order_id: str, direccion: str = 'BUY') -> bool:
        """
        Cierra/cancela una orden por su OrderID.
        El parámetro 'direccion' debe indicar el lado original de la orden.
        """
        if not self.auto_trade or not self._trade_session:
            return False

        if not self._trade_session.esperar_logon(timeout=10):
            logger.error("FIXBroker: sesión Trade no disponible para cierre")
            return False

        side = SIDE_BUY if str(direccion).upper() == 'BUY' else SIDE_SELL
        ok   = self._trade_session.cancelar_orden(
            orig_cl_ord_id=order_id,
            order_id=order_id,
            symbol=self.symbol,
            side=side,
        )

        if ok:
            logger.info(f"✅ FIXBroker: orden {order_id} cerrada")
        else:
            logger.warning(f"⚠️ FIXBroker: cierre de {order_id} no confirmado")

        return ok

    def get_precio_actual(self) -> Optional[float]:
        """Retorna el último precio bid recibido del Quote session."""
        return self._ultimo_bid

    def estado(self) -> dict:
        trade_ok = self._trade_session.activo if self._trade_session else False
        quote_ok = self._quote_session.activo if self._quote_session else False
        return {
            'auto_trade':    self.auto_trade,
            'trade_activo':  trade_ok,
            'quote_activo':  quote_ok,
            'host':          self.host,
            'trade_port':    self.trade_port,
            'quote_port':    self.quote_port,
            'sender_comp':   self.sender_comp,
            'symbol':        self.symbol,
            'lotes':         self.lotes,
            'ultimo_bid':    self._ultimo_bid,
        }

    def desconectar(self):
        """Cierra ambas sesiones FIX limpiamente."""
        if self._trade_session:
            self._trade_session.desconectar()
        if self._quote_session:
            self._quote_session.desconectar()


# ─── Instancia singleton ─────────────────────────────────────────────────────
broker = FIXBroker()
