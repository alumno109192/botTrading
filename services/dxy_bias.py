"""
dxy_bias.py — Sesgo del USD Index para correlación con Gold

Gold (XAUUSD) tiene correlación inversa estructural con el dólar americano.
Este módulo descarga DXY via Twelve Data (sin yfinance) y calcula el sesgo
de tendencia usando EMA9/EMA21 en 1H.

El resultado se cachea 30 minutos para evitar llamadas excesivas.

Uso:
    from services.dxy_bias import get_dxy_bias, ajustar_score_por_dxy

    bias = get_dxy_bias()
    score_buy, score_sell = ajustar_score_por_dxy(score_buy, score_sell, bias)
"""
import os
import requests
import pandas as pd
import threading
from datetime import datetime, timezone, timedelta

# Twelve Data: DXY disponible con símbolo "USD/CAD" no, pero sí como índice:
# En plan gratuito, el DXY (US Dollar Index) se obtiene con el símbolo "DX-Y.NYB"
# en Twelve Data como "USD" o bien como índice forex "USD".
# Usamos EUR/USD como proxy invertido si DXY no está disponible en plan free:
# DXY sube ≈ EURUSD baja (correlación ~-0.96). Un proxy fiable y siempre disponible.
_ENV_KEYS = [
    ('key1',  'TWELVE_DATA_API_KEY'),
    ('key2',  'TWELVE_DATA_API_KEY_2'),
    ('key3',  'TWELVE_DATA_API_KEY_3'),
    ('key4',  'TWELVE_DATA_API_KEY_4'),
    ('key5',  'TWELVE_DATA_API_KEY_5'),
    ('key6',  'TWELVE_DATA_API_KEY_6'),
    ('key7',  'TWELVE_DATA_API_KEY_7'),
    ('key8',  'TWELVE_DATA_API_KEY_8'),
    ('key9',  'TWELVE_DATA_API_KEY_9'),
    ('key10', 'TWELVE_DATA_API_KEY_10'),
    ('key11', 'TWELVE_DATA_API_KEY_11'),
]
_TWELVE_DATA_KEYS = [
    (alias, os.environ.get(env, '').strip())
    for alias, env in _ENV_KEYS
    if os.environ.get(env, '').strip()
]


def _next_dxy_key():
    """Elige la key con MENOS uso hoy (delega en data_provider para reutilizar cache)."""
    if not _TWELVE_DATA_KEYS:
        return None, None
    try:
        from adapters.data_provider import _next_td_key
        return _next_td_key()
    except Exception:
        # Fallback local: primera key disponible
        return _TWELVE_DATA_KEYS[0]


def _registrar_uso_dxy(alias: str):
    """Registra el consumo de la key en BD (best-effort)."""
    try:
        from adapters.data_provider import _registrar_uso_key
        _registrar_uso_key(alias)
    except Exception:
        pass

_cache: dict = {'bias': None, 'timestamp': None, 'precio': None, 'ema9': None, 'ema21': None}
_cache_lock = threading.Lock()
_CACHE_TTL_MINUTES = 30


def _fetch_dxy_twelve() -> pd.DataFrame:
    """
    Descarga DXY via Twelve Data usando el símbolo EUR/USD como proxy inverso.
    DXY ≈ inverso de EUR/USD (correlación ~-0.96).
    Cuando EUR/USD sube → DXY baja (bearish DXY = bullish Gold).
    Cuando EUR/USD baja → DXY sube (bullish DXY = bearish Gold).
    Retorna DataFrame con columna 'Close' (precio EUR/USD) o vacío si falla.
    """
    for alias, key in _TWELVE_DATA_KEYS:
        try:
            url = (
                "https://api.twelvedata.com/time_series"
                "?symbol=EUR/USD"
                "&interval=1h"
                "&outputsize=50"
                "&timezone=UTC"
                f"&apikey={key}"
            )
            r = requests.get(url, timeout=15)
            if r.status_code != 200:
                continue
            data = r.json()
            if data.get('status') == 'error' or 'values' not in data:
                continue
            _registrar_uso_dxy(alias)
            df = pd.DataFrame(data['values'])
            df['datetime'] = pd.to_datetime(df['datetime'], utc=True)
            df = df.set_index('datetime').sort_index()
            df['Close'] = df['close'].astype(float)
            return df[['Close']]
        except Exception:
            continue
    return pd.DataFrame()


def get_dxy_bias() -> str | None:
    """
    Calcula el sesgo del dólar usando EUR/USD (proxy inverso de DXY) via Twelve Data.

    Lógica: EUR/USD sube → DXY baja → Gold sube (BEARISH DXY)
             EUR/USD baja → DXY sube → Gold baja (BULLISH DXY)

    Retorna:
        'BULLISH'  → DXY alcista (penaliza BUY Gold, refuerza SELL Gold)
        'BEARISH'  → DXY bajista (favorece BUY Gold, penaliza SELL Gold)
        'NEUTRAL'  → Sin dirección clara
        None       → Error al descargar

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
        df = _fetch_dxy_twelve()
        if df.empty or len(df) < 20:
            print("  ⚠️ [DXY] Datos EUR/USD insuficientes — sesgo no calculado")
            return None

        close = df['Close']
        ema9  = close.ewm(span=9,  adjust=False).mean()
        ema21 = close.ewm(span=21, adjust=False).mean()

        precio_actual = float(close.iloc[-1])
        ema9_actual   = float(ema9.iloc[-1])
        ema21_actual  = float(ema21.iloc[-1])

        # EUR/USD sube (ema9 > ema21) → DXY baja → BEARISH DXY
        # EUR/USD baja (ema9 < ema21) → DXY sube → BULLISH DXY
        if precio_actual < ema9_actual < ema21_actual:
            # EURUSD cayendo → DXY subiendo
            bias = "BULLISH"
            emoji = "📈"
        elif precio_actual > ema9_actual > ema21_actual:
            # EURUSD subiendo → DXY bajando
            bias = "BEARISH"
            emoji = "📉"
        else:
            bias = "NEUTRAL"
            emoji = "➡️"

        print(f"  💵 DXY: {emoji} {bias}  "
              f"(EUR/USD={precio_actual:.5f}, EMA9={ema9_actual:.5f}, EMA21={ema21_actual:.5f})")

        with _cache_lock:
            _cache['bias']      = bias
            _cache['timestamp'] = ahora
            _cache['precio']    = precio_actual
            _cache['ema9']      = ema9_actual
            _cache['ema21']     = ema21_actual
        return bias

    except Exception as e:
        print(f"  ⚠️ [DXY] Error calculando sesgo DXY: {e}")
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
