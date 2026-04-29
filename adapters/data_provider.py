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
import pandas as pd
import requests
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

load_dotenv(override=True)

def _build_key_list():
    """Construye lista de (alias, key) con las keys configuradas (key1..key11)."""
    keys = []
    env_names = [
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
    for alias, env in env_names:
        key = os.environ.get(env, '').strip()
        if key:
            keys.append((alias, key))
    return keys
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

# Intervalos soportados por fuentes externas (incluye 4h y 1d para Twelve Data)
_INTRADAY_INTERVALS = {'1m', '5m', '15m', '30m', '1h', '4h', '1d'}

# ── Cache intraday y diario (TTL proporcional al intervalo) ──
# Para intervalos lentos (4h, 1d, 1wk) el precio no cambia en minutos:
# usar TTL largo evita centenares de API calls/día innecesarias.
_intraday_cache: dict = {}
_intraday_cache_lock = threading.Lock()
_INTRADAY_TTL = timedelta(seconds=65)  # legacy — no se usa directamente
_CACHE_TTL_MAP = {
    '1m':  timedelta(seconds=65),
    '5m':  timedelta(seconds=65),
    '15m': timedelta(minutes=5),
    '1h':  timedelta(minutes=20),
    '4h':  timedelta(hours=5),
    '1d':  timedelta(hours=12),
    '1wk': timedelta(hours=24),
}

# ── Tiempo máximo desde la última vela en BD para considerar datos “frescos” ──
_DB_STALE = {
    '1m':  timedelta(minutes=2),
    '5m':  timedelta(minutes=10),
    '15m': timedelta(minutes=30),
    '1h':  timedelta(hours=2),
    '4h':  timedelta(hours=8),
    '1d':  timedelta(hours=48),
}

# ── Selección de API Key por menor uso diario ────────────────────────────────
# En cada petición se consulta la BD para elegir la key con menos calls hoy.
# Si la BD no responde (arranque, error), se usa round-robin como fallback.
# El lock evita que dos threads elijan la misma key simultáneamente.

_td_keys = _build_key_list()
_td_cycle = itertools.cycle(_td_keys) if _td_keys else iter([])
_td_cycle_lock = threading.Lock()

# Cache local de uso (evita una query a BD por cada petición; se refresca cada 60s)
_uso_cache: dict = {}          # alias → peticiones hoy
_uso_cache_ts: float = 0.0     # timestamp de la última carga
_uso_cache_lock = threading.Lock()
_USO_CACHE_TTL = 60            # segundos

# Cooldown por minuto: cuando una key supera el límite/minuto se bloquea 65s
_key_cooldown: dict = {}       # alias → timestamp hasta el que está bloqueada
_cooldown_lock = threading.Lock()


def _set_key_cooldown(alias: str, seconds: int = 65):
    """Bloquea una key N segundos por haber superado el límite por minuto."""
    import time as _time
    with _cooldown_lock:
        _key_cooldown[alias] = _time.time() + seconds
    print(f"  🕐 [quota] {alias}: cooldown {seconds}s por límite/minuto")


def _is_on_cooldown(alias: str) -> bool:
    """True si la key está en cooldown por límite de minuto."""
    import time as _time
    with _cooldown_lock:
        return _time.time() < _key_cooldown.get(alias, 0)


def _cargar_uso_desde_bd() -> dict:
    """Lee uso de keys de hoy desde BD. Retorna dict alias→count o {} si falla."""
    try:
        from adapters.database import DatabaseManager
        return DatabaseManager().obtener_uso_keys_hoy()
    except Exception:
        return {}


def _next_td_key() -> tuple:
    """
    Devuelve (alias, key) eligiendo la key con MENOS peticiones hoy.
    Fuentes de datos (en orden):
      1. Cache local en memoria (TTL 60s) — evita una query BD por cada llamada
      2. BD Turso — fuente de verdad del contador diario
      3. Round-Robin — fallback si BD no está disponible
    """
    if not _td_keys:
        return None, None

    import time as _time
    now = _time.time()

    with _uso_cache_lock:
        if now - _uso_cache_ts > _USO_CACHE_TTL:
            fresh = _cargar_uso_desde_bd()
            if fresh or _uso_cache:          # actualiza si hay datos nuevos
                _uso_cache.clear()
                _uso_cache.update(fresh)
            globals()['_uso_cache_ts'] = now

        if _uso_cache:
            # Descartar keys agotadas (≥800/día) o en cooldown por minuto
            disponibles = [
                ak for ak in _td_keys
                if _uso_cache.get(ak[0], 0) < 800 and not _is_on_cooldown(ak[0])
            ]
            if not disponibles:
                # Todas agotadas o en cooldown — intentar las que solo están en cooldown
                disponibles_sin_diario = [
                    ak for ak in _td_keys if _uso_cache.get(ak[0], 0) < 800
                ]
                if disponibles_sin_diario:
                    # Hay keys con cuota diaria disponible pero en cooldown de minuto
                    # Usar la de menor uso (el cooldown habrá expirado pronto)
                    disponibles = disponibles_sin_diario
                else:
                    print("  ⚠️ [quota] TODAS las API keys han alcanzado el límite de 800/día")
                    disponibles = _td_keys
            alias, key = min(disponibles, key=lambda ak: _uso_cache.get(ak[0], 0))
            return alias, key

    # Fallback: round-robin (BD no disponible)
    with _td_cycle_lock:
        return next(_td_cycle)


def _registrar_uso_key(alias: str):
    """Persiste el uso de la key en BD e invalida el cache local (best-effort)."""
    try:
        from adapters.database import DatabaseManager
        total = DatabaseManager().incrementar_uso_key(alias)
        # Actualizar cache local inmediatamente para que la próxima elección
        # ya refleje este incremento sin esperar al TTL de 60s
        with _uso_cache_lock:
            _uso_cache[alias] = total
            if total >= 800:
                print(f"  🚫 [quota] {alias}: {total}/800 — KEY AGOTADA, descartada hasta mañana")
            elif total % 50 == 0:
                print(f"  📊 [quota] {alias}: {total}/800 peticiones hoy")
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
        df, ok = _get_twelve_data(ticker_yf, period, interval, key, alias=alias)
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
    # 1️⃣ Cache en memoria (TTL proporcional al intervalo) — la más rápida
    if interval in _INTRADAY_INTERVALS:
        _ck = (ticker_yf, period, interval)
        _ttl = _CACHE_TTL_MAP.get(interval, _INTRADAY_TTL)
        with _intraday_cache_lock:
            _e = _intraday_cache.get(_ck)
            if _e and (datetime.now(timezone.utc) - _e['ts']) < _ttl:
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

    # 3️⃣ Twelve Data (tiempo real) — soporta intraday y 1d
    if interval in _INTRADAY_INTERVALS and _td_keys and ticker_yf in _TICKER_MAP_TWELVE:
        for _ in range(len(_td_keys)):
            alias, key = _next_td_key()
            if not key:
                break
            df, ok = _get_twelve_data(ticker_yf, period, interval, key, alias=alias)
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
        print(f"  ⚠️ [data_provider] Polygon.io falló — intentando Twelve Data")

    # 5️⃣ Sin más fuentes disponibles — devolver DataFrame vacío
    print(f"  ❌ [data_provider] No hay fuente disponible para {ticker_yf} {interval} {period}")
    return pd.DataFrame(), True


def _get_twelve_data(ticker_yf: str, period: str, interval: str, api_key: str,
                     alias: str = '') -> tuple:
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
            '1m': 960, '5m': 192, '15m': 64, '30m': 32, '1h': 16, '4h': 6, '1d': 1,
        }
        dias      = dias_map.get(period, 5)
        por_dia   = velas_por_dia.get(interval, 64)
        outputsize = min(dias * por_dia, 5000)

        # Twelve Data intervalos: 1min, 5min, 15min, 1h, 4h, 1day
        interval_td_map = {
            '1m': '1min', '5m': '5min', '15m': '15min', '30m': '30min',
            '1h': '1h', '4h': '4h', '1d': '1day',
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
            # Límite por minuto → cooldown 65s para no seguir rotando en vano
            if alias and 'current minute' in msg.lower():
                _set_key_cooldown(alias, 65)
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
