"""
core/base_detector.py — Clase base abstracta para detectores de trading.

Elimina ~70% de código duplicado entre detector_gold_1d, 4h, 1h, 15m, 5m.
Cada detector concreto solo necesita implementar analizar() con su lógica
de score específica de TF.

Funciones centralizadas:
  - calcular_zonas_sr        — S/R mediante swing highs/lows locales
  - calcular_indicadores     — RSI, EMA, ATR, Bollinger, MACD, OBV, ADX + columnas de vela
  - calcular_niveles         — SL y TP basados en ATR
  - calcular_rr              — Risk:Reward ratio
  - ya_enviada / marcar_enviada / limpiar_alertas_viejas  — Anti-spam
  - esta_duplicado / registrar_analisis                   — Guard de análisis duplicado
  - exclusion_mutua          — Una sola dirección por vela
  - determinar_sesgo         — BULLISH / BEARISH / NEUTRAL
  - enviar                   — Wrapper de enviar_telegram con aviso macro
"""

import os
import time
import logging
import pandas as pd
import numpy as np
from abc import ABC, abstractmethod
from datetime import datetime, timezone, timedelta

_logger = logging.getLogger('bottrading')

import adapters.telegram as _telegram_mod
# ── MT5 / VT Markets ─────────────────────────────────────────────────────────
# Requiere MT5_AUTO_TRADE=true en .env y MetaTrader5 instalado (solo Windows).
# En entornos Linux (Render) la importación falla silenciosamente.
from adapters.mt5_broker import broker as _mt5_broker
from core.indicators import (
    calcular_rsi, calcular_ema, calcular_atr,
    calcular_bollinger_bands, calcular_macd, calcular_obv, calcular_adx,
)

# Vigencia máxima por timeframe — debe mantenerse sincronizado con signal_monitor.py
_VIGENCIA_HORAS: dict = {
    '1M':  2,
    '5M':  4,
    '15M': 8,
    '1H':  16,
    '4H':  72,
    '1D':  120,
}

_NOMBRES_DISPLAY: dict = {
    'XAUUSD': 'ORO (XAUUSD)',
    'EURUSD': 'EUR/USD',
    'GBPUSD': 'GBP/USD',
    'USDJPY': 'USD/JPY',
    'US30':   'DOW JONES (US30)',
    'NAS100': 'NASDAQ (NAS100)',
    'BTCUSD': 'BTC/USD',
}


def simbolo_a_nombre(simbolo: str) -> str:
    """Devuelve el nombre legible de un símbolo para mensajes Telegram.

    Acepta tanto 'XAUUSD' como 'XAUUSD_1H' (extrae el símbolo base).
    Fallback: devuelve el símbolo tal cual.
    """
    base = simbolo.split('_')[0]
    return _NOMBRES_DISPLAY.get(base, base)


