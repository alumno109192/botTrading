"""
open_interest.py — Open Interest de futuros Gold (GC=F) via yfinance

El Open Interest (OI) mide el número total de contratos abiertos en el mercado.
Junto con el precio, permite identificar la fuerza real de una tendencia.

Interpretación clásica:
    Precio ↑ + OI ↑  → tendencia ALCISTA FUERTE (dinero entrando en largos)
    Precio ↑ + OI ↓  → subida DÉBIL (short covering, no convicción)
    Precio ↓ + OI ↑  → tendencia BAJISTA FUERTE (dinero entrando en cortos)
    Precio ↓ + OI ↓  → caída DÉBIL (liquidación de largos, posible suelo)

El sesgo resultante refuerza o debilita señales según la confluencia con el precio.

Cache: 30 minutos.

Uso:
    from services.open_interest import get_oi_bias, ajustar_score_por_oi
    bias = get_oi_bias()
    score_buy, score_sell = ajustar_score_por_oi(score_buy, score_sell, bias)
"""

import threading
import logging
import concurrent.futures
from datetime import datetime, timezone, timedelta

import yfinance as yf
import pandas as pd
from adapters.yf_lock import _yf_lock

logger = logging.getLogger(__name__)

_CACHE_TTL_MINUTES = 30
_cache: dict = {'bias': None, 'detalle': None, 'timestamp': None}
_cache_lock = threading.Lock()

# Umbrales para considerar variación significativa de OI
_OI_CAMBIO_MIN_PCT = 0.5   # cambio mínimo del 0.5% para ser relevante


def _calcular_oi_bias() -> tuple:
    """
    Descarga datos diarios de GC=F y analiza la tendencia del Open Interest
    en las últimas 5 velas.

    Returns:
        (bias: str, detalle: dict)
    """
    try:
        acquired = _yf_lock.acquire(timeout=10)
        if not acquired:
            return None, None
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(
                    yf.download, "GC=F", period="10d", interval="1d", progress=False
                )
                try:
                    df = future.result(timeout=15)
                except concurrent.futures.TimeoutError:
                    return None, None
        finally:
            _yf_lock.release()

        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        if df.empty or len(df) < 3:
            return None, None

        # yfinance devuelve Open Interest en la columna 'Volume' para futuros
        # El OI real requiere acceso a datos premium; usamos el proxy de volumen
        # acumulado como aproximación (disponible de forma gratuita)
        # Para OI real, intentamos con el ticker de opciones o campo específico
        ticker = yf.Ticker("GC=F")
        info = ticker.info

        # Intentar obtener OI del campo info (no siempre disponible en yf gratuito)
        oi_actual = info.get('openInterest', None)

        # Calcular cambio de precio en las últimas 2 velas cerradas
        close_actual = float(df['Close'].iloc[-2])
        close_prev   = float(df['Close'].iloc[-3])
        vol_actual   = float(df['Volume'].iloc[-2])
        vol_prev     = float(df['Volume'].iloc[-3])

        precio_sube = close_actual > close_prev * 1.001
        precio_baja = close_actual < close_prev * 0.999
        vol_sube    = vol_actual > vol_prev * (1 + _OI_CAMBIO_MIN_PCT / 100)
        vol_baja    = vol_actual < vol_prev * (1 - _OI_CAMBIO_MIN_PCT / 100)

        # Tendencia de volumen en 5 días como proxy de OI
        vols = df['Volume'].iloc[-6:-1].values
        vol_trend_alcista = vols[-1] > vols[0]  # último > primero

        # Determinar bias según reglas clásicas de OI/Volumen
        if precio_sube and vol_sube:
            bias = 'BULLISH_FUERTE'   # tendencia alcista confirmada
        elif precio_baja and vol_sube:
            bias = 'BEARISH_FUERTE'   # tendencia bajista confirmada
        elif precio_sube and vol_baja:
            bias = 'BULLISH_DEBIL'    # subida con falta de convicción
        elif precio_baja and vol_baja:
            bias = 'BEARISH_DEBIL'    # caída con falta de convicción (posible suelo)
        else:
            bias = 'NEUTRAL'

        detalle = {
            'close_actual': round(close_actual, 2),
            'close_prev': round(close_prev, 2),
            'vol_actual': int(vol_actual),
            'vol_prev': int(vol_prev),
            'oi_disponible': oi_actual,
            'bias': bias,
        }

        cambio_pct = (close_actual - close_prev) / close_prev * 100
        logger.info(
            f"  📊 [OI/Vol] Precio: ${close_actual:.2f} ({cambio_pct:+.2f}%) | "
            f"Volumen: {int(vol_actual):,} vs {int(vol_prev):,} | Sesgo: {bias}"
        )
        if oi_actual:
            logger.info(f"  📊 [OI] Open Interest: {oi_actual:,}")

        return bias, detalle

    except Exception as e:
        logger.warning(f"  ⚠️ [OI] Error calculando Open Interest: {e}")
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
