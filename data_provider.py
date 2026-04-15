"""
data_provider.py — Fuente de datos OHLCV con prioridad de fuentes

Prioridad (de mejor a peor):
  1. Twelve Data (gratuito, 800 req/día, tiempo real) → TWELVE_DATA_API_KEY en .env
  2. Polygon.io  (de pago, ~$29/mes, tiempo real)     → POLYGON_API_KEY en .env
  3. yfinance fallback (gratuito, 15 min delay en intraday)

Uso:
    from data_provider import get_ohlcv
    df, is_delayed = get_ohlcv('GC=F', period='5d', interval='15m')
    if is_delayed:
        print("⚠️ Datos con 15 min de delay (yfinance free)")

Obtener clave Twelve Data gratuita:
    https://twelvedata.com/apikey  →  Sign Up → Free plan (800 req/día)
    Añadir al .env:  TWELVE_DATA_API_KEY=tu_clave
"""
import os
import yfinance as yf
import pandas as pd
import requests
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

load_dotenv()

TWELVE_DATA_API_KEY = os.environ.get('TWELVE_DATA_API_KEY')
POLYGON_API_KEY     = os.environ.get('POLYGON_API_KEY')

# Mapa de tickers yfinance → símbolo Twelve Data
_TICKER_MAP_TWELVE = {
    'GC=F':  'XAU/USD',   # Gold Spot
    'SI=F':  'XAG/USD',   # Silver Spot
}

# Mapa de tickers yfinance → símbolo Polygon.io Forex/Metals
_TICKER_MAP_POLYGON = {
    'GC=F':  'C:XAUUSD',   # Gold Spot
    'SI=F':  'C:XAGUSD',   # Silver Spot
}

# Intervalos soportados por fuentes externas (solo intraday merece el cambio)
_INTRADAY_INTERVALS = {'1m', '5m', '15m', '30m', '1h'}


def get_ohlcv(ticker_yf: str, period: str, interval: str) -> tuple:
    """
    Descarga datos OHLCV para el ticker y periodo indicados.

    Orden de prioridad:
      1. Twelve Data (gratuito, tiempo real) si TWELVE_DATA_API_KEY configurada
      2. Polygon.io  (de pago, tiempo real)  si POLYGON_API_KEY configurada
      3. yfinance    (delay 15 min en intraday)

    Retorna:
        (DataFrame, is_delayed: bool)
        - is_delayed=False → datos en tiempo real
        - is_delayed=True  → datos con delay de yfinance

    El DataFrame siempre tiene columnas: Open, High, Low, Close, Volume
    """
    if interval in _INTRADAY_INTERVALS:
        # ── Intentar Twelve Data primero (gratuito) ──
        if TWELVE_DATA_API_KEY and ticker_yf in _TICKER_MAP_TWELVE:
            df, ok = _get_twelve_data(ticker_yf, period, interval)
            if ok and not df.empty and len(df) >= 10:
                print(f"  ✅ [data_provider] Twelve Data — {ticker_yf} {interval} ({len(df)} velas, tiempo real)")
                return df, False
            print(f"  ⚠️ [data_provider] Twelve Data falló — intentando siguiente fuente")

        # ── Intentar Polygon.io (de pago) ──
        if POLYGON_API_KEY and ticker_yf in _TICKER_MAP_POLYGON:
            df, ok = _get_polygon(ticker_yf, period, interval)
            if ok and not df.empty and len(df) >= 10:
                print(f"  ✅ [data_provider] Polygon.io — {ticker_yf} {interval} ({len(df)} velas, tiempo real)")
                return df, False
            print(f"  ⚠️ [data_provider] Polygon.io falló — usando yfinance (delay 15m)")

    # ── Fallback: yfinance ──
    df = yf.download(ticker_yf, period=period, interval=interval, progress=False)

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    if not df.empty:
        expected = {'Open', 'High', 'Low', 'Close', 'Volume'}
        if not expected.issubset(set(df.columns)):
            if len(df.columns) == 5:
                df.columns = ['Open', 'High', 'Low', 'Close', 'Volume']
            elif len(df.columns) == 6:
                df.columns = ['Open', 'High', 'Low', 'Close', 'Adj Close', 'Volume']

    return df, True


