"""
data_provider.py — Fuente de datos OHLCV con TwelveData

🔥 MODO DIRECT FETCH — Plan Grow 55 (peticiones ilimitadas)
    Con plan de pago, consultamos TwelveData directamente sin cache.
    Datos siempre frescos, sin preocupación por límites API.

Fuentes de datos:
  1. Twelve Data (Grow 55: ∞ req/día, 55/min) → directo, tiempo real
  2. Polygon.io (opcional, de pago) → backup en modo legacy

Uso:
    from adapters.data_provider import get_ohlcv
    df, is_delayed = get_ohlcv('GC=F', period='5d', interval='15m')

Plan actual: Grow 55 (32€/mes) — Peticiones ILIMITADAS, solo límite 55 req/min
    https://twelvedata.com/pricing

Configuración .env:
    TWELVE_DATA_API_KEY=tu_key_grow_55
    DIRECT_FETCH_MODE=true  ← activa consulta directa (sin cache/BD)
"""
import os
import itertools
import logging
import threading
import pandas as pd
import requests
from collections import deque
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

logger = logging.getLogger('bottrading')

load_dotenv(override=True)

# ── MODO DIRECT FETCH (plan Grow 55 ilimitado) ────────────────────────────────────────────────
# Con peticiones ilimitadas, consultamos directamente TD sin cache para datos frescos
DIRECT_FETCH_MODE = os.getenv('DIRECT_FETCH_MODE', 'true').lower() == 'true'

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

# Mapa de tickers → símbolo Twelve Data
_TICKER_MAP_TWELVE = {
    'GC=F':  'XAU/USD',   # Gold Spot
    'SI=F':  'XAG/USD',   # Silver Spot
}

# Mapa de tickers → símbolo Polygon.io Forex/Metals
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
    '1m':  timedelta(minutes=3),   # poller refresca cada 60s → máx 1 min stale en producción
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

# Cooldown reactivo: cuando la API devuelve error de límite/minuto, bloquear 65s
_key_cooldown: dict = {}       # alias → timestamp hasta el que está bloqueada
_cooldown_lock = threading.Lock()

# Contador proactivo por minuto: PREVIENE el error antes de que ocurra
# Usa ventana DESLIZANTE (deque de timestamps) en lugar de ventana fija.
# TwelveData aplica ventana deslizante de 60s → la ventana fija causaba picos
# de hasta límite*2 req en el cruce de minuto (p.ej. 50+9=59 con límite 55).
_key_minute_window: dict = {}  # alias → deque de timestamps float
_minute_lock = threading.Lock()

# Límites por plan (con margen de seguridad)
# key1  → Plan Grow 55  : 55 req/min ilimitado/día  → usamos 50 req/min
# key2+ → Plan Basic 8  :  8 req/min,  800 req/día  → usamos  7 req/min, 750 req/día
_MAX_RPM_KEY1  = 50    # Grow 55: límite real 55 req/min
_MAX_RPM_FREE  =  7    # Basic 8: límite real  8 req/min
_MAX_DAILY_FREE = 790  # Basic 8: límite real 800 req/día → margen 10 para no rozar el techo

def _get_max_rpm(alias: str) -> int:
    """Devuelve el límite de req/min según el plan de la key."""
    return _MAX_RPM_KEY1 if alias == 'key1' else _MAX_RPM_FREE


def _set_key_cooldown(alias: str, seconds: int = 65, reason: str = 'error API'):
    """Bloquea una key N segundos (reactivo: cuando la API ya devolvió el error)."""
    import time as _time
    with _cooldown_lock:
        _key_cooldown[alias] = _time.time() + seconds
    print(f"  🕐 [quota] {alias}: cooldown {seconds}s ({reason})")


def _is_on_cooldown(alias: str) -> bool:
    """True si la key está en cooldown reactivo."""
    import time as _time
    with _cooldown_lock:
        return _time.time() < _key_cooldown.get(alias, 0)


