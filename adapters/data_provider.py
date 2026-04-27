"""
data_provider.py — Fuente de datos OHLCV con prioridad de fuentes

Prioridad (de mejor a peor):
  1. Twelve Data (gratuito, 800 req/día, tiempo real) → TWELVE_DATA_API_KEY en .env
  2. Polygon.io  (de pago, ~$29/mes, tiempo real)     → POLYGON_API_KEY en .env
  3. yfinance fallback (gratuito, 15 min delay en intraday)

Uso:
    from adapters.data_provider import get_ohlcv
    df, is_delayed = get_ohlcv('GC=F', period='5d', interval='15m')
    if is_delayed:
        print("⚠️ Datos con 15 min de delay (yfinance free)")

Obtener claves Twelve Data gratuitas (3 cuentas = 2400 req/día):
    https://twelvedata.com/apikey  →  Sign Up → Free plan (800 req/día c/u)
    Añadir al .env:
        TWELVE_DATA_API_KEY=clave_cuenta_1
        TWELVE_DATA_API_KEY_2=clave_cuenta_2  ← opcional, backup automático
        TWELVE_DATA_API_KEY_3=clave_cuenta_3  ← opcional, backup automático
"""
import os
import itertools
import threading
import yfinance as yf
import pandas as pd
import requests
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

load_dotenv()

TWELVE_DATA_API_KEY   = os.environ.get('TWELVE_DATA_API_KEY')
TWELVE_DATA_API_KEY_2 = os.environ.get('TWELVE_DATA_API_KEY_2')
TWELVE_DATA_API_KEY_3 = os.environ.get('TWELVE_DATA_API_KEY_3')
POLYGON_API_KEY       = os.environ.get('POLYGON_API_KEY')

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

# Intervalos soportados por fuentes externas (incluye 4h para Twelve Data)
_INTRADAY_INTERVALS = {'1m', '5m', '15m', '30m', '1h', '4h'}

# ── Cache para datos diarios (1d) — evita re-descargar 730 filas cada 10 min ──
_daily_cache: dict = {}   # key = (ticker, period) → {'df': DataFrame, 'ts': datetime}
_daily_cache_lock = threading.Lock()
_DAILY_TTL = timedelta(hours=4)  # Re-descargar cada 4h (suficiente para velas diarias)

# ── Cache intraday (TTL 65s) — comparte datos entre detectores del mismo activo ──
# Los detectores 5M, 15M y 1H usan el mismo (ticker, period, interval) → una sola llamada a la API
_intraday_cache: dict = {}
_intraday_cache_lock = threading.Lock()
_INTRADAY_TTL = timedelta(seconds=65)

# ── Tiempo máximo desde la última vela en BD para considerar datos “frescos” ──
_DB_STALE = {
    '1m':  timedelta(minutes=2),
    '5m':  timedelta(minutes=10),
    '15m': timedelta(minutes=30),
    '1h':  timedelta(hours=2),
    '4h':  timedelta(hours=8),
    '1d':  timedelta(hours=48),
}

# ── Round-Robin de API Keys ──────────────────────────────────────────────────
# Se construye la lista al arranque con las keys disponibles.
# El ciclo se comparte entre todos los threads (protegido con lock).

def _build_key_list():
    """Construye lista de (alias, key) con las keys configuradas."""
    keys = []
    for alias, key in [
        ('key1', TWELVE_DATA_API_KEY),
        ('key2', TWELVE_DATA_API_KEY_2),
        ('key3', TWELVE_DATA_API_KEY_3),
    ]:
        if key:
            keys.append((alias, key))
    return keys

_td_keys = _build_key_list()
_td_cycle = itertools.cycle(_td_keys) if _td_keys else iter([])
_td_cycle_lock = threading.Lock()

def _next_td_key():
    """Devuelve (alias, key) siguiendo Round-Robin. None si no hay keys."""
    if not _td_keys:
        return None, None
    with _td_cycle_lock:
        return next(_td_cycle)