def _get_twelve_data(ticker_yf: str, period: str, interval: str) -> tuple:
    """
    Descarga datos desde Twelve Data API (plan gratuito: 800 req/día).
    Retorna: (DataFrame, success: bool)

    Documentación: https://twelvedata.com/docs#time-series
    """
    try:
        ticker_td = _TICKER_MAP_TWELVE[ticker_yf]

        # Twelve Data usa outputsize (número de velas), no period
        dias_map     = {'1d': 1, '2d': 2, '5d': 5, '7d': 7, '1mo': 30}
        velas_por_dia = {
            '1m': 960, '5m': 192, '15m': 64, '30m': 32, '1h': 16,
        }
        dias      = dias_map.get(period, 5)
        por_dia   = velas_por_dia.get(interval, 64)
        outputsize = min(dias * por_dia, 5000)

        # Twelve Data usa "1min", "5min", "15min", "1h"
        interval_td_map = {
            '1m': '1min', '5m': '5min', '15m': '15min', '30m': '30min', '1h': '1h',
        }
        interval_td = interval_td_map.get(interval, '15min')

        url = (
            f"https://api.twelvedata.com/time_series"
            f"?symbol={ticker_td}"
            f"&interval={interval_td}"
            f"&outputsize={outputsize}"
            f"&timezone=UTC"
            f"&apikey={TWELVE_DATA_API_KEY}"
        )

        r = requests.get(url, timeout=15)
        if r.status_code != 200:
            print(f"  ⚠️ Twelve Data HTTP {r.status_code}: {r.text[:80]}")
            return pd.DataFrame(), False

        data = r.json()

        # Gestionar errores de la API (límite diario, clave inválida, etc.)
        if data.get('status') == 'error' or 'values' not in data:
            msg = data.get('message', data.get('status', 'unknown error'))
            print(f"  ⚠️ Twelve Data error: {msg}")
            return pd.DataFrame(), False

        values = data['values']
        if not values:
            return pd.DataFrame(), False

        df = pd.DataFrame(values)
        df['datetime'] = pd.to_datetime(df['datetime'], utc=True)
        df = df.set_index('datetime').sort_index()
        df = df.rename(columns={
            'open': 'Open', 'high': 'High', 'low': 'Low',
            'close': 'Close', 'volume': 'Volume'
        })
        cols = [c for c in ['Open', 'High', 'Low', 'Close', 'Volume'] if c in df.columns]
        df = df[cols].astype(float)

        # Twelve Data para XAU/USD puede no incluir Volume — añadir ceros
        if 'Volume' not in df.columns:
            df['Volume'] = 0.0

        return df, True

    except Exception as e:
        print(f"  ⚠️ Twelve Data excepción: {e}")
        return pd.DataFrame(), False


def _get_polygon(ticker_yf: str, period: str, interval: str) -> tuple:
    """
    Descarga datos desde la API REST de Polygon.io.
    Retorna: (DataFrame, success: bool)
    """
    try:
        ticker_polygon = _TICKER_MAP_POLYGON[ticker_yf]

        ahora = datetime.now(timezone.utc)
        dias_map = {'1d': 1, '2d': 2, '5d': 5, '7d': 7, '1mo': 30, '3mo': 90}
        dias = dias_map.get(period, 5)
        desde = ahora - timedelta(days=dias + 1)  # +1 para buffer

        mult_map = {
            '1m':  (1,  'minute'),
            '5m':  (5,  'minute'),
            '15m': (15, 'minute'),
            '30m': (30, 'minute'),
            '1h':  (1,  'hour'),
        }
        mult, timespan = mult_map.get(interval, (15, 'minute'))

        url = (
            f"https://api.polygon.io/v2/aggs/ticker/{ticker_polygon}/range/"
            f"{mult}/{timespan}"
            f"/{desde.strftime('%Y-%m-%d')}/{ahora.strftime('%Y-%m-%d')}"
            f"?adjusted=true&sort=asc&limit=5000&apiKey={POLYGON_API_KEY}"
        )

        r = requests.get(url, timeout=15)
        if r.status_code != 200:
            print(f"  ⚠️ Polygon.io HTTP {r.status_code}: {r.text[:80]}")
            return pd.DataFrame(), False

        data = r.json()
        if data.get('resultsCount', 0) == 0 or not data.get('results'):
            return pd.DataFrame(), False

        results = data['results']
        df = pd.DataFrame(results)
        df['timestamp'] = pd.to_datetime(df['t'], unit='ms', utc=True)
        df = df.set_index('timestamp')
        df = df.rename(columns={
            'o': 'Open', 'h': 'High', 'l': 'Low', 'c': 'Close', 'v': 'Volume'
        })
        df = df[['Open', 'High', 'Low', 'Close', 'Volume']].copy()
        df = df.astype(float)

        return df, True

    except Exception as e:
        print(f"  ⚠️ Polygon.io excepción: {e}")
        return pd.DataFrame(), False
