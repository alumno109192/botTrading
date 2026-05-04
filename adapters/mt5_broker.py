"""
adapters/mt5_broker.py — Ejecución automática en MetaTrader 5 (VT Markets Demo/Live).

Toda la lógica de conexión y envío de órdenes está encapsulada en la clase MT5Broker.

IMPORTANTE: La librería MetaTrader5 solo funciona en Windows con MT5 instalado
localmente. En entornos Linux (Render) la importación falla silenciosamente y
la clase queda desactivada.

Variables de entorno requeridas:
  MT5_AUTO_TRADE   — 'true' para activar (default: false)
  MT5_LOGIN        — Número de cuenta demo/real
  MT5_PASSWORD     — Contraseña de la cuenta
  MT5_SERVER       — Nombre exacto del servidor MT5 (ej. VTMarkets-Demo)
  MT5_SYMBOL       — Símbolo en MT5 (ej. XAUUSD)
  MT5_RISK_PCT     — % del balance a arriesgar por operación (default: 1.0)
  MT5_MAX_LOTES    — Tope máximo de lotes por operación (default: 0.10)
  MT5_MIN_SCORE    — Score mínimo para ejecutar (default: 4)
  MT5_TIMEFRAMES_ACTIVOS — TFs habilitados separados por coma (default: 5m,15m)

Uso (descomentado cuando se quiera activar):
  from adapters.mt5_broker import broker
  broker.abrir_operacion(senal_data)
"""

import os
import math
import logging
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger('bottrading.mt5')


