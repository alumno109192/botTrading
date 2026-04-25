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
import pandas as pd
import numpy as np
from abc import ABC, abstractmethod

import adapters.telegram as _telegram_mod
from core.indicators import (
    calcular_rsi, calcular_ema, calcular_atr,
    calcular_bollinger_bands, calcular_macd, calcular_obv, calcular_adx,
)


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

        # Inicializar BD si las variables están configuradas
        import logging as _logging
        _logger = _logging.getLogger('bottrading')
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

        # Resistencia: zona centrada en el swing high (más banda bajo el pivot, donde venden)
        zrh = round(resist_pivot + zone_width * 0.25, 2)
        zrl = round(resist_pivot - zone_width * 0.75, 2)
        # Soporte: zona centrada en el swing low (más banda sobre el pivot, donde compran)
        zsh = round(support_pivot + zone_width * 0.75, 2)
        zsl = round(support_pivot - zone_width * 0.25, 2)

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
    # Telegram wrapper
    # ─────────────────────────────────────────────────────────────────────────

    def enviar(self, mensaje: str) -> bool:
        """
        Envía un mensaje a Telegram añadiendo el sufijo de aviso macro si existe.
        Delega en adapters.telegram.enviar_telegram para facilitar el mocking.
        """
        sufijo = (f"\n⚠️ <b>Evento macro próximo:</b> {self.aviso_macro}"
                  if self.aviso_macro else "")
        return _telegram_mod.enviar_telegram(mensaje + sufijo,
                                             self.telegram_thread_id)
