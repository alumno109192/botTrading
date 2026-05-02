"""
yield_bias.py — Sesgo de Yields Reales (TIPS 10Y) para correlación con Gold

El yield real (nominal 10Y - inflación breakeven 10Y) tiene la correlación
inversa más robusta con el oro a medio plazo:
    Yield real SUBE → coste de oportunidad de tener oro sube → presión BAJISTA en Gold
    Yield real BAJA → coste de oportunidad de tener oro baja → presión ALCISTA en Gold

Fuentes (Yahoo Finance, sin API key):
    ^TNX  — Treasury Note 10Y Yield (nominal, %)
    RINF  — ProShares Inflation Expectations ETF (proxy breakeven)
         ó
    TIP   — iShares TIPS Bond ETF (proxy alternativo)

Yield real aproximado: yield_real ≈ TNX (%) - breakeven (%)
donde breakeven = ((TIP_precio / 100) * factor) ← aproximación lineal sobre cambio % diario

Método simplificado y robusto:
    - Se descarga ^TNX (yield nominal 10Y, últimas 30 velas diarias)
    - Se descarga ^FVX (Treasury 5Y) como señal de dirección del tramo corto
    - Se usa el cambio en TNX vs su media 10D para determinar dirección:
        TNX subiendo (por encima de media 10D) → yields reales subiendo → BEARISH Gold
        TNX bajando  (por debajo de media 10D) → yields reales bajando → BULLISH Gold

Cache: 4 horas (los yields cambian cada día, no intradía de forma significativa)

Uso:
    from services.yield_bias import get_yield_bias, ajustar_score_por_yield
    bias, yield_val, yield_ma = get_yield_bias()
    score_buy, score_sell = ajustar_score_por_yield(score_buy, score_sell, bias)
"""

import threading
import logging
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

_CACHE_TTL_HOURS = 4
_cache: dict = {'bias': None, 'yield_val': None, 'yield_ma': None, 'timestamp': None}
_cache_lock = threading.Lock()


def _fetch_yields() -> tuple[float | None, float | None]:
    """
    Descarga ^TNX (yield nominal 10Y) via yfinance y calcula la media 10D.
    Retorna (yield_actual, yield_media_10d) o (None, None) si falla.
    """
    try:
        import yfinance as yf
        tick = yf.Ticker('^TNX')
        df = tick.history(period='30d', interval='1d', auto_adjust=True)
        if df is None or len(df) < 11:
            logger.warning('  ⚠️ [YIELD] Datos ^TNX insuficientes')
            return None, None
        close = df['Close']
        yield_actual = float(close.iloc[-1])
        yield_ma10   = float(close.iloc[-11:-1].mean())  # media de las 10 anteriores (excluye hoy)
        return yield_actual, yield_ma10
    except Exception as e:
        logger.warning(f'  ⚠️ [YIELD] Error descargando ^TNX: {e}')
        return None, None


def get_yield_bias() -> tuple[str | None, float | None, float | None]:
    """
    Calcula el sesgo de yields reales 10Y para Gold.

    Lógica:
        TNX > MA10 + umbral → yields subiendo → coste de oportunidad alto → BEARISH Gold
        TNX < MA10 - umbral → yields bajando → coste de oportunidad bajo  → BULLISH Gold
        Entre umbrales       → NEUTRAL

    Umbral: 0.05 pp (5 puntos básicos) para evitar ruido en días sin movimiento.

    Retorna:
        (bias, yield_actual, yield_ma10)
        bias: 'BULLISH' | 'BEARISH' | 'NEUTRAL' | None
    """
    ahora = datetime.now(timezone.utc)
    with _cache_lock:
        if (_cache['bias'] is not None
                and _cache['timestamp'] is not None
                and (ahora - _cache['timestamp']) < timedelta(hours=_CACHE_TTL_HOURS)):
            return _cache['bias'], _cache['yield_val'], _cache['yield_ma']

    yield_val, yield_ma = _fetch_yields()

    if yield_val is None or yield_ma is None:
        return None, None, None

    umbral = 0.05  # 5 puntos básicos

    if yield_val > yield_ma + umbral:
        bias = 'BEARISH'   # yields subiendo → presión bajista en Gold
        emoji = '📈'
    elif yield_val < yield_ma - umbral:
        bias = 'BULLISH'   # yields bajando → presión alcista en Gold
        emoji = '📉'
    else:
        bias = 'NEUTRAL'
        emoji = '➡️'

    logger.info(
        f'  📊 YIELDS 10Y: {emoji} {bias}  '
        f'(TNX={yield_val:.3f}%, MA10={yield_ma:.3f}%)'
    )

    with _cache_lock:
        _cache['bias']      = bias
        _cache['yield_val'] = yield_val
        _cache['yield_ma']  = yield_ma
        _cache['timestamp'] = ahora

    return bias, yield_val, yield_ma


def ajustar_score_por_yield(
        score_buy: int,
        score_sell: int,
        bias: str | None,
) -> tuple[int, int]:
    """
    Ajusta los scores de BUY y SELL según el sesgo de yields reales.

    Correlación inversa Gold / Yields Reales:
        BEARISH (yields subiendo) → penalizar BUY Gold, reforzar SELL
        BULLISH (yields bajando)  → favorecer BUY Gold, penalizar SELL
        NEUTRAL o None            → sin ajuste

    El ajuste es intencionadamente conservador (±2 / ±1) para evitar
    que un único factor macro anule señales técnicas válidas.

    Args:
        score_buy:  score acumulado de compra antes del ajuste
        score_sell: score acumulado de venta antes del ajuste
        bias:       resultado de get_yield_bias()[0]

    Returns:
        (score_buy ajustado, score_sell ajustado)
    """
    if bias == 'BEARISH':
        # Yields subiendo → coste de oportunidad alto → presión bajista en Gold
        score_buy  = max(0, score_buy  - 2)
        score_sell = score_sell + 1
        logger.info(f'  📊 YIELD BEARISH → score_buy -2, score_sell +1')
    elif bias == 'BULLISH':
        # Yields bajando → presión alcista en Gold
        score_buy  = score_buy  + 1
        score_sell = max(0, score_sell - 2)
        logger.info(f'  📊 YIELD BULLISH → score_buy +1, score_sell -2')
    # NEUTRAL o None → sin cambio
    return score_buy, score_sell
