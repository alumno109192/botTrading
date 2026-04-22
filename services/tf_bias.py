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

# ─────────────────────────────────────
TTL_SESGO_HORAS = 2  # sesgos más viejos que esto se tratan como sin_datos
_lock        = threading.Lock()
_bias_store  = {}   # {simbolo: {tf: {bias, score, ts}}}
_canal_store = {}   # {simbolo: {alcista_roto, bajista_roto, linea_soporte, linea_resist, ts}}
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

        # Regla dura: 1D nunca puede ser contrario
        contra_1d = any(tf == '1D' for tf, _, _ in contrarios)
        if contra_1d:
            return False, f"🚫 Bloqueada — 1D contrario\n{desc}"

        # Sin datos en TFs superiores → permitir con aviso
        if len(confirmados) == 0 and len(contrarios) == 0:
            return True, f"⏳ TFs superiores sin datos — señal permitida\n{desc}"

        # Mayóría estricta confirma → permitir; empate → bloquear
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
    """Publica el estado del canal 4H para que el detector 1H lo consulte."""
    with _lock:
        _canal_store[simbolo] = {
            'alcista_roto':  alcista_roto,
            'bajista_roto':  bajista_roto,
            'linea_soporte': linea_soporte,
            'linea_resist':  linea_resist,
            'ts':            datetime.now(),
        }


def obtener_canal_4h(simbolo: str) -> dict:
    """Retorna el último estado de canal 4H o None si no hay datos recientes."""
    with _lock:
        datos = _canal_store.get(simbolo)
        if datos is None:
            return None
        edad_h = (datetime.now() - datos['ts']).total_seconds() / 3600
        if edad_h > TTL_SESGO_HORAS:
            return None
        return dict(datos)
