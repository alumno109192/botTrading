"""
test_telegram_senales.py — Prueba COMPLETA de todos los mensajes Telegram del bot.

Escenarios basados en el gráfico XAUUSD 1D:
  - Resistencia: $4,741-$4,763 (zona oscura actual)
  - Soporte actual: ~$4,640-$4,600
  - Fibonacci 0.618: $4,495
  - Fibonacci 0.786: $4,388
  - Zona profunda: $4,252

Ejecutar con:
    .venv\\Scripts\\python.exe tests\\test_telegram_senales.py
"""

import sys, os, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from dotenv import load_dotenv
load_dotenv()

from adapters.telegram import enviar_telegram

# Thread ID de intradía (igual que usa el detector)
THREAD_ID = int(os.environ.get('THREAD_ID_INTRADAY') or 0) or None

def send(msg, label=""):
    """Envía mensaje y muestra resultado."""
    print(f"\n{'='*60}")
    print(f"  {label}")
    print(f"{'='*60}")
    ok = enviar_telegram(msg, THREAD_ID)
    if ok:
        print(f"  ✅ Enviado correctamente")
    else:
        print(f"  ❌ FALLO al enviar")
    time.sleep(1.5)   # anti-flood Telegram
    return ok

# ─────────────────────────────────────────────────────────────
# TEST 0 — Conectividad básica
# ─────────────────────────────────────────────────────────────
def test_0_ping():
    msg = (
        f"🧪 <b>TEST BOT — INICIO SUITE</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Verificando todos los tipos de mensajes...\n"
        f"Gráfico analizado: <b>XAUUSD 1D</b>\n"
        f"Precio actual: <b>$4,683</b>\n"
        f"Resistencia: $4,741-$4,763\n"
        f"Soporte: $4,600-$4,640\n"
        f"Fib 0.618: $4,495 | Fib 0.786: $4,388"
    )
    return send(msg, "TEST 0 — Ping / Conectividad")

# ─────────────────────────────────────────────────────────────
# TEST 1 — PREP_SELL: precio tocando resistencia $4,741-$4,763
# ─────────────────────────────────────────────────────────────
def test_1_prep_sell():
    msg = (
        f"⚠️ <b>SETUP SELL — ORO (XAUUSD) 1H</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🔴 Precio en <b>zona de resistencia</b> — esperar confirmación\n"
        f"📍 <b>Zona Resist:</b> $4,741 – $4,763\n"
        f"📌 <b>SELL LIMIT:</b> $4,752.00  ← PON LA ORDEN\n"
        f"🛑 <b>Stop Loss:</b> $4,771.00\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🎯 <b>TP1:</b> $4,726.00  (+1.74%)\n"
        f"🎯 <b>TP2:</b> $4,706.00  (+3.07%)\n"
        f"🎯 <b>TP3:</b> $4,680.00  (+4.80%)\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 <b>Score:</b> 8/21  📉 <b>RSI:</b> 64.2\n"
        f"📐 <b>R:R TP1:</b> 1.4:1\n"
        f"⏱️ <b>TF:</b> 1H  |  💵 DXY: BULLISH (+1)\n"
        f"<i>Esperar vela de rechazo para confirmar</i>"
    )
    return send(msg, "TEST 1 — PREP_SELL (Setup SELL en resistencia $4,741-$4,763)")

# ─────────────────────────────────────────────────────────────
# TEST 2 — LIVE_SELL: vela en tiempo real rechazando resistencia
# ─────────────────────────────────────────────────────────────
def test_2_live_sell():
    msg = (
        f"⚡ <b>RECHAZO LIVE — ORO SELL (1H)</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🔴 Vela actual formando rechazo en resistencia\n"
        f"📍 <b>High vela:</b> $4,759.80\n"
        f"📍 <b>Cierre actual:</b> $4,738.40  (bearish)\n"
        f"📌 <b>Entry ahora:</b> $4,738.00\n"
        f"🛑 <b>Stop Loss:</b> $4,763.00\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🎯 <b>TP1:</b> $4,721.00  (+2.3%)\n"
        f"🎯 <b>TP2:</b> $4,700.00  (+5.0%)\n"
        f"🎯 <b>TP3:</b> $4,672.00\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 <b>Score SELL:</b> 10/21  📉 <b>RSI:</b> 66.8\n"
        f"⚡ <b>Vela VIVA:</b> rechazo confirmado en zona\n"
        f"⏱️ <b>TF:</b> 1H  |  No esperes al cierre"
    )
    return send(msg, "TEST 2 — LIVE_SELL (Rechazo live en resistencia)")