def _registrar_uso_key(alias: str):
    """Persiste el uso de la key en BD de forma no bloqueante (best-effort)."""
    try:
        from adapters.database import DatabaseManager
        db = DatabaseManager()
        total = db.incrementar_uso_key(alias)
        if total % 100 == 0:  # Log cada 100 peticiones para no saturar stdout
            print(f"  📊 [quota] {alias}: {total} peticiones hoy")
    except Exception:
        pass  # Nunca debe bloquear la petición de datos


def _guardar_en_db(ticker_yf: str, interval: str, df: pd.DataFrame):
    """Persiste DataFrame OHLCV en la tabla ohlcv de la BD (best-effort, no bloquea)."""
    try:
        from adapters.database import DatabaseManager
        db = DatabaseManager()
        rows = []
        for ts, row in df.iterrows():
            ts_str = ts.isoformat() if hasattr(ts, 'isoformat') else str(ts)
            rows.append((
                ts_str,
                float(row['Open']), float(row['High']),
                float(row['Low']),  float(row['Close']),
                float(row.get('Volume', 0))
            ))
        db.guardar_velas(ticker_yf, interval, rows)
    except Exception as e:
        print(f"  ⚠️ [data_provider] Error guardando en BD: {e}")


def _get_from_db(ticker_yf: str, period: str, interval: str):
    """
    Intenta servir datos OHLCV desde la BD local.
    - Para 5m, 15m, 1h: lee velas 5m almacenadas y resamplea si es necesario.
    - Para 4h, 1d: lee directamente el intervalo almacenado.
    Retorna (DataFrame, is_delayed) o (None, None) si los datos no existen o están obsoletos.
    """
    try:
        from adapters.database import DatabaseManager
        db = DatabaseManager()
    except Exception:
        return None, None

    # 5m/15m/1h se almacenan como 5m en BD y se resamplean en memoria
    db_interval = '5m' if interval in ('5m', '15m', '1h') else interval
    rows = db.obtener_velas(ticker_yf, db_interval, period)

    if not rows or len(rows) < 10:
        return None, None

    # Comprobar frescura: última vela dentro del umbral permitido
    ultima_ts = pd.to_datetime(rows[-1]['ts'], utc=True)
    umbral = _DB_STALE.get(db_interval, timedelta(minutes=10))
    if (datetime.now(timezone.utc) - ultima_ts) > umbral:
        return None, None

    # Construir DataFrame
    df = pd.DataFrame(rows)
    df['ts'] = pd.to_datetime(df['ts'], utc=True)
    df = df.set_index('ts')
    df = df.rename(columns={
        'open': 'Open', 'high': 'High', 'low': 'Low', 'close': 'Close', 'volume': 'Volume'
    })
    df = df[['Open', 'High', 'Low', 'Close', 'Volume']].astype(float)

    # Resamplear si el intervalo pedido es mayor que el almacenado
    _agg = {'Open': 'first', 'High': 'max', 'Low': 'min', 'Close': 'last', 'Volume': 'sum'}
    if interval == '15m':
        df = df.resample('15min').agg(_agg).dropna()
    elif interval == '1h':
        df = df.resample('1h').agg(_agg).dropna()

    if df.empty or len(df) < 10:
        return None, None

    print(f"  🗄️ [data_provider] BD hit — {ticker_yf} {interval} ({len(df)} velas)")
    return df, False  # Datos de BD se originaron en Twelve Data → no delayed


