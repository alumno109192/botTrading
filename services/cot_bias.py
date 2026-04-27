"""
cot_bias.py — Sesgo del COT Report (Commitment of Traders) para Gold

La CFTC publica cada viernes los datos del martes anterior.
Usamos las posiciones de los 'Non-Commercial' (grandes especuladores / hedge funds)
en futuros de Gold (GC) del CME como indicador de sesgo institucional.

Lógica:
    - Si los Non-Commercial están > 65% largos  → sesgo BULLISH (institucionales compran)
    - Si están < 35% largos                     → sesgo BEARISH (institucionales venden)
    - Entre 35-65%                              → NEUTRAL

Efecto en scores Gold:
    COT BULLISH → institucionales compran → reforzar BUY, penalizar SELL
    COT BEARISH → institucionales venden  → reforzar SELL, penalizar BUY
    COT NEUTRAL → sin ajuste

Cache: 6 horas (los datos solo se actualizan semanalmente)

Uso:
    from services.cot_bias import get_cot_bias, ajustar_score_por_cot
    bias, ratio = get_cot_bias()
    score_buy, score_sell = ajustar_score_por_cot(score_buy, score_sell, bias)
"""

import threading
import logging
from datetime import datetime, timezone, timedelta

import requests
import pandas as pd

logger = logging.getLogger(__name__)

# ── Parámetros ────────────────────────────────────────────────────────────────
# URL del archivo COT legacy combinado del CME (Futures + Options)
# Contiene los datos del año en curso + histórico reciente
_COT_URL = "https://www.cftc.gov/dea/options/deacmesf.htm"
# URL alternativa: CSV directo año en curso
_COT_CSV_URL = "https://www.cftc.gov/files/dea/history/deacot{year}.zip"
# Market name del oro en el COT
_GOLD_MARKET = "GOLD - COMMODITY EXCHANGE INC."

_UMBRAL_BULLISH = 65.0   # % largo → institucionales alcistas
_UMBRAL_BEARISH = 35.0   # % largo → institucionales bajistas
_CACHE_TTL_HOURS = 6

# ── Cache ────────────────────────────────────────────────────────────────────
_cache: dict = {'bias': None, 'ratio': None, 'timestamp': None}
_cache_lock = threading.Lock()


def _descargar_cot() -> dict | None:
    """
    Descarga el COT report del año actual desde CFTC y extrae
    la última fila de Gold (GC). Devuelve dict con campos clave o None.
    """
    year = datetime.now(timezone.utc).year
    url = _COT_CSV_URL.format(year=year)

    try:
        import io, zipfile
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()

        with zipfile.ZipFile(io.BytesIO(resp.content)) as z:
            # El zip contiene un único CSV/TXT
            name = z.namelist()[0]
            with z.open(name) as f:
                df = pd.read_csv(f, low_memory=False)

        # Columnas reales del COT CFTC (nombres con espacios)
        COL_MARKET  = 'Market and Exchange Names'
        COL_FECHA   = 'As of Date in Form YYYY-MM-DD'
        COL_LONGS   = 'Noncommercial Positions-Long (All)'
        COL_SHORTS  = 'Noncommercial Positions-Short (All)'

        # Filtrar Gold
        gold = df[df[COL_MARKET].str.upper().str.contains('GOLD - COMMODITY', na=False)]
        if gold.empty:
            logger.warning("  ⚠️ [COT] No se encontró Gold en el reporte")
            return None

        # Ordenar por fecha y coger el más reciente
        gold = gold.copy()
        gold[COL_FECHA] = pd.to_datetime(gold[COL_FECHA], errors='coerce')
        gold = gold.sort_values(COL_FECHA, ascending=False)
        row = gold.iloc[0]

        fecha  = row[COL_FECHA]
        longs  = float(row[COL_LONGS])
        shorts = float(row[COL_SHORTS])
        total  = longs + shorts
        ratio  = (longs / total * 100) if total > 0 else 50.0

        return {
            'fecha': fecha,
            'longs': longs,
            'shorts': shorts,
            'ratio_long_pct': round(ratio, 1),
        }

    except Exception as e:
        logger.warning(f"  ⚠️ [COT] Error descargando reporte: {e}")
        return None


def get_cot_bias() -> tuple:
    """
    Devuelve el sesgo institucional de Gold según el COT report.

    Returns:
        (bias: str, ratio: float)
        bias  → 'BULLISH' | 'BEARISH' | 'NEUTRAL' | None (error)
        ratio → % de posiciones largas de Non-Commercials (0-100)

    Cacheado 6 horas.
    """
    global _cache
    ahora = datetime.now(timezone.utc)

    with _cache_lock:
        if (_cache['bias'] is not None
                and _cache['timestamp'] is not None
                and (ahora - _cache['timestamp']) < timedelta(hours=_CACHE_TTL_HOURS)):
            return _cache['bias'], _cache['ratio']

    datos = _descargar_cot()

    if datos is None:
        return None, None

    ratio = datos['ratio_long_pct']
    if ratio >= _UMBRAL_BULLISH:
        bias = 'BULLISH'
    elif ratio <= _UMBRAL_BEARISH:
        bias = 'BEARISH'
    else:
        bias = 'NEUTRAL'

    logger.info(
        f"  📋 [COT] {datos['fecha'].strftime('%Y-%m-%d')} | "
        f"Longs: {datos['longs']:,.0f} | Shorts: {datos['shorts']:,.0f} | "
        f"Ratio: {ratio:.1f}% largos → {bias}"
    )

    with _cache_lock:
        _cache.update({'bias': bias, 'ratio': ratio, 'timestamp': ahora})

    return bias, ratio


def ajustar_score_por_cot(score_buy: int, score_sell: int,
                           bias: str | None) -> tuple:
    """
    Ajusta los scores según el sesgo institucional del COT.

    COT BULLISH → institucionales compran → +1 BUY, -1 SELL
    COT BEARISH → institucionales venden  → +1 SELL, -1 BUY
    COT NEUTRAL / None → sin cambio

    Efecto moderado (±1 pt) — el COT es semanal, no intraday.

    Returns:
        (score_buy ajustado, score_sell ajustado)
    """
    if bias == 'BULLISH':
        score_buy  = score_buy  + 1
        score_sell = max(0, score_sell - 1)
        logger.info(f"  📋 COT BULLISH → score_buy +1, score_sell -1")
    elif bias == 'BEARISH':
        score_buy  = max(0, score_buy  - 1)
        score_sell = score_sell + 1
        logger.info(f"  📋 COT BEARISH → score_buy -1, score_sell +1")

    return score_buy, score_sell