def _reserve_minute_slot(alias: str) -> bool:
    """
    Proactivo: intenta reservar un slot de uso para el minuto actual.
    Usa ventana DESLIZANTE de 60s para reflejar fielmente cómo TwelveData
    cuenta las peticiones (evita el efecto de cruce de minuto con ventana fija).
    - Atómico: check + append bajo lock → imposible que dos threads
      reserven el mismo slot simultáneamente.
    - Retorna True si se reservó (puede hacer la llamada).
    - Retorna False si el cupo está lleno → rotar a otra key.
    """
    import time as _time
    now = _time.time()
    window_start = now - 60.0
    with _minute_lock:
        if alias not in _key_minute_window:
            _key_minute_window[alias] = deque()
        window = _key_minute_window[alias]
        # Eliminar timestamps que ya salieron de la ventana de 60s
        while window and window[0] <= window_start:
            window.popleft()
        max_rpm = _get_max_rpm(alias)
        if len(window) >= max_rpm:
            # Cupo lleno — cooldown hasta que el timestamp más antiguo expire
            secs_left = int(window[0] - window_start) + 2
            _set_key_cooldown(alias, max(secs_left, 5), reason='cupo/min lleno')
            return False
        window.append(now)
        return True


def _cargar_uso_desde_bd() -> dict:
    """Lee uso de keys de hoy desde BD. Retorna dict alias→count o {} si falla."""
    try:
        from adapters.database import DatabaseManager
        return DatabaseManager().obtener_uso_keys_hoy()
    except Exception:
        return {}


def _is_daily_limit_exceeded(alias: str) -> bool:
    """
    True si una key FREE (key2-key11) ha alcanzado el límite diario de 800 req/día.
    key1 (Grow 55) tiene peticiones ilimitadas → siempre False.
    Usa el cache de uso con TTL de 60s para evitar una query a BD por petición.
    """
    if alias == 'key1':
        return False
    import time as _time
    global _uso_cache, _uso_cache_ts
    with _uso_cache_lock:
        if _time.time() - _uso_cache_ts > _USO_CACHE_TTL:
            _uso_cache = _cargar_uso_desde_bd()
            _uso_cache_ts = _time.time()
        return _uso_cache.get(alias, 0) >= _MAX_DAILY_FREE


def _next_td_key() -> tuple:
    """
    Devuelve (alias, key) con prioridad:
    1. key1 (Plan Grow 55: ∞ req/día, 55 req/min) - SIEMPRE PRIMERO
    2. keys 2-11 (Plan Basic 8: 8 req/min, 800 req/día) - FALLBACK en round-robin
    3. Todas bloqueadas → (None, None): el caller debe abortar, NO forzar key1

    Estrategia:
    - Si key1 disponible (no en cooldown) → usar key1
    - Si key1 en cooldown → rotar entre key2-key11 disponibles y sin cuota diaria agotada
    - Si todas bloqueadas → devolver (None, None) para que el loop se detenga
    """
    if not _td_keys:
        return None, None

    # 1️⃣ PRIORIDAD: Intentar key1 primero (Plan Grow 55 ilimitado)
    for alias, key in _td_keys:
        if alias == 'key1' and not _is_on_cooldown(alias):
            return alias, key

    # 2️⃣ FALLBACK: key1 en cooldown → usar keys FREE (2-11) en round-robin
    with _td_cycle_lock:
        for _ in range(len(_td_keys)):
            alias, key = next(_td_cycle)
            # Saltar key1 en el ciclo (ya la intentamos arriba)
            if alias == 'key1':
                continue
            if _is_on_cooldown(alias):
                continue
            if _is_daily_limit_exceeded(alias):
                print(f"  🚫 [quota] {alias}: límite diario alcanzado ({_MAX_DAILY_FREE} req) — saltando")
                continue
            return alias, key

    # 3️⃣ TODAS BLOQUEADAS — no devolver key1 (evita bucle infinito)
    #    El caller tiene "if not key: break" que detendrá el loop.
    return None, None


def _registrar_uso_key(alias: str, exito: bool = True):
    """Registra cada intento de uso en BD (best-effort, no bloquea la petición)."""
    try:
        from adapters.database import DatabaseManager
        total = DatabaseManager().incrementar_uso_key(alias, exito=exito)
        if total % 100 == 0:
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
            # Ignorar velas con precios inválidos (Low=0, etc.) para no contaminar la BD
            if float(row['Low']) <= 0 or float(row['High']) <= 0 or float(row['Close']) <= 0:
                continue
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


