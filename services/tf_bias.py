"""
tf_bias.py — Sesgo compartido multi-TF (Opción C Cascada)

Módulo thread-safe para compartir el sesgo de cada timeframe entre detectores.
Los detectores de TF superior publican su sesgo; los inferiores lo consultan
antes de enviar una señal.

Uso:
    import tf_bias
    # En detector 1D (al final de analizar):
    tf_bias.publicar_sesgo('SPX500', '1D', tf_bias.BIAS_BEARISH, score=11)
    # En detector 4H (antes de enviar señal):
    ok, desc = tf_bias.verificar_confluencia('SPX500', '4H', tf_bias.BIAS_BEARISH)
    if ok:
        enviar_telegram(msg + f"\n{desc}")
"""

import threading
from datetime import datetime, timedelta

# Import lazy para evitar circular import (adapters depende de services)
def _get_db():
    try:
        from adapters.database import DatabaseManager
        return DatabaseManager()
    except Exception:
        return None

# ─────────────────────────────────────
TTL_SESGO_HORAS    = 2    # sesgos más viejos que esto se tratan como sin_datos
TTL_CANAL_1H_HORAS = 4    # canal 1H persiste 4h para detectar el retest posterior
TTL_CANAL_4H_HORAS = 8    # canal 4H: retest puede tardar varias velas 4H
TTL_CANAL_1D_HORAS = 72   # canal 1D: el retest puede tardar días
TTL_CANAL_1W_HORAS = 336  # canal 1W: 2 semanas (14 días)
_lock           = threading.Lock()
_bias_store     = {}   # {simbolo: {tf: {bias, score, ts}}}
_canal_store    = {}   # canal 4H en memoria
_canal_1h_store = {}   # canal 1H en memoria
_canal_1d_store = {}   # canal 1D en memoria
_canal_1w_store = {}   # canal 1W en memoria
# ─────────────────────────────────────

BIAS_BULLISH = 'BULLISH'
BIAS_BEARISH = 'BEARISH'
BIAS_NEUTRAL = 'NEUTRAL'

# Orden jerárquico de mayor (más lento) a menor (más rápido)
TF_JERARQUIA = ['1W', '1D', '4H', '1H', '30M', '15M', '5M']


# ─────────────────────────────────────
def publicar_sesgo(simbolo: str, tf: str, bias: str, score: int) -> None:
    """Publica (actualiza) el sesgo de un TF para un símbolo. Thread-safe."""
    with _lock:
        if simbolo not in _bias_store:
            _bias_store[simbolo] = {}
        _bias_store[simbolo][tf] = {
            'bias':  bias,
            'score': score,
            'ts':    datetime.now(),
        }


def obtener_sesgo(simbolo: str, tf: str):
    """Retorna dict {bias, score, ts} o None si no hay datos."""
    with _lock:
        return _bias_store.get(simbolo, {}).get(tf)


def verificar_confluencia(simbolo: str, tf_actual: str, direccion: str) -> tuple:
    """
    Verifica si los TFs superiores confirman la dirección propuesta.

    Reglas:
    - Si 1D es contrario a la dirección → bloquear siempre (regla dura)
    - Si mayoría de TFs con datos son contrarios → bloquear
    - Si no hay datos de TFs superiores → permitir (sesgo neutro)

    Returns:
        (bool confirmado, str descripcion_para_telegram)
    """
    with _lock:
        sesgo_simbolo = _bias_store.get(simbolo, {})

        try:
            idx = TF_JERARQUIA.index(tf_actual)
        except ValueError:
            return True, f"TF {tf_actual} no en jerarquía — señal permitida"

        tfs_superiores = TF_JERARQUIA[:idx]
        if not tfs_superiores:
            return True, "Sin TFs superiores — señal permitida"

        confirmados = []
        contrarios  = []
        sin_datos   = []

        for tf in tfs_superiores:
            entrada = sesgo_simbolo.get(tf)
            if entrada is None:
                sin_datos.append(tf)
            else:
                # Expirar sesgos más viejos que TTL_SESGO_HORAS
                edad_horas = (datetime.now() - entrada['ts']).total_seconds() / 3600
                if edad_horas > TTL_SESGO_HORAS:
                    sin_datos.append(tf)
                elif entrada['bias'] == direccion:
                    confirmados.append((tf, entrada['score']))
                elif entrada['bias'] != BIAS_NEUTRAL:
                    contrarios.append((tf, entrada['bias'], entrada['score']))

        desc = _build_desc(sesgo_simbolo, tfs_superiores, tf_actual,
                           confirmados, contrarios, sin_datos)

        # Sin datos en TFs superiores → permitir con aviso
        if len(confirmados) == 0 and len(contrarios) == 0:
            return True, f"⏳ TFs superiores sin datos — señal permitida\n{desc}"

        # Mayoría confirma → permitir; empate o mayoría contraria → bloquear
        if len(confirmados) > len(contrarios):
            return True, desc

        return False, f"⚠️ Bloqueada — mayoría de TFs no confirma\n{desc}"


