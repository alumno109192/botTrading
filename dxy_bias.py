"""
dxy_bias.py — Sesgo del USD Index (DX-Y.NYB) para correlación con Gold

Gold (XAUUSD) tiene correlación inversa estructural con el dólar americano.
Este módulo descarga DXY (disponible en yfinance sin delay intraday)
y calcula el sesgo de tendencia usando EMA9/EMA21 en 1H.

El resultado se cachea 30 minutos para evitar llamadas excesivas.

Uso:
    from dxy_bias import get_dxy_bias, ajustar_score_por_dxy

    bias = get_dxy_bias()
    score_buy, score_sell = ajustar_score_por_dxy(score_buy, score_sell, bias)
"""
import yfinance as yf
import pandas as pd
import threading
from datetime import datetime, timezone, timedelta

# ── Cache en memoria (se reinicia al reiniciar el proceso) ──
_cache: dict = {'bias': None, 'timestamp': None}
_cache_lock = threading.Lock()
_CACHE_TTL_MINUTES = 30


def get_dxy_bias() -> str | None:
    """
    Descarga DXY (DX-Y.NYB) en 1H y calcula el sesgo de tendencia.

    Retorna:
        'BULLISH'  → DXY alcista (penaliza BUY Gold, refuerza SELL Gold)
        'BEARISH'  → DXY bajista (favorece BUY Gold, penaliza SELL Gold)
        'NEUTRAL'  → Sin dirección clara
        None       → Error al descargar (no penalizar ni favorecer nada)

    Cacheado 30 minutos.
    """
    global _cache

    ahora = datetime.now(timezone.utc)
    with _cache_lock:
        if (_cache['bias'] is not None
                and _cache['timestamp'] is not None
                and (ahora - _cache['timestamp']) < timedelta(minutes=_CACHE_TTL_MINUTES)):
            return _cache['bias']

    try:
        dxy = yf.download("DX-Y.NYB", period="10d", interval="1h", progress=False)

        if isinstance(dxy.columns, pd.MultiIndex):
            dxy.columns = dxy.columns.get_level_values(0)

        if dxy.empty or len(dxy) < 20:
            print("  ⚠️ [DXY] Datos insuficientes — sesgo no calculado")
            return None

        close     = dxy['Close']
        ema9      = close.ewm(span=9,  adjust=False).mean()
        ema21     = close.ewm(span=21, adjust=False).mean()

        precio_actual = float(close.iloc[-1])
        ema9_actual   = float(ema9.iloc[-1])
        ema21_actual  = float(ema21.iloc[-1])

        if precio_actual > ema9_actual > ema21_actual:
            bias = "BULLISH"
            emoji = "📈"
        elif precio_actual < ema9_actual < ema21_actual:
            bias = "BEARISH"
            emoji = "📉"
        else:
            bias = "NEUTRAL"
            emoji = "➡️"

        print(f"  💵 DXY: {emoji} {bias}  "
              f"(precio={precio_actual:.3f}, EMA9={ema9_actual:.3f}, EMA21={ema21_actual:.3f})")

        with _cache_lock:
            _cache['bias']      = bias
            _cache['timestamp'] = ahora
        return bias

    except Exception as e:
        print(f"  ⚠️ [DXY] Error al descargar DX-Y.NYB: {e}")
        return None


def ajustar_score_por_dxy(score_buy: int, score_sell: int, bias: str | None) -> tuple:
    """
    Ajusta los scores de BUY y SELL según el sesgo del DXY.

    Correlación inversa Gold/DXY:
        DXY BULLISH → el USD sube → Gold tiende a bajar → penalizar BUY, reforzar SELL
        DXY BEARISH → el USD baja → Gold tiende a subir → favorecer BUY, penalizar SELL
        DXY NEUTRAL o None → sin ajuste

    Args:
        score_buy:  score acumulado de compra antes del ajuste
        score_sell: score acumulado de venta antes del ajuste
        bias:       resultado de get_dxy_bias()

    Returns:
        (score_buy ajustado, score_sell ajustado)
    """
    if bias == "BULLISH":
        score_buy  = max(0, score_buy  - 2)
        score_sell = score_sell + 1
        print(f"  💵 DXY BULLISH → score_buy -{2}, score_sell +1")
    elif bias == "BEARISH":
        score_buy  = score_buy  + 1
        score_sell = max(0, score_sell - 2)
        print(f"  💵 DXY BEARISH → score_buy +1, score_sell -{2}")
    # NEUTRAL o None → sin cambio

    return score_buy, score_sell
