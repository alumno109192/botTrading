"""
bridge/sse_broker.py — Broker de Server-Sent Events (SSE)

Permite que el backend publique eventos (señales, precios) y que el frontend
los reciba en tiempo real a través del endpoint /api/v1/events.

Uso:
    from bridge.sse_broker import broker

    # Publicar evento de señal
    broker.publicar_senal(tipo='nueva', simbolo='XAUUSD', timeframe='1H',
                          direccion='COMPRA', precio_entrada=2345.5,
                          senal_id=42)

    # Publicar actualización de precio
    broker.publicar_precio(symbol='XAUUSD', precio=2345.5)
"""

import json
import queue
import threading
import logging

logger = logging.getLogger('bottrading')

# Tamaño máximo de la cola por cliente; si el cliente va lento se descarta
_MAX_QUEUE = 50


class _SSEBroker:
    """Gestor de suscriptores SSE (thread-safe)."""

    def __init__(self):
        self._lock = threading.Lock()
        self._clientes: list[queue.Queue] = []

    # ── Gestión de clientes ────────────────────────────────────────────────

    def suscribir(self) -> queue.Queue:
        """Registra un nuevo cliente y devuelve su cola de eventos."""
        q: queue.Queue = queue.Queue(maxsize=_MAX_QUEUE)
        with self._lock:
            self._clientes.append(q)
        logger.debug(f"[SSE] Cliente suscrito — total: {len(self._clientes)}")
        return q

    def desuscribir(self, q: queue.Queue) -> None:
        """Elimina un cliente (llamar cuando la conexión se cierra)."""
        with self._lock:
            try:
                self._clientes.remove(q)
            except ValueError:
                pass
        logger.debug(f"[SSE] Cliente desuscrito — total: {len(self._clientes)}")

    # ── Publicación ────────────────────────────────────────────────────────

    def _publicar(self, event_type: str, data: dict) -> None:
        """Envía un evento a todos los clientes conectados."""
        payload = json.dumps(data, ensure_ascii=False)
        # Formato SSE: "event: <tipo>\ndata: <json>\n\n"
        msg = f"event: {event_type}\ndata: {payload}\n\n"
        with self._lock:
            clientes = list(self._clientes)
        enviados = 0
        for q in clientes:
            try:
                q.put_nowait(msg)
                enviados += 1
            except queue.Full:
                pass  # cliente lento — se pierde el mensaje, no bloquear
        if clientes:
            logger.debug(f"[SSE] evento '{event_type}' enviado a {enviados}/{len(clientes)} clientes")

    def publicar_senal(self, tipo: str, simbolo: str, timeframe: str,
                       direccion: str, precio_entrada: float,
                       senal_id: int = None, **kwargs) -> None:
        """Publica un evento de señal de trading.

        Args:
            tipo: 'nueva' | 'tp1' | 'tp2' | 'tp3' | 'sl' | 'caducada' | etc.
            simbolo: p.ej. 'XAUUSD'
            timeframe: p.ej. '1H'
            direccion: 'COMPRA' | 'VENTA'
            precio_entrada: precio de entrada de la señal
            senal_id: ID de la señal en BD (opcional)
        """
        data = {
            'tipo': tipo,
            'simbolo': simbolo,
            'timeframe': timeframe,
            'direccion': direccion,
            'precio_entrada': precio_entrada,
            'senal_id': senal_id,
            **kwargs,
        }
        self._publicar('senal', data)

    def publicar_precio(self, symbol: str, precio: float) -> None:
        """Publica una actualización de precio en tiempo real.

        Args:
            symbol: ticker, p.ej. 'XAUUSD' o 'GC=F'
            precio: precio actual (último close)
        """
        self._publicar('precio', {'symbol': symbol, 'precio': precio})

    @property
    def num_clientes(self) -> int:
        with self._lock:
            return len(self._clientes)


# Instancia global (singleton)
broker = _SSEBroker()
