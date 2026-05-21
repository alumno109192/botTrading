"""Servicios de datos para la sección Value Investing."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import threading
import re

import yfinance as yf

from adapters.yf_lock import _yf_lock

WATCHLIST = {
    "Software Industrial":     {"emoji": "⚙️", "peso": 5, "tickers": ["MSFT", "ADBE", "CRM", "NOW", "ANSS", "PTC", "CDNS"]},
    "Microchips Occidentales": {"emoji": "💡", "peso": 5, "tickers": ["ASML", "NVDA", "LRCX", "AMAT", "KLAC", "TSM"]},
    "Salud Premium":           {"emoji": "🏥", "peso": 4, "tickers": ["ISRG", "EW", "DXCM", "MASI", "STE"]},
    "Industrial Tech":         {"emoji": "🔧", "peso": 4, "tickers": ["ROK", "EMR", "ITW", "AME", "KEYS", "TDY"]},
    "Consultoría Premium":     {"emoji": "💼", "peso": 3, "tickers": ["ACN", "EPAM", "INFU"]},
    "Infraestructura Digital": {"emoji": "🌐", "peso": 3, "tickers": ["MSCI", "ICE", "SPGI", "MCO", "FDS"]},
    "Consumo Premium":         {"emoji": "👜", "peso": 2, "tickers": ["LVMH", "NKE", "EL"]},
    "Chicharros de Calidad":   {"emoji": "🚀", "peso": 4, "tickers": ["CELH", "MELI", "SE", "DUOL"]},
}

COMPANY_NAMES = {
    "MSFT": "Microsoft Corporation",
    "ADBE": "Adobe Inc.",
    "CRM": "Salesforce, Inc.",
    "NOW": "ServiceNow, Inc.",
    "ANSS": "ANSYS, Inc.",
    "PTC": "PTC Inc.",
    "CDNS": "Cadence Design Systems, Inc.",
    "ASML": "ASML Holding N.V.",
    "NVDA": "NVIDIA Corporation",
    "LRCX": "Lam Research Corporation",
    "AMAT": "Applied Materials, Inc.",
    "KLAC": "KLA Corporation",
    "TSM": "Taiwan Semiconductor Manufacturing Company Limited",
    "ISRG": "Intuitive Surgical, Inc.",
    "EW": "Edwards Lifesciences Corporation",
    "DXCM": "DexCom, Inc.",
    "MASI": "Masimo Corporation",
    "STE": "STERIS plc",
    "ROK": "Rockwell Automation, Inc.",
    "EMR": "Emerson Electric Co.",
    "ITW": "Illinois Tool Works Inc.",
    "AME": "AMETEK, Inc.",
    "KEYS": "Keysight Technologies, Inc.",
    "TDY": "Teledyne Technologies Incorporated",
    "ACN": "Accenture plc",
    "EPAM": "EPAM Systems, Inc.",
    "INFU": "InfuSystem Holdings, Inc.",
    "MSCI": "MSCI Inc.",
    "ICE": "Intercontinental Exchange, Inc.",
    "SPGI": "S&P Global Inc.",
    "MCO": "Moody's Corporation",
    "FDS": "FactSet Research Systems Inc.",
    "LVMH": "LVMH Moët Hennessy Louis Vuitton",
    "NKE": "NIKE, Inc.",
    "EL": "The Estée Lauder Companies Inc.",
    "CELH": "Celsius Holdings, Inc.",
    "MELI": "MercadoLibre, Inc.",
    "SE": "Sea Limited",
    "DUOL": "Duolingo, Inc.",
}

TICKER_TO_CAT = {
    ticker: categoria
    for categoria, data in WATCHLIST.items()
    for ticker in data["tickers"]
}

_EMPRESA_TTL = timedelta(hours=1)
_CALENDARIO_TTL = timedelta(minutes=30)
_SORT_FALLBACK_DAYS = 99_999

_cache_empresas: dict[str, dict] = {}
_cache_calendario: dict[str, object] = {"timestamp": None, "data": None}
_cache_lock = threading.Lock()

_DIAS_ES = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]
_MESES_ES = ["Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"]


def _safe_float(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = value.replace('%', '').replace(',', '').strip()
        if not cleaned:
            return None
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None


def _format_earnings_date(date_value) -> str:
    if not date_value:
        return "Sin fecha confirmada"
    date_only = date_value.date() if hasattr(date_value, 'date') else date_value
    return f"{_DIAS_ES[date_only.weekday()]} {date_only.day:02d} {_MESES_ES[date_only.month - 1]}"


def _dias_para(date_value):
    if not date_value:
        return None
    date_only = date_value.date() if hasattr(date_value, 'date') else date_value
    return (date_only - datetime.now().date()).days


def _semana_label(dias_para_earnings):
    if dias_para_earnings is None:
        return "Sin fecha confirmada"
    if dias_para_earnings < 0:
        return "Semana pasada"
    if dias_para_earnings <= 6:
        return "Esta semana"
    if dias_para_earnings <= 13:
        return "Próxima semana"
    semanas = (dias_para_earnings // 7)
    return f"En {semanas} semanas"


def _extraer_fecha_calendar(calendar_data):
    if calendar_data is None:
        return None

    if hasattr(calendar_data, 'loc') and hasattr(calendar_data, 'index'):
        idx = str(calendar_data.index[0]).lower() if len(calendar_data.index) else ''
        if 'earnings date' in idx and not calendar_data.empty:
            val = calendar_data.iloc[0, 0]
            if isinstance(val, (list, tuple)) and val:
                return val[0]
            return val

    if isinstance(calendar_data, dict):
        for key in ("Earnings Date", "earningsDate", "earnings_date"):
            val = calendar_data.get(key)
            if isinstance(val, (list, tuple)) and val:
                return val[0]
            if val:
                return val

    return None


def _normalizar_fecha(fecha_raw):
    if fecha_raw is None:
        return None
    if isinstance(fecha_raw, datetime):
        return fecha_raw.replace(tzinfo=None) if fecha_raw.tzinfo else fecha_raw
    if hasattr(fecha_raw, 'to_pydatetime'):
        dt = fecha_raw.to_pydatetime()
        return dt.replace(tzinfo=None) if dt.tzinfo else dt
    return None


def _get_earnings_dates_df(ticker_obj):
    with _yf_lock:
        df = getattr(ticker_obj, 'earnings_dates', None)
    if df is not None and hasattr(df, 'empty') and not df.empty:
        return df
    with _yf_lock:
        get_fn = getattr(ticker_obj, 'get_earnings_dates', None)
        if callable(get_fn):
            try:
                df = get_fn(limit=12)
                if df is not None and hasattr(df, 'empty') and not df.empty:
                    return df
            except Exception:
                return None
    return None


def _build_historial_earnings(df):
    historico = []
    if df is None or df.empty:
        return historico

    cols = {c.lower(): c for c in df.columns}
    est_col = cols.get('eps estimate')
    real_col = cols.get('reported eps')
    surprise_col = cols.get('surprise(%)')

    if not est_col and not real_col:
        return historico

    rows = df.sort_index(ascending=False)
    for idx, row in rows.iterrows():
        real = _safe_float(row.get(real_col)) if real_col else None
        est = _safe_float(row.get(est_col)) if est_col else None
        if real is None and est is None:
            continue

        sorpresa = _calc_sorpresa_pct(est, real, row.get(surprise_col) if surprise_col else None)

        periodo_dt = _normalizar_fecha(idx)
        periodo = periodo_dt.strftime('%b %Y') if periodo_dt else str(idx)
        beat = bool(real is not None and est is not None and real >= est)

        historico.append({
            'periodo': periodo,
            'eps_estimado': est,
            'eps_real': real,
            'sorpresa_pct': round(sorpresa, 2) if isinstance(sorpresa, float) else None,
            'beat': beat,
        })
        if len(historico) == 4:
            break

    return historico


def _calc_sorpresa_pct(eps_estimado, eps_real, sorpresa_raw=None):
    sorpresa = _safe_float(sorpresa_raw)
    if sorpresa is not None:
        return sorpresa
    if eps_estimado in (None, 0) or eps_real is None:
        return None
    return ((eps_real - eps_estimado) / abs(eps_estimado)) * 100


def get_earnings_info(ticker: str) -> dict:
    ticker = (ticker or '').upper().strip()

    with _cache_lock:
        cache_hit = _cache_empresas.get(ticker)
        if cache_hit and (datetime.now(timezone.utc) - cache_hit['timestamp']) < _EMPRESA_TTL:
            return cache_hit['data']

    categoria = TICKER_TO_CAT.get(ticker)
    cat_data = WATCHLIST.get(categoria, {})

    result = {
        'ticker': ticker,
        'nombre': COMPANY_NAMES.get(ticker, ticker),
        'categoria': categoria,
        'categoria_emoji': cat_data.get('emoji', ''),
        'categoria_peso': cat_data.get('peso', 0),
        'precio_actual': None,
        'variacion_dia_pct': None,
        'market_cap_b': None,
        'earnings_date': None,
        'earnings_date_str': 'Sin fecha confirmada',
        'dias_para_earnings': None,
        'earnings_timing': 'TBD',
        'eps_estimado': None,
        'eps_real': None,
        'eps_sorpresa_pct': None,
        'historico_earnings': [],
        'error': None,
    }

    if not ticker:
        result['error'] = 'Ticker inválido'
        return result

    try:
        with _yf_lock:
            ticker_obj = yf.Ticker(ticker)
            hist = ticker_obj.history(period='5d', interval='1d', auto_adjust=False)
            fast_info = getattr(ticker_obj, 'fast_info', {}) or {}
            info = getattr(ticker_obj, 'info', {}) or {}
            calendar = getattr(ticker_obj, 'calendar', None)

        if hist is not None and not hist.empty:
            close_now = _safe_float(hist['Close'].iloc[-1])
            result['precio_actual'] = round(close_now, 2) if close_now is not None else None
            if len(hist) >= 2:
                prev = _safe_float(hist['Close'].iloc[-2])
                if prev not in (None, 0) and close_now is not None:
                    result['variacion_dia_pct'] = round(((close_now - prev) / prev) * 100, 2)

        if result['precio_actual'] is None:
            fallback_price = _safe_float(fast_info.get('lastPrice')) or _safe_float(info.get('currentPrice'))
            result['precio_actual'] = round(fallback_price, 2) if fallback_price is not None else None

        market_cap = _safe_float(fast_info.get('marketCap')) or _safe_float(info.get('marketCap'))
        if market_cap is not None:
            result['market_cap_b'] = round(market_cap / 1_000_000_000, 2)

        earnings_df = _get_earnings_dates_df(ticker_obj)
        result['historico_earnings'] = _build_historial_earnings(earnings_df)

        fecha_earnings = _normalizar_fecha(_extraer_fecha_calendar(calendar))
        eps_est = None
        eps_real = None
        eps_surprise = None

        if earnings_df is not None and not earnings_df.empty:
            df_sorted = earnings_df.sort_index(ascending=True)
            cols = {c.lower(): c for c in df_sorted.columns}
            est_col = cols.get('eps estimate')
            real_col = cols.get('reported eps')
            surprise_col = cols.get('surprise(%)')

            future_rows = []
            for idx in df_sorted.index:
                normalized = _normalizar_fecha(idx)
                if not normalized:
                    continue
                dias = _dias_para(normalized)
                if dias is not None and dias >= 0:
                    future_rows.append(idx)
            idx_target = future_rows[0] if future_rows else df_sorted.index[-1]
            target_row = df_sorted.loc[idx_target]

            if fecha_earnings is None:
                fecha_earnings = _normalizar_fecha(idx_target)

            eps_est = _safe_float(target_row.get(est_col)) if est_col else None
            eps_real = _safe_float(target_row.get(real_col)) if real_col else None
            eps_surprise = _calc_sorpresa_pct(
                eps_est,
                eps_real,
                target_row.get(surprise_col) if surprise_col else None,
            )

        result['earnings_date'] = fecha_earnings.isoformat() if fecha_earnings else None
        result['earnings_date_str'] = _format_earnings_date(fecha_earnings)
        result['dias_para_earnings'] = _dias_para(fecha_earnings)
        result['earnings_timing'] = 'TBD'
        result['eps_estimado'] = eps_est
        result['eps_real'] = eps_real
        result['eps_sorpresa_pct'] = round(eps_surprise, 2) if isinstance(eps_surprise, float) else None

    except Exception as exc:
        result['error'] = str(exc)

    with _cache_lock:
        _cache_empresas[ticker] = {'timestamp': datetime.now(timezone.utc), 'data': result}

    return result


def _orden_semana(label: str):
    if label == 'Esta semana':
        return (0, 0)
    if label == 'Próxima semana':
        return (1, 0)
    m = re.match(r'^En (\d+) semanas$', label or '')
    if m:
        return (2, int(m.group(1)))
    if label == 'Semana pasada':
        return (3, 0)
    return (4, 0)


def get_calendario_semanal() -> list[dict]:
    with _cache_lock:
        ts = _cache_calendario.get('timestamp')
        data = _cache_calendario.get('data')
        if ts and data and (datetime.now(timezone.utc) - ts) < _CALENDARIO_TTL:
            return data

    empresas = []
    for categoria, cat_data in WATCHLIST.items():
        for ticker in cat_data['tickers']:
            info = get_earnings_info(ticker)
            info = dict(info)
            info['categoria'] = categoria
            info['categoria_emoji'] = cat_data['emoji']
            info['categoria_peso'] = cat_data['peso']
            info['semana_label'] = _semana_label(info.get('dias_para_earnings'))
            empresas.append(info)

    empresas.sort(
        key=lambda x: (
            _orden_semana(x.get('semana_label')),
            x.get('dias_para_earnings') if x.get('dias_para_earnings') is not None else _SORT_FALLBACK_DAYS,
            x.get('ticker', ''),
        )
    )

    with _cache_lock:
        _cache_calendario['timestamp'] = datetime.now(timezone.utc)
        _cache_calendario['data'] = empresas

    return empresas


def invalidar_cache(ticker: str | None = None):
    with _cache_lock:
        if ticker:
            _cache_empresas.pop(ticker.upper().strip(), None)
        else:
            _cache_empresas.clear()
        _cache_calendario['timestamp'] = None
        _cache_calendario['data'] = None