def poll_ohlcv(ticker_yf: str, interval: str = '5m') -> bool:
    """
    Descarga velas de Twelve Data y las persiste en BD, saltando el cache en memoria.
    Llamado por ohlcv_poller cada 60 segundos.
    Retorna True si la operación fue exitosa.
    """
    if not _td_keys or ticker_yf not in _TICKER_MAP_TWELVE:
        return False
    try:
        from adapters.database import DatabaseManager
        db = DatabaseManager()
    except Exception:
        return False

    # Decidir periodo: fill inicial 7d o incremental 1d
    # Umbrales mínimos de velas para considerar el historial suficiente
    _MIN_CANDLES = {'5m': 800, '15m': 150, '1h': 100, '4h': 30, '1d': 20}
    min_candles = _MIN_CANDLES.get(interval, 100)

    ultima_ts_str = db.obtener_ultima_ts_vela(ticker_yf, interval)
    if ultima_ts_str:
        ultima_ts = pd.to_datetime(ultima_ts_str, utc=True)
        dias_sin_datos = (datetime.now(timezone.utc) - ultima_ts).total_seconds() / 86400
        velas_en_db = len(db.obtener_velas(ticker_yf, interval, '8d'))
        period = '7d' if (dias_sin_datos > 6 or velas_en_db < min_candles) else '1d'
    else:
        period = '7d'  # Primera vez: fill completo

    for _ in range(len(_td_keys)):
        alias, key = _next_td_key()
        if not key:
            break
        df, ok = _get_twelve_data(ticker_yf, period, interval, key)
        if ok and not df.empty:
            _registrar_uso_key(alias)
            rows = []
            for ts, row in df.iterrows():
                ts_str = ts.isoformat() if hasattr(ts, 'isoformat') else str(ts)
                rows.append((
                    ts_str,
                    float(row['Open']), float(row['High']),
                    float(row['Low']),  float(row['Close']),
                    float(row.get('Volume', 0))
                ))
            db.guardar_velas(ticker_yf, interval, rows)
            db.purgar_velas_antiguas(ticker_yf, interval, dias_max=8)
            print(f"  💾 [poller] {ticker_yf} {interval} ({alias}) — {len(df)} velas → BD")
            return True
        print(f"  ⚠️ [poller] {alias} falló para {ticker_yf} {interval}")

    return False


def get_ohlcv(ticker_yf: str, period: str, interval: str) -> tuple:
    """
    Descarga datos OHLCV para el ticker y periodo indicados.

    Orden de prioridad:
      1. Twelve Data (Round-Robin entre key1/key2/key3, tiempo real)
      2. Polygon.io  (de pago, tiempo real) si POLYGON_API_KEY configurada
      3. yfinance    (delay 15 min en intraday)

    Retorna:
        (DataFrame, is_delayed: bool)
        - is_delayed=False → datos en tiempo real
        - is_delayed=True  → datos con delay de yfinance

    El DataFrame siempre tiene columnas: Open, High, Low, Close, Volume
    """
    # 1️⃣ Cache en memoria (65s TTL) — la más rápida
    if interval in _INTRADAY_INTERVALS:
        _ck = (ticker_yf, period, interval)
        with _intraday_cache_lock:
            _e = _intraday_cache.get(_ck)
            if _e and (datetime.now(timezone.utc) - _e['ts']) < _INTRADAY_TTL:
                print(f"  💾 [data_provider] Cache mem hit — {ticker_yf} {interval} ({len(_e['df'])} velas)")
                return _e['df'].copy(), _e['delayed']

    # 2️⃣ Base de datos local (persistente, TTL por intervalo)
    df_db, delayed_db = _get_from_db(ticker_yf, period, interval)
    if df_db is not None and not df_db.empty:
        if interval in _INTRADAY_INTERVALS:
            with _intraday_cache_lock:
                _intraday_cache[(ticker_yf, period, interval)] = {
                    'df': df_db.copy(), 'ts': datetime.now(timezone.utc), 'delayed': delayed_db
                }
        return df_db, delayed_db

    # 3️⃣ Twelve Data (tiempo real)
    if interval in _INTRADAY_INTERVALS and _td_keys and ticker_yf in _TICKER_MAP_TWELVE:
        for _ in range(len(_td_keys)):
            alias, key = _next_td_key()
            if not key:
                break
            df, ok = _get_twelve_data(ticker_yf, period, interval, key)
            if ok and not df.empty and len(df) >= 10:
                _registrar_uso_key(alias)
                print(f"  ✅ [data_provider] Twelve Data ({alias}) — {ticker_yf} {interval} ({len(df)} velas, tiempo real)")
                _guardar_en_db(ticker_yf, interval, df)
                with _intraday_cache_lock:
                    _intraday_cache[(ticker_yf, period, interval)] = {'df': df.copy(), 'ts': datetime.now(timezone.utc), 'delayed': False}
                return df, False
            print(f"  ⚠️ [data_provider] Twelve Data {alias} falló — rotando a siguiente key")

    # 4️⃣ Polygon.io (de pago)
    if interval in _INTRADAY_INTERVALS and POLYGON_API_KEY and ticker_yf in _TICKER_MAP_POLYGON:
        df, ok = _get_polygon(ticker_yf, period, interval)
        if ok and not df.empty and len(df) >= 10:
            print(f"  ✅ [data_provider] Polygon.io — {ticker_yf} {interval} ({len(df)} velas, tiempo real)")
            _guardar_en_db(ticker_yf, interval, df)
            with _intraday_cache_lock:
                _intraday_cache[(ticker_yf, period, interval)] = {'df': df.copy(), 'ts': datetime.now(timezone.utc), 'delayed': False}
            return df, False
        print(f"  ⚠️ [data_provider] Polygon.io falló — usando yfinance (delay 15m)")

    # 5️⃣ Fallback: yfinance
    if interval == '1d':
        cache_key = (ticker_yf, period)
        with _daily_cache_lock:
            cached = _daily_cache.get(cache_key)
            if cached and (datetime.now(timezone.utc) - cached['ts']) < _DAILY_TTL:
                print(f"  💾 [data_provider] Cache 1d hit — {ticker_yf} ({len(cached['df'])} velas)")
                return cached['df'].copy(), True

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

        _guardar_en_db(ticker_yf, interval, df)
        if interval == '1d':
            with _daily_cache_lock:
                _daily_cache[(ticker_yf, period)] = {'df': df.copy(), 'ts': datetime.now(timezone.utc)}
        elif interval in _INTRADAY_INTERVALS:
            with _intraday_cache_lock:
                _intraday_cache[(ticker_yf, period, interval)] = {'df': df.copy(), 'ts': datetime.now(timezone.utc), 'delayed': True}

    return df, True