# ─────────────────────────────────────────────────────────────
# TEST 3 — SELL FUERTE: precio claramente bajo EMAs, canal bajista
# ─────────────────────────────────────────────────────────────
def test_3_sell_fuerte():
    msg = (
        f"🔴 <b>SELL FUERTE (1H) — ORO XAUUSD</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📌 <b>SELL LIMIT:</b> $4,741.00  ← PON LA ORDEN\n"
        f"🛑 <b>Stop Loss:</b> $4,763.00  (19 USD)\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🎯 <b>TP1:</b> $4,712.00  (+1.9%)  ← Cierra 33%\n"
        f"🎯 <b>TP2:</b> $4,683.00  (+3.8%)  ← Cierra 33%\n"
        f"🎯 <b>TP3:</b> $4,640.00  (+6.7%)  ← Cierra resto\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 <b>Score:</b> 12/21  📉 <b>RSI:</b> 62.4\n"
        f"📐 <b>R:R TP1:</b> 1.7:1\n"
        f"⏱️ <b>TF:</b> 1H  |  Canal bajista activo\n"
        f"✅ TF superior 4H confirma sesgo BEARISH"
    )
    return send(msg, "TEST 3 — SELL FUERTE ($4,741 resistencia principal)")

# ─────────────────────────────────────────────────────────────
# TEST 4 — PREP_BUY: precio llegando a soporte $4,600-$4,640
# ─────────────────────────────────────────────────────────────
def test_4_prep_buy():
    msg = (
        f"⚠️ <b>SETUP BUY — ORO (XAUUSD) 1H</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🟢 Precio en <b>zona de soporte</b> — esperar rebote\n"
        f"📍 <b>Zona Soporte:</b> $4,600 – $4,640\n"
        f"📌 <b>BUY LIMIT:</b> $4,618.00  ← PON LA ORDEN\n"
        f"🛑 <b>Stop Loss:</b> $4,594.00\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🎯 <b>TP1:</b> $4,650.00  (+1.5%)\n"
        f"🎯 <b>TP2:</b> $4,683.00  (+3.1%)\n"
        f"🎯 <b>TP3:</b> $4,721.00  (+5.0%)\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 <b>Score:</b> 7/21  📈 <b>RSI:</b> 38.1\n"
        f"📐 <b>R:R TP1:</b> 1.3:1\n"
        f"⏱️ <b>TF:</b> 1H  |  💵 DXY: BEARISH (-1)\n"
        f"<i>Esperar vela de rebote para confirmar</i>"
    )
    return send(msg, "TEST 4 — PREP_BUY (Setup BUY en soporte $4,600-$4,640)")

# ─────────────────────────────────────────────────────────────
# TEST 5 — LIVE_BUY: rebote en soporte en tiempo real
# ─────────────────────────────────────────────────────────────
def test_5_live_buy():
    msg = (
        f"⚡ <b>REBOTE LIVE — ORO BUY (1H)</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🟢 Vela actual formando rebote en soporte\n"
        f"📍 <b>Low vela:</b> $4,603.20\n"
        f"📍 <b>Cierre actual:</b> $4,628.50  (bullish)\n"
        f"📌 <b>Entry ahora:</b> $4,628.00\n"
        f"🛑 <b>Stop Loss:</b> $4,596.00\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🎯 <b>TP1:</b> $4,660.00  (+2.4%)\n"
        f"🎯 <b>TP2:</b> $4,695.00  (+5.5%)\n"
        f"🎯 <b>TP3:</b> $4,721.00\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 <b>Score BUY:</b> 9/21  📈 <b>RSI:</b> 35.6\n"
        f"⚡ <b>Vela VIVA:</b> rebote confirmado en zona\n"
        f"⏱️ <b>TF:</b> 1H  |  No esperes al cierre"
    )
    return send(msg, "TEST 5 — LIVE_BUY (Rebote live en soporte $4,600)")

