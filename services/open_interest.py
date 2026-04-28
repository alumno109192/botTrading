"""
open_interest.py — Sesgo de Volumen/OI de Gold via Twelve Data

Usa precio y volumen de GC=F (XAU/USD) en 1d para inferir la fuerza de la tendencia.
Ya no depende de yfinance.

Interpretación clásica:
    Precio ↑ + Vol ↑  → tendencia ALCISTA FUERTE
    Precio ↑ + Vol ↓  → subida DÉBIL (short covering, no convicción)
    Precio ↓ + Vol ↑  → tendencia BAJISTA FUERTE
    Precio ↓ + Vol ↓  → caída DÉBIL (liquidación, posible suelo)

Cache: 30 minutos.

Uso:
    from services.open_interest import get_oi_bias, ajustar_score_por_oi
    bias = get_oi_bias()
    score_buy, score_sell = ajustar_score_por_oi(score_buy, score_sell, bias)
"""

import threading
import logging
from datetime import datetime, timezone, timedelta

from adapters.data_provider import get_ohlcv

logger = logging.getLogger(__name__)

_CACHE_TTL_MINUTES = 30
_cache: dict = {'bias': None, 'detalle': None, 'timestamp': None}
_cache_lock = threading.Lock()

_OI_CAMBIO_MIN_PCT = 0.5


def _calcular_oi_bias() -> tuple:
    """
    Descarga datos diarios de GC=F via Twelve Data y analiza precio/volumen.
    Returns: (bias: str, detalle: dict)
    """
    try:
        df, _ = get_ohlcv('GC=F', period='10d', interval='1d')

        if df is None or df.empty or len(df) < 3:
            return None, None

        close_actual = float(df['Close'].iloc[-2])
        close_prev   = float(df['Close'].iloc[-3])
        vol_actual   = float(df['Volume'].iloc[-2])
        vol_prev     = float(df['Volume'].iloc[-3])

        precio_sube = close_actual > close_prev * 1.001
        precio_baja = close_actual < close_prev * 0.999
        vol_sube    = vol_actual > vol_prev * (1 + _OI_CAMBIO_MIN_PCT / 100)
        vol_baja    = vol_actual < vol_prev * (1 - _OI_CAMBIO_MIN_PCT / 100)

        if precio_sube and vol_sube:
            bias = 'BULLISH_FUERTE'
        elif precio_baja and vol_sube:
            bias = 'BEARISH_FUERTE'
        elif precio_sube and vol_baja:
            bias = 'BULLISH_DEBIL'
        elif precio_baja and vol_baja:
            bias = 'BEARISH_DEBIL'
        else:
            bias = 'NEUTRAL'

        detalle = {
            'close_actual': round(close_actual, 2),
            'close_prev':   round(close_prev, 2),
            'vol_actual':   int(vol_actual),
            'vol_prev':     int(vol_prev),
            'bias':         bias,
        }

        cambio_pct = (close_actual - close_prev) / close_prev * 100
        logger.info(
            f"  📊 [OI/Vol] Precio: ${close_actual:.2f} ({cambio_pct:+.2f}%) | "
            f"Volumen: {int(vol_actual)} vs {int(vol_prev)} | Sesgo: {bias}"
        )

        return bias, detalle

    except Exception as e:
        logger.warning(f"  ⚠️ [OI] Error calculando sesgo volumen: {e}")
        return None, None


def get_oi_bias() -> str | None:
    """
    Devuelve el sesgo de Open Interest/Volumen para Gold.

    Returns:
        'BULLISH_FUERTE'  → precio ↑ + volumen ↑ → tendencia alcista real
        'BEARISH_FUERTE'  → precio ↓ + volumen ↑ → tendencia bajista real
        'BULLISH_DEBIL'   → precio ↑ + volumen ↓ → subida sin convicción
        'BEARISH_DEBIL'   → precio ↓ + volumen ↓ → caída sin convicción (rebote posible)
        'NEUTRAL'         → sin señal clara
        None              → error

    Cacheado 30 minutos.
    """
    global _cache
    ahora = datetime.now(timezone.utc)

    with _cache_lock:
        if (_cache['bias'] is not None
                and _cache['timestamp'] is not None
                and (ahora - _cache['timestamp']) < timedelta(minutes=_CACHE_TTL_MINUTES)):
            return _cache['bias']

    bias, detalle = _calcular_oi_bias()

    with _cache_lock:
        _cache.update({'bias': bias, 'detalle': detalle, 'timestamp': ahora})

    return bias


def ajustar_score_por_oi(score_buy: int, score_sell: int,
                          bias: str | None) -> tuple:
    """
    Ajusta los scores según el sesgo de Open Interest/Volumen.

    Lógica:
        BULLISH_FUERTE → confirma tendencia alcista: +2 BUY, -1 SELL
        BEARISH_FUERTE → confirma tendencia bajista: +2 SELL, -1 BUY
        BULLISH_DEBIL  → subida sin convicción: sin cambio (no confirmar ni penalizar)
        BEARISH_DEBIL  → caída sin convicción, posible suelo: +1 BUY (rebote)
        NEUTRAL / None → sin cambio

    Returns:
        (score_buy ajustado, score_sell ajustado)
    """
    if bias == 'BULLISH_FUERTE':
        score_buy  = score_buy  + 2
        score_sell = max(0, score_sell - 1)
        logger.info(f"  📊 OI BULLISH FUERTE → score_buy +2, score_sell -1")
    elif bias == 'BEARISH_FUERTE':
        score_buy  = max(0, score_buy  - 1)
        score_sell = score_sell + 2
        logger.info(f"  📊 OI BEARISH FUERTE → score_buy -1, score_sell +2")
    elif bias == 'BEARISH_DEBIL':
        score_buy  = score_buy  + 1
        logger.info(f"  📊 OI BEARISH DÉBIL → posible suelo, score_buy +1")
    # BULLISH_DEBIL y NEUTRAL → sin cambio

    return score_buy, score_sell