def _get_twelve_data(ticker_yf: str, period: str, interval: str, api_key: str) -> tuple:
    """
    Descarga datos desde Twelve Data API (plan gratuito: 800 req/día por clave).
    Retorna: (DataFrame, success: bool)

    Documentación: https://twelvedata.com/docs#time-series
    """
    try:
        ticker_td = _TICKER_MAP_TWELVE[ticker_yf]

        # Twelve Data usa outputsize (número de velas), no period
        dias_map     = {'1d': 1, '2d': 2, '5d': 5, '7d': 7, '1mo': 30, '60d': 60, '3mo': 90}
        velas_por_dia = {
            '1m': 960, '5m': 192, '15m': 64, '30m': 32, '1h': 16, '4h': 6,
        }
        dias      = dias_map.get(period, 5)
        por_dia   = velas_por_dia.get(interval, 64)
        outputsize = min(dias * por_dia, 5000)

        # Twelve Data usa "1min", "5min", "15min", "1h", "4h"
        interval_td_map = {
            '1m': '1min', '5m': '5min', '15m': '15min', '30m': '30min', '1h': '1h', '4h': '4h',
        }
        interval_td = interval_td_map.get(interval, '15min')

        url = (
            f"https://api.twelvedata.com/time_series"
            f"?symbol={ticker_td}"
            f"&interval={interval_td}"
            f"&outputsize={outputsize}"
            f"&timezone=UTC"
            f"&apikey={api_key}"
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
        df.columns = df.columns.str.lower()  # normalizar case antes de rename
        df = df.rename(columns={
            'open': 'Open', 'high': 'High', 'low': 'Low',
            'close': 'Close', 'volume': 'Volume'
        })
        cols = [c for c in ['Open', 'High', 'Low', 'Close', 'Volume'] if c in df.columns]
        df = df[cols].astype(float)

        # Twelve Data para XAU/USD puede no incluir Volume — usar 1.0 como neutro
        if 'Volume' not in df.columns:
            df['Volume'] = 1.0

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