# ─────────────────────────────────────────────────────────────
# TEST 6 — BUY FUERTE en Fibonacci 0.618 ($4,495)
# ─────────────────────────────────────────────────────────────
def test_6_buy_fib618():
    msg = (
        f"🟢 <b>BUY FUERTE (1H) — ORO XAUUSD</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📌 <b>BUY LIMIT:</b> $4,495.00  ← PON LA ORDEN\n"
        f"📍 Confluencia: Fib 0.618 + zona demanda 1D\n"
        f"🛑 <b>Stop Loss:</b> $4,468.00  (27 USD)\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🎯 <b>TP1:</b> $4,560.00  (+1.4%)  ← Cierra 33%\n"
        f"🎯 <b>TP2:</b> $4,640.00  (+3.2%)  ← Cierra 33%\n"
        f"🎯 <b>TP3:</b> $4,721.00  (+5.0%)  ← Cierra resto\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 <b>Score:</b> 13/21  📈 <b>RSI:</b> 29.8  (sobreventa)\n"
        f"📐 <b>R:R TP1:</b> 2.4:1\n"
        f"⏱️ <b>TF:</b> 1H  |  Fib 0.618 = $4,495\n"
        f"✅ TF superior 1D confirma zona de demanda histórica"
    )
    return send(msg, "TEST 6 — BUY FUERTE Fibonacci 0.618 ($4,495)")

# ─────────────────────────────────────────────────────────────
# TEST 7 — BUY MÁXIMA en Fibonacci 0.786 ($4,388)
# ─────────────────────────────────────────────────────────────
def test_7_buy_fib786():
    msg = (
        f"🔥 <b>BUY MÁXIMA (1H) — ORO XAUUSD</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📌 <b>BUY LIMIT:</b> $4,388.00  ← PON LA ORDEN\n"
        f"📍 Confluencia: Fib 0.786 + soporte mayor 1D\n"
        f"🛑 <b>Stop Loss:</b> $4,355.00  (33 USD)\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🎯 <b>TP1:</b> $4,495.00  (+2.4%)  ← Fib 0.618\n"
        f"🎯 <b>TP2:</b> $4,600.00  (+4.8%)  ← Fib 0.5\n"
        f"🎯 <b>TP3:</b> $4,763.00  (+8.6%)  ← Resistencia\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 <b>Score:</b> 16/21  📈 <b>RSI:</b> 22.1  (sobreventa extrema)\n"
        f"📐 <b>R:R TP1:</b> 3.6:1\n"
        f"⏱️ <b>TF:</b> 1H  |  Fib 0.786 = $4,388\n"
        f"🔥 Señal de máxima confluencia — RSI extremo + Fibonacci + zona 1D"
    )
    return send(msg, "TEST 7 — BUY MÁXIMA Fibonacci 0.786 ($4,388)")

# ─────────────────────────────────────────────────────────────
# TEST 8 — TP1 alcanzado en señal SELL
# ─────────────────────────────────────────────────────────────
def test_8_tp1_sell():
    msg = (
        f"🎯 <b>TP1 ALCANZADO</b>\n"
        f"\n"
        f"📊 XAUUSD_1H | VENTA\n"
        f"💰 Entrada: $4,752.00\n"
        f"✅ TP1: $4,726.00\n"
        f"📉 Actual: $4,725.80\n"
        f"💵 Beneficio: +0.73%\n"
        f"\n"
        f"📋 <b>ACCIÓN RECOMENDADA:</b>\n"
        f"🔴 Cerrar 33% de la posición\n"
        f"🔒 Mover SL a breakeven ($4,752.00)\n"
        f"⏳ Dejar correr hacia TP2 ($4,706.00)"
    )
    return send(msg, "TEST 8 — TP1 alcanzado (SELL $4,752 → $4,726)")

# ─────────────────────────────────────────────────────────────
# TEST 9 — TP2 alcanzado en señal SELL
# ─────────────────────────────────────────────────────────────
def test_9_tp2_sell():
    msg = (
        f"🎯🎯 <b>TP2 ALCANZADO</b>\n"
        f"\n"
        f"📊 XAUUSD_1H | VENTA\n"
        f"💰 Entrada: $4,752.00\n"
        f"✅ TP2: $4,706.00\n"
        f"📉 Actual: $4,705.40\n"
        f"💵 Beneficio: +1.54%\n"
        f"\n"
        f"📋 <b>ACCIÓN RECOMENDADA:</b>\n"
        f"🔴 Cerrar 33% de la posición\n"
        f"🔒 Mover SL a TP1 ($4,726.00)\n"
        f"⏳ Dejar correr hacia TP3 ($4,680.00)"
    )
    return send(msg, "TEST 9 — TP2 alcanzado (SELL)")

