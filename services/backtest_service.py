"""
services/backtest_service.py — Servicio de backtest mensual automático

Ejecuta un backtest walk-forward sobre XAUUSD (1H y 4H) al inicio y
luego cada 30 días. Envía los resultados a Telegram y emite una alerta
si la tasa de acierto cae por debajo del WIN_RATE_MINIMO (55 %).
"""

import os
import time
import logging
from datetime import datetime, timedelta

logger = logging.getLogger('bottrading')

# ─────────────────────────────────────────────────────────────────────────────
INTERVALO_BACKTEST = 30 * 24 * 3600   # 30 días en segundos
WIN_RATE_MINIMO    = 55.0             # % — umbral de alerta
SIMBOLO            = 'XAUUSD'
TIMEFRAMES         = ['1h', '4h']
# ─────────────────────────────────────────────────────────────────────────────


def main():
    """Bucle principal del servicio de backtest mensual."""
    logger.info("📊 [Backtest] Servicio iniciado — primera ejecución en 1 hora")
    time.sleep(3600)   # esperar arranque completo del bot

    while True:
        try:
            _ejecutar_y_notificar()
        except Exception as e:
            logger.error(f"❌ [Backtest] Error en ciclo mensual: {e}")

        logger.info("📊 [Backtest] Próximo ciclo en 30 días")
        time.sleep(INTERVALO_BACKTEST)


def _ejecutar_y_notificar():
    """Ejecuta el backtest para cada TF y notifica resultados a Telegram."""
    from scripts.backtest import ejecutar_backtest
    from adapters.telegram import enviar_telegram

    hasta = datetime.utcnow().strftime('%Y-%m-%d')
    desde = (datetime.utcnow() - timedelta(days=30)).strftime('%Y-%m-%d')

    logger.info(f"📊 [Backtest] Ejecutando {SIMBOLO} {TIMEFRAMES} — {desde} → {hasta}")

    resultados = []
    for tf in TIMEFRAMES:
        try:
            r = ejecutar_backtest(SIMBOLO, tf, desde, hasta)
            m = r['metricas']
            resultados.append((tf.upper(), m))
            logger.info(
                f"  ✅ {tf.upper()}: win_rate={m['win_rate']}% | "
                f"PF={m['profit_factor']} | señales={m['total_senales']}"
            )
        except Exception as e:
            logger.warning(f"  ⚠️ [Backtest] {tf} fallido: {e}")

    if not resultados:
        logger.warning("  ⚠️ [Backtest] Sin resultados — posible fallo de datos")
        return

    _enviar_reporte(resultados, desde, hasta, enviar_telegram)


def _enviar_reporte(resultados, desde, hasta, enviar_telegram):
    """Construye y envía el mensaje de reporte de backtest a Telegram."""
    lineas = [
        f"📊 <b>Backtest Mensual — {SIMBOLO}</b>",
        f"━━━━━━━━━━━━━━━━━━━━",
        f"📅 {desde} → {hasta}",
        f"━━━━━━━━━━━━━━━━━━━━",
    ]

    alertas = []
    for tf_label, m in resultados:
        wr   = m['win_rate']
        icono = "✅" if wr >= WIN_RATE_MINIMO else "🚨"
        en_curso = f" (+{m.get('en_curso_count', 0)} en curso)" if m.get('en_curso_count') else ""
        lineas.append(
            f"{icono} <b>{tf_label}:</b> WR={wr}%  PF={m['profit_factor']}  "
            f"Señales={m['total_senales']}{en_curso}"
        )
        if wr < WIN_RATE_MINIMO and m['total_senales'] >= 5:
            alertas.append(tf_label)

    if alertas:
        lineas += [
            f"━━━━━━━━━━━━━━━━━━━━",
            f"🚨 <b>ALERTA:</b> Win rate bajo el {WIN_RATE_MINIMO}% en: {', '.join(alertas)}",
            f"💡 Revisar parámetros o elevar SCORE_MIN en los detectores afectados.",
        ]

    msg = "\n".join(lineas)
    thread_id = int(os.environ.get('THREAD_ID_SWING') or 0) or None
    try:
        enviar_telegram(msg, thread_id)
    except Exception as e:
        logger.error(f"  ⚠️ [Backtest] Error enviando reporte a Telegram: {e}")

    logger.info("📊 [Backtest] Reporte mensual enviado")