class BaseDetector(ABC):
    """
    Clase base abstracta para detectores de señales de trading.

    Contiene toda la lógica compartida entre los detectores de Gold
    (y potencialmente otros instrumentos):
      - Inicialización de BD y Telegram
      - Cálculo de zonas S/R
      - Bloque de indicadores técnicos
      - Cálculo de SL/TP y R:R
      - Anti-spam (ya_enviada/marcar_enviada/limpiar)
      - Guard de análisis duplicado
      - Exclusión mutua SELL/BUY
      - Determinación de sesgo
      - Wrapper enviar con aviso macro
    """

    _TTL_ALERTA = 172_800   # 48 horas en segundos — TTL para el anti-spam
    _SR_WING = 3            # velas a cada lado para confirmar un swing high/low
    _SR_MIN_DIST_ATR = 0.3  # distancia mínima (en ATR) entre precio actual y un pivote válido

    # Mapeo símbolo → nombre legible para mensajes Telegram
    _NOMBRES_DISPLAY: dict = {
        'XAUUSD': 'ORO (XAUUSD)',
        'EURUSD': 'EUR/USD',
        'GBPUSD': 'GBP/USD',
        'USDJPY': 'USD/JPY',
        'US30':   'DOW JONES (US30)',
        'NAS100': 'NASDAQ (NAS100)',
        'BTCUSD': 'BTC/USD',
    }

    def __init__(self, simbolo: str, tf_label: str, params: dict,
                 telegram_thread_id):
        self.simbolo = simbolo
        self.tf_label = tf_label
        self.params = params
        self.telegram_thread_id = telegram_thread_id

        # Estado interno
        self.alertas_enviadas: dict = {}
        self.ultimo_analisis: dict = {}
        self.aviso_macro: str = ""
        self.contexto_pullback: str = ""  # contexto pullback multi-TF; se appendea en cada mensaje
        self._last_senal_id: int | None = None  # ID de la última señal guardada en BD
        self._last_senal_esperando: bool = False  # True si la última señal quedó en estado ESPERANDO
        self._current_candle_ts = None             # Timestamp de la vela analizada (lo asigna cada detector)

        # Inicializar BD si las variables están configuradas
        try:
            from adapters.database import get_db
            self.db = get_db()
        except Exception as _e:
            _logger.warning(f"⚠️ BaseDetector: no se pudo inicializar BD — {_e}")
            self.db = None

        # Restaurar estado anti-spam desde BD al arrancar
        if self.db:
            try:
                for clave, ts in self.db.get_antispam_activos(self._TTL_ALERTA):
                    self.alertas_enviadas[clave] = ts
            except Exception:
                pass  # Fallo no crítico; se parte de estado vacío

    # ─────────────────────────────────────────────────────────────────────────
    # Nombre display — símbolo legible para mensajes Telegram
    # ─────────────────────────────────────────────────────────────────────────

    @property
    def nombre_display(self) -> str:
        """Devuelve el nombre legible del símbolo para mensajes Telegram."""
        return simbolo_a_nombre(self.simbolo)

    # ─────────────────────────────────────────────────────────────────────────
    # Helper BD — inyecta telegram_thread_id automáticamente
    # ─────────────────────────────────────────────────────────────────────────

    # Umbrales de score por TF para derivar nivel cuando el detector no lo
    # especifica explícitamente. Valores conservadores basados en los umbrales
    # estáticos de cada detector.
    _NIVEL_THRESHOLDS: dict = {
        '1D':  {'MAXIMA': 10, 'FUERTE': 8,  'MEDIA': 6,  'ALERTA': 4},
        '4H':  {'MAXIMA': 14, 'FUERTE': 12, 'MEDIA': 9,  'ALERTA': 5},
        '1H':  {'MAXIMA': 14, 'FUERTE': 12, 'MEDIA': 9,  'ALERTA': 5},
        '15M': {'MAXIMA': 12, 'FUERTE': 10, 'MEDIA': 7,  'ALERTA': 4},
        '5M':  {'MAXIMA': 10, 'FUERTE': 8,  'MEDIA': 6,  'ALERTA': 3},
    }

    @classmethod
    def _derivar_nivel(cls, score: float, tf_label: str) -> str:
        """Clasifica un score numérico en ALERTA/MEDIA/FUERTE/MAXIMA según el TF."""
        tf = tf_label.upper()
        thresholds = cls._NIVEL_THRESHOLDS.get(tf, cls._NIVEL_THRESHOLDS['1H'])
        if score >= thresholds['MAXIMA']:
            return 'MAXIMA'
        if score >= thresholds['FUERTE']:
            return 'FUERTE'
        if score >= thresholds['MEDIA']:
            return 'MEDIA'
        return 'ALERTA'

    def _guardar_senal(self, senal_data: dict) -> int:
        """Proxy sobre db.guardar_senal que inyecta telegram_thread_id, timestamp_entry y nivel."""
        senal_data.setdefault('telegram_thread_id', self.telegram_thread_id)
        # Inyectar timestamp de la vela si no viene explícito
        if 'timestamp_entry' not in senal_data and self._current_candle_ts is not None:
            senal_data['timestamp_entry'] = self._current_candle_ts.isoformat()
        # Derivar nivel automáticamente si el detector no lo provee explícitamente
        if 'nivel' not in senal_data:
            score = senal_data.get('score', 0) or 0
            senal_data['nivel'] = self._derivar_nivel(score, self.tf_label)
        # Por defecto, las señales se guardan como ESPERANDO hasta que el precio alcance la entrada
        senal_data.setdefault('estado', 'ESPERANDO')
        senal_id = self.db.guardar_senal(senal_data)
        self._last_senal_id = senal_id  # lo consumirá el próximo enviar()
        self._last_senal_esperando = senal_data.get('estado') == 'ESPERANDO'
        return senal_id

    # ─────────────────────────────────────────────────────────────────────────
    # Método abstracto — debe implementarse en cada subclase
    # ─────────────────────────────────────────────────────────────────────────

    @abstractmethod
    def analizar(self, *args, **kwargs):
        """Implementar en subclase con la lógica de score específica del TF."""

    # ─────────────────────────────────────────────────────────────────────────
    # S/R — Zonas de Soporte y Resistencia
    # ─────────────────────────────────────────────────────────────────────────

    def calcular_zonas_sr(self, df: pd.DataFrame, atr: float,
                          lookback: int, zone_mult: float) -> tuple:
        """
        Detecta zonas S/R usando swing highs/lows locales.

        Principio: soporte roto = nueva resistencia; resistencia rota = nuevo soporte.
        Selecciona el pivote más cercano al precio actual en cada dirección.

        Returns: (zrl, zrh, zsl, zsh)
            zrl/zrh = límites bajo/alto de la zona de resistencia
            zsl/zsh = límites bajo/alto de la zona de soporte
        """
        close = float(df['Close'].iloc[-1])
        zone_width = atr * zone_mult
        wing = self._SR_WING

        highs = df['High'].iloc[-lookback - 1:-1]
        lows = df['Low'].iloc[-lookback - 1:-1]

        swing_highs = []
        for i in range(wing, len(highs) - wing):
            val = float(highs.iloc[i])
            if (all(val >= float(highs.iloc[i - j]) for j in range(1, wing + 1)) and
                    all(val >= float(highs.iloc[i + j]) for j in range(1, wing + 1))):
                swing_highs.append(val)

        swing_lows = []
        for i in range(wing, len(lows) - wing):
            val = float(lows.iloc[i])
            if (all(val <= float(lows.iloc[i - j]) for j in range(1, wing + 1)) and
                    all(val <= float(lows.iloc[i + j]) for j in range(1, wing + 1))):
                swing_lows.append(val)

        if not swing_highs:
            swing_highs = [float(highs.max())]
        if not swing_lows:
            swing_lows = [float(lows.min())]

        min_dist = atr * self._SR_MIN_DIST_ATR
        # Resistencia: cualquier pivote encima (incluye soportes rotos)
        candidatos_resist = [v for v in set(swing_highs + swing_lows)
                             if v > close + min_dist]
        # Soporte: cualquier pivote debajo (incluye resistencias rotas)
        candidatos_sop = [v for v in set(swing_lows + swing_highs)
                         if v < close - min_dist]

        resist_pivot = min(candidatos_resist) if candidatos_resist else float(highs.max())
        support_pivot = max(candidatos_sop) if candidatos_sop else float(lows.min())

        # Precisión dinámica según precio: Gold (~3000) → 2 dec, Forex (~1.17) → 5 dec
        _decimales = 2 if close > 100 else 5

        # Resistencia: zona centrada en el swing high (más banda bajo el pivot, donde venden)
        zrh = round(resist_pivot + zone_width * 0.25, _decimales)
        zrl = round(resist_pivot - zone_width * 0.75, _decimales)
        # Soporte: zona centrada en el swing low (más banda sobre el pivot, donde compran)
        zsh = round(support_pivot + zone_width * 0.75, _decimales)
        zsl = round(support_pivot - zone_width * 0.25, _decimales)

        return zrl, zrh, zsl, zsh

    # ─────────────────────────────────────────────────────────────────────────
    # Indicadores técnicos
    # ─────────────────────────────────────────────────────────────────────────

    def calcular_indicadores(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Calcula y agrega todos los indicadores técnicos estándar al DataFrame.

        Usa los params del constructor para los periodos:
          rsi_length, ema_fast_len, ema_slow_len, ema_trend_len, atr_length.
        Los params opcionales bb_length, macd_fast/slow/signal, adx_length
        permiten ajustar los indicadores secundarios por TF si se desea.

        Returns: copia del DataFrame con columnas de indicadores añadidas.
        """
        p = self.params
        df = df.copy()

        # ── Indicadores base ──────────────────────────────────────────────────
        df['rsi'] = calcular_rsi(df['Close'], p['rsi_length'])
        df['ema_fast'] = calcular_ema(df['Close'], p['ema_fast_len'])
        df['ema_slow'] = calcular_ema(df['Close'], p['ema_slow_len'])
        df['ema_trend'] = calcular_ema(df['Close'], p['ema_trend_len'])
        df['atr'] = calcular_atr(df, p['atr_length'])
        df['vol_avg'] = df['Volume'].rolling(20).mean()

        # ── Bollinger Bands ───────────────────────────────────────────────────
        bb_len = p.get('bb_length', 20)
        df['bb_upper'], df['bb_mid'], df['bb_lower'], df['bb_width'] = \
            calcular_bollinger_bands(df['Close'], length=bb_len)

        # ── MACD ─────────────────────────────────────────────────────────────
        macd_fast = p.get('macd_fast', 12)
        macd_slow = p.get('macd_slow', 26)
        macd_sig = p.get('macd_signal_len', 9)
        df['macd'], df['macd_signal'], df['macd_hist'] = \
            calcular_macd(df['Close'], fast=macd_fast, slow=macd_slow, signal=macd_sig)

        # ── OBV ──────────────────────────────────────────────────────────────
        df['obv'] = calcular_obv(df)
        df['obv_ema'] = calcular_ema(df['obv'], 20)

        # ── ADX ──────────────────────────────────────────────────────────────
        adx_len = p.get('adx_length', p['atr_length'])
        df['adx'], df['di_plus'], df['di_minus'] = calcular_adx(df, length=adx_len)

        # ── Columnas de vela ─────────────────────────────────────────────────
        df['body'] = (df['Close'] - df['Open']).abs()
        df['upper_wick'] = df['High'] - df[['Close', 'Open']].max(axis=1)
        df['lower_wick'] = df[['Close', 'Open']].min(axis=1) - df['Low']
        df['total_range'] = df['High'] - df['Low']
        df['is_bearish'] = df['Close'] < df['Open']
        df['is_bullish'] = df['Close'] > df['Open']

        return df

    # ─────────────────────────────────────────────────────────────────────────
    # SL / TP / R:R
    # ─────────────────────────────────────────────────────────────────────────

    def calcular_niveles(self, sell_limit: float, buy_limit: float,
                         atr: float) -> tuple:
        """
        Calcula Stop Loss y Take Profits para ambas direcciones usando los
        multiplicadores de ATR definidos en params.

        Returns: (sl_venta, sl_compra, tp1_v, tp2_v, tp3_v, tp1_c, tp2_c, tp3_c)
        """
        p = self.params
        asm = p['atr_sl_mult']

        sl_venta = round(sell_limit + atr * asm, 2)
        sl_compra = round(buy_limit - atr * asm, 2)

        tp1_v = round(sell_limit - atr * p['atr_tp1_mult'], 2)
        tp2_v = round(sell_limit - atr * p['atr_tp2_mult'], 2)
        tp3_v = round(sell_limit - atr * p['atr_tp3_mult'], 2)

        tp1_c = round(buy_limit + atr * p['atr_tp1_mult'], 2)
        tp2_c = round(buy_limit + atr * p['atr_tp2_mult'], 2)
        tp3_c = round(buy_limit + atr * p['atr_tp3_mult'], 2)

        return sl_venta, sl_compra, tp1_v, tp2_v, tp3_v, tp1_c, tp2_c, tp3_c

    def calcular_rr(self, limit: float, sl: float, tp: float) -> float:
        """
        Risk:Reward ratio redondeado a 1 decimal.
        Retorna 0 si el denominador es 0 (evita división por cero).
        """
        denom = abs(sl - limit)
        if denom == 0:
            return 0.0
        return round(abs(tp - limit) / denom, 1)

    # ─────────────────────────────────────────────────────────────────────────
    # Anti-spam
    # ─────────────────────────────────────────────────────────────────────────

    def ya_enviada(self, clave: str) -> bool:
        """Retorna True si la alerta fue enviada en las últimas 48h."""
        ts = self.alertas_enviadas.get(clave, 0)
        return ts > time.time() - self._TTL_ALERTA

    def marcar_enviada(self, clave: str) -> None:
        """Registra el timestamp de envío de la alerta en memoria y en BD."""
        self.alertas_enviadas[clave] = time.time()
        if self.db:
            self.db.set_antispam(clave, self.alertas_enviadas[clave])

    def limpiar_alertas_viejas(self) -> None:
        """Elimina entradas de alertas_enviadas más antiguas que el TTL."""
        corte = time.time() - self._TTL_ALERTA
        claves_viejas = [k for k, v in self.alertas_enviadas.items() if v < corte]
        for k in claves_viejas:
            del self.alertas_enviadas[k]
        if self.db:
            self.db.limpiar_antispam_viejos(self._TTL_ALERTA)

    # ─────────────────────────────────────────────────────────────────────────
    # Guard de análisis duplicado
    # ─────────────────────────────────────────────────────────────────────────

    def esta_duplicado(self, simbolo: str, fecha: str,
                       score_sell: int, score_buy: int,
                       delta: int = 1) -> bool:
        """
        Retorna True si ya se analizó esta vela (misma fecha) con scores similares
        (diferencia <= delta en ambas dimensiones).
        """
        prev = self.ultimo_analisis.get(simbolo)
        if prev is None:
            return False
        if prev['fecha'] != fecha:
            return False
        return (abs(prev['score_sell'] - score_sell) <= delta and
                abs(prev['score_buy'] - score_buy) <= delta)

    def registrar_analisis(self, simbolo: str, fecha: str,
                           score_sell: int, score_buy: int) -> None:
        """Guarda el resultado del análisis para el control de duplicados."""
        self.ultimo_analisis[simbolo] = {
            'fecha': fecha,
            'score_sell': score_sell,
            'score_buy': score_buy,
        }

    # ─────────────────────────────────────────────────────────────────────────
    # Exclusión mutua
    # ─────────────────────────────────────────────────────────────────────────

    def exclusion_mutua(self, score_sell: int, score_buy: int,
                        senal_sell: bool, senal_buy: bool) -> tuple:
        """
        Si ambas señales están activas, mantiene solo la de mayor score.
        En empate, SELL tiene precedencia (conservadurismo).

        Returns: (senal_sell, senal_buy) como booleans corregidos.
        """
        if senal_sell and senal_buy:
            if score_sell >= score_buy:
                senal_buy = False
            else:
                senal_sell = False
        return bool(senal_sell), bool(senal_buy)

    # ─────────────────────────────────────────────────────────────────────────
    # Sesgo
    # ─────────────────────────────────────────────────────────────────────────

    def determinar_sesgo(self, score_sell: int, score_buy: int) -> str:
        """
        Retorna 'BEARISH' si sell > buy, 'BULLISH' si buy > sell,
        'NEUTRAL' en empate (incluido 0:0).
        """
        if score_sell > score_buy:
            return 'BEARISH'
        if score_buy > score_sell:
            return 'BULLISH'
        return 'NEUTRAL'

    # ─────────────────────────────────────────────────────────────────────────
    # Filtro de supresión por evento macro
    # ─────────────────────────────────────────────────────────────────────────

    def _debe_suprimir_por_evento(self, nivel_senal: str) -> bool:
        """
        Suprime señales de baja prioridad (ALERTA y MEDIA) cuando hay evento macro activo.
        Solo FUERTE y MAXIMA pasan durante eventos.

        Args:
            nivel_senal: 'ALERTA', 'MEDIA', 'FUERTE' o 'MAXIMA'

        Returns:
            True si la señal debe suprimirse.
        """
        if self.aviso_macro and nivel_senal in ('ALERTA', 'MEDIA'):
            import logging as _log
            _log.getLogger('bottrading').warning(
                f"  🔕 Señal {nivel_senal} suprimida — evento macro activo: {self.aviso_macro}"
            )
            return True
        return False

    # ─────────────────────────────────────────────────────────────────────────
    # Telegram wrapper
    # ─────────────────────────────────────────────────────────────────────────

    def enviar(self, mensaje: str) -> bool:
        """
        Envía un mensaje a Telegram añadiendo el sufijo de aviso macro si existe.
        Si justo antes se guardó una señal en BD, añade su #ID al mensaje
        y guarda el message_id de Telegram en la BD para reply posterior.
        Delega en adapters.telegram.enviar_telegram para facilitar el mocking.

        Para señales ESPERANDO (orden LIMIT): se envía la notificación BUY/SELL LIMIT
        a Telegram de inmediato para que el trader coloque la orden, pero la señal
        no aparecerá en el dashboard web hasta que el precio llegue a la entrada
        y el signal_monitor la transite a ACTIVA.
        """
        senal_id = self._last_senal_id
        self._last_senal_id = None          # consumir siempre
        self._last_senal_esperando = False  # consumir siempre

        if senal_id:
            # Calcular hora estimada de caducidad según timeframe del símbolo
            _sufijo_tf = self.simbolo.split('_')[-1].upper() if '_' in self.simbolo else ''
            _max_horas = _VIGENCIA_HORAS.get(_sufijo_tf, 24)
            _caduca_utc = datetime.now(timezone.utc) + timedelta(hours=_max_horas)
            _caduca_local = _caduca_utc.strftime('%d/%m %H:%M') + ' UTC'
            _caduca_info = f"⏰ Caduca: <b>{_caduca_local}</b> (en {_max_horas}h)"
            mensaje = (mensaje.rstrip()
                       + f"\n━━━━━━━━━━━━━━━━━━━━"
                       + f"\n🔖 <b>#{senal_id}</b>  |  {_caduca_info}")
        sufijo = (f"\n⚠️ <b>Evento macro próximo:</b> {self.aviso_macro}"
                  if self.aviso_macro else "")
        sufijo_pb = (f"\n{self.contexto_pullback}"
                     if self.contexto_pullback else "")
        message_id = _telegram_mod.enviar_telegram(mensaje + sufijo + sufijo_pb,
                                                   self.telegram_thread_id)

        # ── Hook MT5 — ejecución automática en VT Markets ─────────────────────
        # Activo cuando MT5_AUTO_TRADE=true en .env y MetaTrader5 instalado.
        # senal_data se construye con los datos mínimos que necesita MT5Broker.
        # La clase filtra por timeframe, score y MT5_MIN_SCORE antes de operar.
        if senal_id and self.db and _mt5_broker.auto_trade:
            try:
                _result = self.db.ejecutar_query(
                    "SELECT direccion, precio_entrada AS entry, sl, tp1, "
                    "score, timeframe, simbolo FROM senales WHERE id = ?",
                    (senal_id,)
                )
                if _result and _result.rows:
                    _row = dict(_result.rows[0])
                    # MT5Broker espera BUY/SELL; la BD almacena COMPRA/VENTA
                    _dir_map = {'COMPRA': 'BUY', 'VENTA': 'SELL'}
                    _row['direccion'] = _dir_map.get(
                        str(_row.get('direccion', '')).upper(),
                        _row.get('direccion', '')
                    )
                    # MT5Broker compara TF en minúsculas; la BD los almacena en
                    # mayúsculas (ej. '5M', '15M') → normalizar
                    if _row.get('timeframe'):
                        _row['timeframe'] = str(_row['timeframe']).lower()
                    _mt5_broker.abrir_operacion(_row)
            except Exception as _e:
                _logger.warning(
                    f"MT5 hook: error al intentar abrir operación — {_e}"
                )
        # ── Fin hook MT5 ───────────────────────────────────────────────────────

        # Guardar message_id en BD para poder hacer reply en alertas TP/SL
        if message_id and senal_id and self.db:
            try:
                self.db.ejecutar_query(
                    "UPDATE senales SET telegram_message_id = ? WHERE id = ?",
                    (message_id, senal_id)
                )
            except Exception:
                pass
        return bool(message_id)

    # ─────────────────────────────────────────────────────────────────────────
    # Filtro de volumen
    # ─────────────────────────────────────────────────────────────────────────

    def ajustar_scores_por_volumen(self, score_sell: int, score_buy: int,
                                   vol: float, vol_avg: float,
                                   vol_mult: float = 1.2) -> tuple:
        """
        Penaliza ambos scores cuando el volumen de la vela está por debajo del
        umbral mínimo (vol_avg × vol_mult).

        Una señal generada en una vela de bajo volumen es menos fiable porque
        no hay suficiente participación del mercado para confirmar el movimiento.

        Returns: (score_sell, score_buy, volumen_bajo)
            volumen_bajo — True si se aplicó la penalización.
        """
        if (pd.notna(vol_avg) and vol_avg > 0 and
                pd.notna(vol) and vol < vol_avg * vol_mult):
            penalizacion = 3
            score_sell = max(0, score_sell - penalizacion)
            score_buy  = max(0, score_buy  - penalizacion)
            return score_sell, score_buy, True
        return score_sell, score_buy, False

    # ─────────────────────────────────────────────────────────────────────────
    # Score mínimo adaptativo
    # ─────────────────────────────────────────────────────────────────────────

    def en_sesion_optima(self) -> bool:
        """
        Devuelve True si la hora UTC actual está dentro de la sesión óptima
        de trading para Gold (Londres + NY): 08:00 - 21:00 UTC.
        Fuera de ese rango el volumen es bajo y los movimientos son ruido.
        Poner SKIP_SESSION_FILTER=1 en .env para saltar este filtro (pruebas).
        """
        import os
        if os.getenv('SKIP_SESSION_FILTER', '0') == '1':
            return True
        from datetime import datetime, timezone
        hora = datetime.now(timezone.utc).hour
        return 8 <= hora < 21

    def umbral_activo_por_sesion(self, senal_alerta: bool, senal_media: bool,
                                  senal_fuerte: bool, senal_maxima: bool,
                                  tf_corto: bool = False) -> tuple:
        """
        Aplica el filtro de sesión sobre los flags de señal.

        TFs cortos (5M, 15M): fuera de sesión solo FUERTE y MÁXIMA.
        TFs largos (1H, 4H, 1D): fuera de sesión solo MEDIA, FUERTE y MÁXIMA.

        Returns: (alerta, media, fuerte, maxima) posiblemente reducidos.
        """
        if self.en_sesion_optima():
            return senal_alerta, senal_media, senal_fuerte, senal_maxima

        if tf_corto:
            # 5M / 15M: bloquear ALERTA y MEDIA fuera de sesión
            return False, False, senal_fuerte, senal_maxima
        else:
            # 1H / 4H / 1D: bloquear solo ALERTA fuera de sesión
            return False, senal_media, senal_fuerte, senal_maxima

    def umbral_adaptativo(self, base: int, atr: float, atr_media: float,
                          incremento: int = 2) -> int:
        """
        Eleva el umbral mínimo de señal cuando la volatilidad (ATR) supera
        1.5× su media reciente.

        En mercados de alta volatilidad se generan muchas señales falsas.
        Exigir más confirmación (umbral más alto) reduce los falsos positivos.

        Returns: umbral ajustado (base + incremento si ATR es elevado, o base).
        """
        if (pd.notna(atr_media) and atr_media > 0 and
                pd.notna(atr) and atr > atr_media * 1.5):
            return base + incremento
        return base