# ─────────────────────────────────────────────────────────────
# TEST 10 — TP3 alcanzado (cierre completo)
# ─────────────────────────────────────────────────────────────
def test_10_tp3_sell():
    msg = (
        f"🎯🎯🎯 <b>TP3 ALCANZADO!</b>\n"
        f"\n"
        f"📊 XAUUSD_1H | VENTA\n"
        f"💰 Entrada: $4,752.00\n"
        f"✅ TP3: $4,640.00\n"
        f"📉 Actual: $4,638.20\n"
        f"💵 Beneficio: +2.99%\n"
        f"\n"
        f"📋 <b>ACCIÓN RECOMENDADA:</b>\n"
        f"🔴 Cerrar el 100% restante de la posición\n"
        f"🏆 ¡Operación completada con éxito!"
    )
    return send(msg, "TEST 10 — TP3 alcanzado (SELL, cierre completo)")

# ─────────────────────────────────────────────────────────────
# TEST 11 — TP1 alcanzado en señal BUY
# ─────────────────────────────────────────────────────────────
def test_11_tp1_buy():
    msg = (
        f"🎯 <b>TP1 ALCANZADO</b>\n"
        f"\n"
        f"📊 XAUUSD_1H | COMPRA\n"
        f"💰 Entrada: $4,618.00\n"
        f"✅ TP1: $4,650.00\n"
        f"📈 Actual: $4,651.30\n"
        f"💵 Beneficio: +0.69%\n"
        f"\n"
        f"📋 <b>ACCIÓN RECOMENDADA:</b>\n"
        f"🔴 Cerrar 33% de la posición\n"
        f"🔒 Mover SL a breakeven ($4,618.00)\n"
        f"⏳ Dejar correr hacia TP2 ($4,683.00)"
    )
    return send(msg, "TEST 11 — TP1 alcanzado (BUY $4,618 → $4,650)")

# ─────────────────────────────────────────────────────────────
# TEST 12 — SL activado en señal SELL
# ─────────────────────────────────────────────────────────────
def test_12_sl_sell():
    msg = (
        f"❌ <b>STOP LOSS ACTIVADO</b>\n"
        f"\n"
        f"📊 XAUUSD_1H | VENTA\n"
        f"💰 Entrada: $4,741.00\n"
        f"🛑 SL: $4,763.00\n"
        f"📈 Actual: $4,764.50\n"
        f"💸 Pérdida: -0.59%\n"
        f"\n"
        f"📋 <b>ACCIÓN RECOMENDADA:</b>\n"
        f"🔴 Cerrar el 100% de la posición"
    )
    return send(msg, "TEST 12 — SL activado (SELL, precio rompió $4,763)")

# ─────────────────────────────────────────────────────────────
# TEST 13 — SL activado en señal BUY
# ─────────────────────────────────────────────────────────────
def test_13_sl_buy():
    msg = (
        f"❌ <b>STOP LOSS ACTIVADO</b>\n"
        f"\n"
        f"📊 XAUUSD_1H | COMPRA\n"
        f"💰 Entrada: $4,495.00\n"
        f"🛑 SL: $4,468.00\n"
        f"📉 Actual: $4,466.80\n"
        f"💸 Pérdida: -0.63%\n"
        f"\n"
        f"📋 <b>ACCIÓN RECOMENDADA:</b>\n"
        f"🔴 Cerrar el 100% de la posición"
    )
    return send(msg, "TEST 13 — SL activado (BUY Fib 0.618, caída continuó)")

# ─────────────────────────────────────────────────────────────
# TEST 14 — Cancelar señal SELL (BUY tomó el control)
# ─────────────────────────────────────────────────────────────
def test_14_cancelar_sell():
    msg = (
        f"❌ <b>CANCELAR SELL — ORO (1H) INTRADÍA</b> ❌\n"
        f"Precio rompió resistencia — setup SELL invalidado\n"
        f"💰 Precio: $4,768.00  |  Zona era: $4,741-$4,763\n"
        f"📈 Señal BUY activada en dirección opuesta"
    )
    return send(msg, "TEST 14 — CANCELAR SELL (precio rompió resistencia al alza)")

# ─────────────────────────────────────────────────────────────
# TEST 15 — Cancelar señal BUY (continuación bajista)
# ─────────────────────────────────────────────────────────────
def test_15_cancelar_buy():
    msg = (
        f"❌ <b>CANCELAR BUY — ORO (1H) INTRADÍA</b> ❌\n"
        f"Precio perdió soporte — setup BUY invalidado\n"
        f"💰 Precio: $4,592.00  |  Zona era: $4,600-$4,640\n"
        f"📉 Señal SELL activada en dirección opuesta"
    )
    return send(msg, "TEST 15 — CANCELAR BUY (precio rompió soporte a la baja)")