class MT5Broker:
    """
    Encapsula toda la comunicación con MetaTrader 5 para apertura y gestión
    de operaciones en la cuenta demo/real de VT Markets.

    La instancia singleton `broker` al final de este módulo es la que debe
    usarse desde el resto del código.
    """

    # Magic number que identifica todas las órdenes abiertas por este bot
    MAGIC = 20260504

    def __init__(self):
        self._mt5 = None
        self._activo = False

        # ── Leer configuración ──────────────────────────────────────────────
        self.auto_trade    = os.getenv('MT5_AUTO_TRADE', 'false').lower() == 'true'
        self.login         = int(os.getenv('MT5_LOGIN', '0'))
        self.password      = os.getenv('MT5_PASSWORD', '')
        self.server        = os.getenv('MT5_SERVER', 'VTMarkets-Demo')
        self.symbol        = os.getenv('MT5_SYMBOL', 'XAUUSD')
        self.risk_pct      = float(os.getenv('MT5_RISK_PCT', '1.0'))
        self.max_lotes     = float(os.getenv('MT5_MAX_LOTES', '0.10'))
        self.min_score     = int(os.getenv('MT5_MIN_SCORE', '4'))
        self.tfs_activos   = {
            tf.strip()
            for tf in os.getenv('MT5_TIMEFRAMES_ACTIVOS', '5m,15m').split(',')
        }

        # ── Importar MetaTrader5 opcionalmente ─────────────────────────────
        if self.auto_trade:
            try:
                import MetaTrader5 as _mt5_lib
                self._mt5 = _mt5_lib
                logger.info("✅ MT5Broker: librería MetaTrader5 importada")
            except ImportError:
                logger.warning(
                    "⚠️ MT5Broker: MetaTrader5 no disponible en este entorno "
                    "(solo Windows). Ejecución automática desactivada."
                )
                self.auto_trade = False

    # ──────────────────────────────────────────────────────────────────────────
    # Conexión
    # ──────────────────────────────────────────────────────────────────────────

    def conectar(self) -> bool:
        """
        Inicializa MT5 y abre sesión con las credenciales configuradas.
        Llama a esto antes de cualquier operación si no estás seguro de que
        la sesión sigue activa.

        Retorna True si la conexión fue exitosa.
        """
        if not self._mt5:
            return False

        if not self._mt5.initialize():
            logger.error(f"MT5 initialize() falló: {self._mt5.last_error()}")
            self._activo = False
            return False

        ok = self._mt5.login(self.login, password=self.password, server=self.server)
        if not ok:
            logger.error(f"MT5 login falló: {self._mt5.last_error()}")
            self._mt5.shutdown()
            self._activo = False
            return False

        info = self._mt5.account_info()
        logger.info(
            f"✅ MT5 conectado | Cuenta: {info.login} | "
            f"Balance: {info.balance:.2f} {info.currency} | "
            f"Servidor: {self.server}"
        )
        self._activo = True
        return True

    def desconectar(self):
        """Cierra la sesión con MT5."""
        if self._mt5:
            self._mt5.shutdown()
        self._activo = False
        logger.info("MT5Broker: sesión cerrada")

    def _asegurar_conexion(self) -> bool:
        """Verifica que hay sesión activa; reconecta si es necesario."""
        if not self._mt5:
            return False
        info = self._mt5.account_info()
        if info is not None:
            return True
        logger.warning("MT5Broker: sesión caída, intentando reconexión...")
        return self.conectar()

    # ──────────────────────────────────────────────────────────────────────────
    # Sizing
    # ──────────────────────────────────────────────────────────────────────────

    def calcular_lotes(self, entry: float, sl: float) -> float:
        """
        Calcula el tamaño de lote en función del riesgo configurado.

        Fórmula:
            riesgo_usd  = balance × (risk_pct / 100)
            lotes       = riesgo_usd / (distancia_sl_en_puntos × tick_value)

        Para XAU/USD en VT Markets:
            1 lote estándar = 100 oz → tick_value ≈ 10 USD (precio ~2000)

        El resultado se redondea al step mínimo del broker y se limita a
        MT5_MAX_LOTES como medida de seguridad.

        Retorna 0.0 si no hay conexión o el cálculo falla.
        """
        if not self._mt5 or not self._asegurar_conexion():
            return 0.0

        info_cuenta = self._mt5.account_info()
        if not info_cuenta:
            return 0.0

        distancia_sl = abs(entry - sl)
        if distancia_sl == 0:
            logger.warning("calcular_lotes: distancia SL = 0, abortando")
            return 0.0

        sym_info = self._mt5.symbol_info(self.symbol)
        if not sym_info:
            logger.error(f"MT5Broker: símbolo {self.symbol!r} no encontrado en Market Watch")
            return 0.0

        balance    = info_cuenta.balance
        riesgo_usd = balance * (self.risk_pct / 100.0)
        tick_value = sym_info.trade_tick_value
        tick_size  = sym_info.trade_tick_size
        puntos_sl  = distancia_sl / tick_size

        lotes_raw = riesgo_usd / (puntos_sl * tick_value)

        # Redondear al step mínimo del broker (hacia abajo por seguridad)
        step  = sym_info.volume_step
        lotes = math.floor(lotes_raw / step) * step
        lotes = round(min(lotes, self.max_lotes), 2)

        logger.info(
            f"Sizing | Balance {balance:.0f} | Riesgo {riesgo_usd:.2f} USD | "
            f"SL dist {distancia_sl:.2f} | Lotes calculados: {lotes}"
        )
        return lotes

    # ──────────────────────────────────────────────────────────────────────────
    # Apertura de operación
    # ──────────────────────────────────────────────────────────────────────────

    def abrir_operacion(self, senal_data: dict) -> int | None:
        """
        Abre una operación en MT5 a partir de los datos de señal del bot.

        Campos esperados en senal_data:
            direccion  : 'BUY' | 'SELL'
            entry      : float — precio de entrada
            sl         : float — stop loss
            tp1        : float — primer objetivo (TP principal en MT5)
            score      : int   — puntuación de la señal
            timeframe  : str   — ej. '5m', '15m'
            simbolo    : str   — ej. 'GC=F' (se traduce a self.symbol)

        Filtros aplicados antes de operar:
            - MT5_AUTO_TRADE debe ser true
            - timeframe debe estar en MT5_TIMEFRAMES_ACTIVOS
            - score debe ser >= MT5_MIN_SCORE

        Retorna el ticket de la orden abierta, o None si no se ejecutó.
        """
        if not self.auto_trade or not self._mt5:
            return None

        # ── Filtros de seguridad ───────────────────────────────────────────
        tf        = senal_data.get('timeframe', '')
        score     = int(senal_data.get('score', 0) or 0)
        direccion = str(senal_data.get('direccion', '')).upper()

        if tf not in self.tfs_activos:
            logger.debug(f"MT5Broker: TF {tf!r} no está en activos → omitido")
            return None

        if score < self.min_score:
            logger.info(f"MT5Broker: score {score} < {self.min_score} → no ejecutado")
            return None

        if direccion not in ('BUY', 'SELL'):
            logger.warning(f"MT5Broker: dirección inválida {direccion!r}")
            return None

        if not self._asegurar_conexion():
            logger.error("MT5Broker: no hay conexión, operación cancelada")
            return None

        # ── Drawdown diario — parada automática ───────────────────────────
        info_cuenta = self._mt5.account_info()
        if info_cuenta and info_cuenta.equity < info_cuenta.balance * 0.97:
            logger.warning(
                f"MT5Broker: drawdown diario > 3% "
                f"(equity {info_cuenta.equity:.2f} / balance {info_cuenta.balance:.2f}) "
                f"→ operaciones pausadas hasta mañana"
            )
            return None

        # ── Construcción de la orden ───────────────────────────────────────
        entry = float(senal_data['entry'])
        sl    = float(senal_data['sl'])
        tp1   = float(senal_data['tp1'])
        lotes = self.calcular_lotes(entry, sl)

        if lotes <= 0:
            logger.error("MT5Broker: lotes = 0, operación cancelada")
            return None

        order_type = (
            self._mt5.ORDER_TYPE_BUY
            if direccion == 'BUY'
            else self._mt5.ORDER_TYPE_SELL
        )

        request = {
            "action":       self._mt5.TRADE_ACTION_DEAL,
            "symbol":       self.symbol,
            "volume":       lotes,
            "type":         order_type,
            "price":        entry,
            "sl":           sl,
            "tp":           tp1,
            "deviation":    20,
            "magic":        self.MAGIC,
            "comment":      f"BotGold {tf} sc{score}",
            "type_time":    self._mt5.ORDER_TIME_GTC,
            "type_filling": self._mt5.ORDER_FILLING_IOC,
        }

        resultado = self._mt5.order_send(request)

        if resultado is None or resultado.retcode != self._mt5.TRADE_RETCODE_DONE:
            codigo = resultado.retcode if resultado else "None"
            logger.error(
                f"❌ MT5Broker order_send falló | retcode={codigo} | {resultado}"
            )
            return None

        ticket = resultado.order
        logger.info(
            f"✅ Orden abierta | Ticket: {ticket} | {direccion} {lotes} lotes "
            f"@ {entry} | SL {sl} | TP1 {tp1} | TF {tf} | Score {score}"
        )
        return ticket

    # ──────────────────────────────────────────────────────────────────────────
    # Cierre de operación
    # ──────────────────────────────────────────────────────────────────────────

    def cerrar_operacion(self, ticket: int) -> bool:
        """
        Cierra una posición abierta identificada por su ticket.

        Retorna True si el cierre fue exitoso.
        """
        if not self._mt5 or not self._asegurar_conexion():
            return False

        posiciones = self._mt5.positions_get(ticket=ticket)
        if not posiciones:
            logger.warning(f"MT5Broker: posición {ticket} no encontrada")
            return False

        pos = posiciones[0]
        tipo_cierre = (
            self._mt5.ORDER_TYPE_SELL if pos.type == 0
            else self._mt5.ORDER_TYPE_BUY
        )
        tick = self._mt5.symbol_info_tick(self.symbol)
        precio = tick.bid if pos.type == 0 else tick.ask

        request = {
            "action":       self._mt5.TRADE_ACTION_DEAL,
            "symbol":       self.symbol,
            "volume":       pos.volume,
            "type":         tipo_cierre,
            "position":     ticket,
            "price":        precio,
            "deviation":    20,
            "magic":        self.MAGIC,
            "comment":      "BotGold cierre",
            "type_time":    self._mt5.ORDER_TIME_GTC,
            "type_filling": self._mt5.ORDER_FILLING_IOC,
        }

        resultado = self._mt5.order_send(request)
        ok = resultado and resultado.retcode == self._mt5.TRADE_RETCODE_DONE

        if ok:
            logger.info(f"✅ MT5Broker: posición {ticket} cerrada")
        else:
            logger.error(f"❌ MT5Broker: no se pudo cerrar {ticket} | {resultado}")
        return ok

    # ──────────────────────────────────────────────────────────────────────────
    # Estado de la cuenta
    # ──────────────────────────────────────────────────────────────────────────

    def estado_cuenta(self) -> dict:
        """
        Retorna un resumen del estado actual de la cuenta: balance, equity,
        profit flotante y posiciones abiertas por este bot (magic=MAGIC).

        Retorna dict vacío si no hay conexión.
        """
        if not self._mt5 or not self._asegurar_conexion():
            return {}

        info       = self._mt5.account_info()
        posiciones = self._mt5.positions_get(magic=self.MAGIC) or []

        return {
            "balance":    info.balance,
            "equity":     info.equity,
            "profit":     info.profit,
            "currency":   info.currency,
            "posiciones": len(posiciones),
            "tickets":    [p.ticket for p in posiciones],
        }


# ── Singleton ──────────────────────────────────────────────────────────────────
# Instancia única que se importa desde el resto del código.
# La clase se instancia siempre; el flag MT5_AUTO_TRADE controla si opera.
broker = MT5Broker()