def _build_desc(sesgo_simbolo: dict, tfs_superiores: list, tf_actual: str,
                confirmados: list, contrarios: list, sin_datos: list) -> str:
    """Construye descripción de confluencia para el mensaje de Telegram."""
    total_con_datos = len(confirmados) + len(contrarios)
    n_conf = len(confirmados)
    lineas = [f"🔗 <b>Confluencia Multi-TF</b> ({n_conf}/{total_con_datos} TFs):"]

    for tf in tfs_superiores:
        entrada = sesgo_simbolo.get(tf)
        if entrada is None:
            lineas.append(f"  ⏳ {tf} → sin datos")
        elif entrada['bias'] == BIAS_NEUTRAL:
            lineas.append(f"  ➖ {tf} → NEUTRAL (score {entrada['score']})")
        else:
            icon = "✅" if any(t == tf for t, _ in confirmados) else "❌"
            lineas.append(f"  {icon} {tf} → {entrada['bias']} (score {entrada['score']})")

    lineas.append(f"  🎯 {tf_actual} → este TF")
    return "\n".join(lineas)


def estado_completo() -> dict:
    """Retorna copia del estado completo para diagnóstico."""
    with _lock:
        return {s: dict(tfs) for s, tfs in _bias_store.items()}


def publicar_canal_4h(simbolo: str, alcista_roto: bool, bajista_roto: bool,
                      linea_soporte: float, linea_resist: float) -> None:
    """Publica el estado del canal 4H en memoria y en BD (sobrevive restarts)."""
    _publicar_canal_gen(_canal_store, simbolo, '4H', TTL_CANAL_4H_HORAS,
                        alcista_roto, bajista_roto, linea_soporte, linea_resist)


def obtener_canal_4h(simbolo: str) -> dict:
    """Retorna el estado de canal 4H: memoria primero, BD como fallback ante restart."""
    return _obtener_canal_gen(_canal_store, simbolo, '4H', TTL_CANAL_4H_HORAS)


def publicar_canal_1h(simbolo: str, alcista_roto: bool, bajista_roto: bool,
                      linea_soporte: float, linea_resist: float) -> None:
    """Persiste el canal 1H en memoria y en BD. TTL 4h para cubrir la ventana de retest."""
    _publicar_canal_gen(_canal_1h_store, simbolo, '1H', TTL_CANAL_1H_HORAS,
                        alcista_roto, bajista_roto, linea_soporte, linea_resist)
def obtener_canal_1h(simbolo: str) -> dict:
    """Retorna el canal 1H: memoria primero, BD como fallback ante restart."""
    return _obtener_canal_gen(_canal_1h_store, simbolo, '1H', TTL_CANAL_1H_HORAS)


def _publicar_canal_gen(store: dict, simbolo: str, tf: str, ttl_horas: float,
                        alcista_roto: bool, bajista_roto: bool,
                        linea_soporte: float, linea_resist: float) -> None:
    """Helper interno: guarda en el store en memoria y persiste en BD."""
    with _lock:
        store[simbolo] = {
            'alcista_roto':  alcista_roto,
            'bajista_roto':  bajista_roto,
            'linea_soporte': linea_soporte,
            'linea_resist':  linea_resist,
            'ts':            datetime.now(),
        }
    db = _get_db()
    if db:
        try:
            db.guardar_canal_roto(simbolo, tf, alcista_roto, bajista_roto,
                                   linea_soporte, linea_resist)
        except Exception as e:
            print(f"  ⚠️ tf_bias: no se pudo guardar canal {tf} en BD: {e}")


def _obtener_canal_gen(store: dict, simbolo: str, tf: str, ttl_horas: float) -> dict:
    """Helper interno: lee memoria, BD como fallback ante restart."""
    with _lock:
        datos = store.get(simbolo)
        if datos is not None:
            edad_h = (datetime.now() - datos['ts']).total_seconds() / 3600
            if edad_h <= ttl_horas:
                return dict(datos)
    db = _get_db()
    if db:
        try:
            return db.obtener_canal_roto(simbolo, tf, ttl_horas=ttl_horas)
        except Exception:
            pass
    return None


def publicar_canal_1d(simbolo: str, alcista_roto: bool, bajista_roto: bool,
                      linea_soporte: float, linea_resist: float) -> None:
    """Persiste canal 1D. TTL 72h — el retest puede tardar días."""
    _publicar_canal_gen(_canal_1d_store, simbolo, '1D', TTL_CANAL_1D_HORAS,
                        alcista_roto, bajista_roto, linea_soporte, linea_resist)


def obtener_canal_1d(simbolo: str) -> dict:
    """Canal 1D: memoria primero, BD como fallback."""
    return _obtener_canal_gen(_canal_1d_store, simbolo, '1D', TTL_CANAL_1D_HORAS)


def publicar_canal_1w(simbolo: str, alcista_roto: bool, bajista_roto: bool,
                      linea_soporte: float, linea_resist: float) -> None:
    """Persiste canal 1W. TTL 336h (2 semanas) — retests semanales son lentos."""
    _publicar_canal_gen(_canal_1w_store, simbolo, '1W', TTL_CANAL_1W_HORAS,
                        alcista_roto, bajista_roto, linea_soporte, linea_resist)


def obtener_canal_1w(simbolo: str) -> dict:
    """Canal 1W: memoria primero, BD como fallback."""
    return _obtener_canal_gen(_canal_1w_store, simbolo, '1W', TTL_CANAL_1W_HORAS)