# ─────────────────────────────────────────────────────────────
# TEST 16 — Aviso 50% hacia TP1
# ─────────────────────────────────────────────────────────────
def test_16_progreso_50():
    msg = (
        f"⚡ <b>Trade avanzando — 50% hacia TP1</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 XAUUSD_1H | VENTA\n"
        f"💰 Entrada: $4,752.00\n"
        f"📍 Actual: $4,739.00  (50% del camino)\n"
        f"🎯 TP1: $4,726.00  |  Faltan $13.00\n"
        f"💵 P&L actual: -0.55%\n"
        f"🔒 Considera mover SL a breakeven ($4,752.00)"
    )
    return send(msg, "TEST 16 — Aviso 50% progreso hacia TP1")

# ─────────────────────────────────────────────────────────────
# TEST 17 — Confirmación de entrada (PENDIENTE_CONFIRM → ACTIVA)
# ─────────────────────────────────────────────────────────────
def test_17_confirmacion_entrada():
    msg = (
        f"✅ <b>ENTRADA CONFIRMADA — ORO (XAUUSD) 1H</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🔴 <b>Dirección:</b> VENTA\n"
        f"🕐 <b>Confirmado por:</b> análisis velas 1M\n"
        f"📌 SELL LIMIT: <b>$4,741.00</b>  ← PON LA ORDEN AHORA\n"
        f"🛑 <b>Stop Loss:</b> $4,763.00\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🎯 <b>TP1:</b> $4,712.00\n"
        f"🎯 <b>TP2:</b> $4,683.00\n"
        f"🎯 <b>TP3:</b> $4,640.00\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 <b>Score 1H:</b> 11/21  ⏱️ <b>TF:</b> 1H+1M\n"
        f"<code>1M: EMA9=4738.20 EMA21=4741.50 | RSI=44.2 | ATR=1.80</code>"
    )
    return send(msg, "TEST 17 — Confirmación entrada (1M confirma la señal 1H)")

# ─────────────────────────────────────────────────────────────
# TEST 18 — Setup caducado (PENDIENTE_CONFIRM sin confirmación)
# ─────────────────────────────────────────────────────────────
def test_18_setup_caducado():
    msg = (
        f"⏰ <b>Setup 1H caducado</b> — sin confirmación 15M/5M\n"
        f"📊 XAUUSD_1H | VENTA\n"
        f"⌛ Esperó 120 min sin alineación inferior"
    )
    return send(msg, "TEST 18 — Setup caducado (PENDIENTE_CONFIRM expiró)")

# ─────────────────────────────────────────────────────────────
# TEST 18b — BREAKEVEN SELL (TP1 tocado, precio volvió a entrada)
# ─────────────────────────────────────────────────────────────
def test_18b_breakeven_sell():
    msg = (
        f"🔄 <b>BREAKEVEN — XAUUSD_1H</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 VENTA | TP1 alcanzado previamente\n"
        f"💰 Entrada: $4,752.00\n"
        f"📍 Precio tocó breakeven: $4,752.00\n"
        f"📈 Actual: $4,753.40\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"✅ Trade cerrado en <b>0% de pérdida</b>\n"
        f"🔍 El bot buscará nueva oportunidad de entrada"
    )
    return send(msg, "TEST 18b — BREAKEVEN SELL (precio volvió a entrada tras TP1)")

# ─────────────────────────────────────────────────────────────
# TEST 18c — BREAKEVEN BUY (TP1 tocado, precio volvió a entrada)
# ─────────────────────────────────────────────────────────────
def test_18c_breakeven_buy():
    msg = (
        f"🔄 <b>BREAKEVEN — XAUUSD_1H</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 COMPRA | TP1 alcanzado previamente\n"
        f"💰 Entrada: $4,618.00\n"
        f"📍 Precio tocó breakeven: $4,618.00\n"
        f"📉 Actual: $4,616.80\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"✅ Trade cerrado en <b>0% de pérdida</b>\n"
        f"🔍 El bot buscará nueva oportunidad de entrada"
    )
    return send(msg, "TEST 18c — BREAKEVEN BUY (precio volvió a entrada tras TP1)")