def _get_from_db(ticker_yf: str, period: str, interval: str, force: bool = False):
    """
    Intenta servir datos OHLCV desde la BD local.
    - Para 5m, 15m, 1h: lee velas 5m almacenadas y resamplea si es necesario.
    - Para 4h, 1d: lee directamente el intervalo almacenado.
    - force=True: omite el umbral de frescura (fallback de emergencia cuando todas las keys fallan).
    Retorna (DataFrame, is_delayed) o (None, None) si los datos no existen o son insuficientes.
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
    # force=True omite este check (modo emergencia: todas las keys agotadas)
    if not force:
        ultima_ts = pd.to_datetime(rows[-1]['ts'], format='ISO8601', utc=True)
        umbral = _DB_STALE.get(db_interval, timedelta(minutes=10))
        if (datetime.now(timezone.utc) - ultima_ts) > umbral:
            return None, None

    # Construir DataFrame
    df = pd.DataFrame(rows)
    df['ts'] = pd.to_datetime(df['ts'], format='ISO8601', utc=True)
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

    # Decidir periodo: fill inicial según intervalo o incremental 1d
    # Umbrales mínimos de velas para considerar el historial suficiente
    _MIN_CANDLES = {'5m': 800, '15m': 150, '1h': 100, '4h': 450, '1d': 200}
    _FILL_PERIOD = {'5m': '7d', '15m': '7d', '1h': '30d', '4h': '3mo', '1d': '3mo'}
    min_candles = _MIN_CANDLES.get(interval, 100)
    fill_period = _FILL_PERIOD.get(interval, '7d')

    ultima_ts_str = db.obtener_ultima_ts_vela(ticker_yf, interval)
    if ultima_ts_str:
        ultima_ts = pd.to_datetime(ultima_ts_str, format='ISO8601', utc=True)
        dias_sin_datos = (datetime.now(timezone.utc) - ultima_ts).total_seconds() / 86400
        velas_en_db = len(db.obtener_velas(ticker_yf, interval, '8d'))
        period = fill_period if (dias_sin_datos > 6 or velas_en_db < min_candles) else '1d'
    else:
        period = fill_period  # Primera vez: fill completo según intervalo

    _keys_cooldown = 0
    _keys_api_fail = 0
    _keys_bloqueadas = 0

    for _ in range(len(_td_keys)):
        alias, key = _next_td_key()
        if not key:
            _keys_bloqueadas = len(_td_keys) - _keys_cooldown - _keys_api_fail
            break
        # Proactivo: reservar slot de minuto antes de llamar a la API
        if not _reserve_minute_slot(alias):
            _keys_cooldown += 1
            continue
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
            db.purgar_velas_corruptas(ticker_yf, interval)
            logger.info(f"  [poller] {ticker_yf} {interval} ({alias}) — {len(df)} velas -> BD")
            return True
        _keys_api_fail += 1
        # Garantía: forzar cooldown mínimo si _get_twelve_data no lo puso
        if not _is_on_cooldown(alias):
            _set_key_cooldown(alias, 10, reason='fallo poller sin cooldown previo')
        logger.warning(f"  [poller] {alias} error API para {ticker_yf} {interval}")

    # Diagnosticar motivo del fallo
    if _keys_bloqueadas > 0 and _keys_cooldown == 0 and _keys_api_fail == 0:
        logger.warning(f"  [poller] {ticker_yf} {interval} — todas las keys ya bloqueadas al inicio del ciclo, reintentando en 60s")
    elif _keys_cooldown > 0 and _keys_api_fail == 0:
        logger.warning(f"  [poller] {ticker_yf} {interval} — {_keys_cooldown} keys en cooldown/minuto, reintentando en 60s")
    elif _keys_api_fail > 0:
        logger.warning(f"  [poller] {ticker_yf} {interval} — {_keys_api_fail} keys con error API, {_keys_cooldown} en cooldown")
    return False


def get_ohlcv(ticker_yf: str, period: str, interval: str) -> tuple:
    """
    Descarga datos OHLCV para el ticker y periodo indicados.

    Orden de prioridad:
      1. Twelve Data (Round-Robin entre keys, tiempo real)
      2. Polygon.io  (de pago, tiempo real) si POLYGON_API_KEY configurada

    Retorna:
        (DataFrame, is_delayed: bool)
        - is_delayed siempre es False con TwelveData (datos en tiempo real)
        - Si todas las fuentes fallan, retorna (DataFrame vacío, False)

    El DataFrame siempre tiene columnas: Open, High, Low, Close, Volume
    """
    # Mínimo de velas aceptables por intervalo
    _MIN_VELAS = {'5m': 100, '15m': 50, '1h': 40, '4h': 20, '1d': 30, '1wk': 20}

    # ══════════════════════════════════════════════════════════════════════════════
    # MODO DIRECT FETCH: Consulta directa a TwelveData (plan ilimitado)
    # ══════════════════════════════════════════════════════════════════════════════
    if DIRECT_FETCH_MODE and interval in _INTRADAY_INTERVALS and _td_keys and ticker_yf in _TICKER_MAP_TWELVE:
        # ── OPCIÓN A (poller 1m en BD) — ACTIVA ──────────────────────────────
        # El poller refresca 1m cada 60s → BD siempre fresca, sin consumir quota
        # en cada llamada del detector 5m.
        # Para volver a TD directo: comentar el bloque y descomentar OPCIÓN B.
        if interval == '1m':
            df_db, _ = _get_from_db(ticker_yf, period, interval)
            if df_db is not None and not df_db.empty:
                return df_db, True
            return pd.DataFrame(), False
        # ── fin OPCIÓN A ───────────────────────────────────────────────────────

        # ── OPCIÓN B (TD directo para 1m) — INACTIVA ──────────────────────────
        # Plan Grow 55 soporta XAU/USD 1min (verificado 2026-05-03).
        # Para activar: comentar el bloque OPCIÓN A y comentar el target 1m
        # en POLL_TARGETS de ohlcv_poller.py.
        # ── fin OPCIÓN B ───────────────────────────────────────────────────────

        for _ in range(len(_td_keys)):
            alias, key = _next_td_key()
            if not key:
                break
            # Proactivo: reservar slot de minuto antes de llamar a la API
            if not _reserve_minute_slot(alias):
                print(f"  🚦 [quota] {alias}: cupo minuto lleno — rotando")
                continue
            df, ok = _get_twelve_data(ticker_yf, period, interval, key, alias=alias)
            if ok and not df.empty and len(df) >= 10:
                _registrar_uso_key(alias)
                print(f"  🔥 [DIRECT] Twelve Data ({alias}) — {ticker_yf} {interval} ({len(df)} velas, tiempo real)")
                return df, False
            # Garantía: si _get_twelve_data no puso cooldown, forzar cooldown mínimo
            # para que _next_td_key() rote a la siguiente key en la próxima iteración
            if not _is_on_cooldown(alias):
                _set_key_cooldown(alias, 10, reason='fallo sin cooldown previo')
            print(f"  ⚠️ [DIRECT] Twelve Data {alias} falló — rotando a siguiente key")

        # Si todas las keys de TD fallaron, intentar BD como último recurso
        # force=True: aceptar datos aunque excedan el umbral de frescura normal
        logger.warning(f"  ❌ [DIRECT] Todas las keys TD fallaron para {ticker_yf} {interval} — intentando BD (modo emergencia)")
        df_db, delayed_db = _get_from_db(ticker_yf, period, interval, force=True)
        if df_db is not None and not df_db.empty:
            ultima = df_db.index[-1]
            antiguedad = datetime.now(timezone.utc) - ultima
            logger.warning(f"  ♻️ [DIRECT] Fallback BD (emergencia) — {ticker_yf} {interval} ({len(df_db)} velas, última: {str(ultima)[:16]} UTC, antigüedad: {str(antiguedad).split('.')[0]})")
            return df_db, True  # is_delayed=True: datos de BD, no tiempo real
        logger.warning(f"  ❌ [DIRECT] BD también vacía para {ticker_yf} {interval}")
        return pd.DataFrame(), False

    # ══════════════════════════════════════════════════════════════════════════════
    # MODO LEGACY: Sistema de 3 capas (cache → BD → API)
    # ══════════════════════════════════════════════════════════════════════════════
    if not DIRECT_FETCH_MODE:
        # 1️⃣ Cache en memoria (TTL proporcional al intervalo)
        if interval in _INTRADAY_INTERVALS:
            _ck = (ticker_yf, period, interval)
            _ttl = _CACHE_TTL_MAP.get(interval, _INTRADAY_TTL)
            with _intraday_cache_lock:
                _e = _intraday_cache.get(_ck)
                if _e and (datetime.now(timezone.utc) - _e['ts']) < _ttl:
                    _min_v = _MIN_VELAS.get(interval, 10)
                    if len(_e['df']) >= _min_v:
                        print(f"  💾 [cache] Cache mem hit — {ticker_yf} {interval} ({len(_e['df'])} velas)")
                        return _e['df'].copy(), _e['delayed']
                    else:
                        print(f"  ⚠️ [cache] Cache insuficiente ({len(_e['df'])} velas) — refrescando")
                        _intraday_cache.pop(_ck, None)

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
                # Proactivo: reservar slot de minuto antes de llamar a la API
                if not _reserve_minute_slot(alias):
                    print(f"  🚦 [quota] {alias}: cupo minuto lleno — rotando")
                    continue
                df, ok = _get_twelve_data(ticker_yf, period, interval, key, alias=alias)
                if ok and not df.empty and len(df) >= 10:
                    _registrar_uso_key(alias)
                    print(f"  ✅ [legacy] Twelve Data ({alias}) — {ticker_yf} {interval} ({len(df)} velas, tiempo real)")
                    _guardar_en_db(ticker_yf, interval, df)
                    with _intraday_cache_lock:
                        _intraday_cache[(ticker_yf, period, interval)] = {'df': df.copy(), 'ts': datetime.now(timezone.utc), 'delayed': False}
                    return df, False
                print(f"  ⚠️ [legacy] Twelve Data {alias} falló — rotando a siguiente key")

        # 4️⃣ Polygon.io (de pago) — solo en modo legacy
        if interval in _INTRADAY_INTERVALS and POLYGON_API_KEY and ticker_yf in _TICKER_MAP_POLYGON:
            df, ok = _get_polygon(ticker_yf, period, interval)
            if ok and not df.empty and len(df) >= 10:
                print(f"  ✅ [legacy] Polygon.io — {ticker_yf} {interval} ({len(df)} velas, tiempo real)")
                _guardar_en_db(ticker_yf, interval, df)
                with _intraday_cache_lock:
                    _intraday_cache[(ticker_yf, period, interval)] = {'df': df.copy(), 'ts': datetime.now(timezone.utc), 'delayed': False}
                return df, False
            print(f"  ⚠️ [legacy] Polygon.io falló")

    # ═══════════════════════════════════════════════════════════════════════════
    # Sin más fuentes disponibles — devolver DataFrame vacío
    # ═══════════════════════════════════════════════════════════════════════════
    print(f"  ❌ [data_provider] Todas las fuentes fallaron para {ticker_yf} {interval} {period}")
    return pd.DataFrame(), False


def _get_twelve_data(ticker_yf: str, period: str, interval: str, api_key: str,
                     alias: str = '') -> tuple:
    """
    Descarga datos desde Twelve Data API (plan Grow 55: ∞ req/día, 55 req/min).
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
            if alias:
                _registrar_uso_key(alias, exito=False)
                _set_key_cooldown(alias, 30, reason=f'HTTP {r.status_code}')
            return pd.DataFrame(), False

        data = r.json()

        # Gestionar errores de la API (límite diario, clave inválida, etc.)
        if data.get('status') == 'error' or 'values' not in data:
            msg = data.get('message', data.get('status', 'unknown error'))
            print(f"  ⚠️ Twelve Data error: {msg}")
            if alias:
                _registrar_uso_key(alias, exito=False)
                msg_lower = msg.lower()
                if 'current minute' in msg_lower:
                    _set_key_cooldown(alias, 65, reason='límite/minuto')
                elif 'current day' in msg_lower or 'daily' in msg_lower:
                    # Cooldown hasta la próxima medianoche UTC (reset del contador de TwelveData)
                    _now = datetime.now(timezone.utc)
                    _midnight = (_now + timedelta(days=1)).replace(
                        hour=0, minute=2, second=0, microsecond=0)
                    _secs_hasta_midnight = int((_midnight - _now).total_seconds())
                    _set_key_cooldown(alias, _secs_hasta_midnight, reason='límite diario → hasta medianoche UTC')
                    # Marcar en caché interno para que _is_daily_limit_exceeded no reintente
                    with _uso_cache_lock:
                        _uso_cache[alias] = _MAX_DAILY_FREE
                    logger.warning(f"  🚫 [quota] {alias}: límite diario TwelveData agotado — cooldown {_secs_hasta_midnight}s (hasta medianoche UTC)")
                else:
                    _set_key_cooldown(alias, 30, reason=f'error API: {msg[:50]}')
            return pd.DataFrame(), False

        values = data['values']
        if not values:
            # Respuesta válida pero sin velas (holiday, plan sin acceso, etc.)
            # Poner cooldown corto para forzar rotación y no ciclar en key1
            if alias:
                _registrar_uso_key(alias, exito=False)
                _set_key_cooldown(alias, 15, reason='values vacío')
            return pd.DataFrame(), False

        df = pd.DataFrame(values)
        df['datetime'] = pd.to_datetime(df['datetime'], format='ISO8601', utc=True)
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
        if alias:
            _registrar_uso_key(alias, exito=False)
            _set_key_cooldown(alias, 30, reason=f'excepción: {str(e)[:50]}')
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