# ─────────────────────────────────────────────────────────────
# TEST 19 — SELL MÁXIMA (score muy alto, múltiples confluencias)
# ─────────────────────────────────────────────────────────────
def test_19_sell_maxima():
    msg = (
        f"🔥 <b>SELL MÁXIMA (1H) — ORO XAUUSD</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📌 <b>SELL LIMIT:</b> $4,763.00  ← PON LA ORDEN\n"
        f"📍 Confluencia: Resist mayor + Fib 0.236 + canal bajista\n"
        f"🛑 <b>Stop Loss:</b> $4,782.00  (19 USD)\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🎯 <b>TP1:</b> $4,741.00  (+1.5%)  ← Cierra 33%\n"
        f"🎯 <b>TP2:</b> $4,721.00  (+2.8%)  ← Cierra 33%\n"
        f"🎯 <b>TP3:</b> $4,683.00  (+4.2%)  ← Cierra resto\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 <b>Score:</b> 17/21  📉 <b>RSI:</b> 71.4  (sobrecompra)\n"
        f"📐 <b>R:R TP1:</b> 1.2:1\n"
        f"⏱️ <b>TF:</b> 1H  |  Máxima confluencia\n"
        f"🔥 RSI sobrecompra + Fibonacci + canal + DXY BULLISH"
    )
    return send(msg, "TEST 19 — SELL MÁXIMA ($4,763 resist mayor con confluencias)")

# ─────────────────────────────────────────────────────────────
# TEST 20 — FIN de suite
# ─────────────────────────────────────────────────────────────
def test_20_fin():
    msg = (
        f"✅ <b>SUITE COMPLETA — TODOS LOS TESTS OK</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"20 tipos de mensajes verificados:\n"
        f"  ✅ PREP_SELL / PREP_BUY\n"
        f"  ✅ LIVE_SELL / LIVE_BUY\n"
        f"  ✅ SELL FUERTE / BUY FUERTE\n"
        f"  ✅ SELL MÁXIMA / BUY MÁXIMA\n"
        f"  ✅ TP1 / TP2 / TP3 (SELL y BUY)\n"
        f"  ✅ SL activado (SELL y BUY)\n"
        f"  ✅ CANCELAR señal\n"
        f"  ✅ Aviso 50% progreso\n"
        f"  ✅ Confirmación entrada\n"
        f"  ✅ Setup caducado\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Si ves este mensaje, Telegram funciona correctamente 🎉"
    )
    return send(msg, "TEST 20 — FIN de suite")


# ─────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────
if __name__ == '__main__':
    tests = [
        test_0_ping,
        test_1_prep_sell,
        test_2_live_sell,
        test_3_sell_fuerte,
        test_4_prep_buy,
        test_5_live_buy,
        test_6_buy_fib618,
        test_7_buy_fib786,
        test_8_tp1_sell,
        test_9_tp2_sell,
        test_10_tp3_sell,
        test_11_tp1_buy,
        test_12_sl_sell,
        test_13_sl_buy,
        test_14_cancelar_sell,
        test_15_cancelar_buy,
        test_16_progreso_50,
        test_17_confirmacion_entrada,
        test_18_setup_caducado,
        test_18b_breakeven_sell,
        test_18c_breakeven_buy,
        test_19_sell_maxima,
        test_20_fin,
    ]

    resultados = []
    for t in tests:
        ok = t()
        resultados.append((t.__name__, ok))

    print(f"\n{'='*60}")
    print(f"  RESUMEN FINAL ({len(tests)} tests)")
    print(f"{'='*60}")
    aprobados = sum(1 for _, ok in resultados if ok)
    fallidos  = sum(1 for _, ok in resultados if not ok)
    for nombre, ok in resultados:
        estado = "✅" if ok else "❌"
        print(f"  {estado}  {nombre}")
    print(f"{'='*60}")
    print(f"  Aprobados: {aprobados}/{len(tests)}  |  Fallidos: {fallidos}")
    if fallidos == 0:
        print(f"  🎉 Telegram funciona correctamente en todos los casos")
    else:
        print(f"  ⚠️  Revisar TOKEN y CHAT_ID en .env")
        token = os.environ.get('TELEGRAM_TOKEN', '')
        chat  = os.environ.get('TELEGRAM_CHAT_ID', '')
        print(f"  TOKEN:   {'OK (...' + token[-6:] + ')' if token else 'NO ENCONTRADO'}")
        print(f"  CHAT_ID: {chat if chat else 'NO ENCONTRADO'}")
    print(f"{'='*60}")
