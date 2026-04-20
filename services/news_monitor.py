"""
gold_news_monitor.py — Monitor de noticias fundamentales para XAUUSD (Oro)

Analiza titulares de múltiples fuentes RSS gratuitas y determina el sesgo
fundamental (alcista/bajista) para el oro.  Sin API key requerida.

Fuentes:
  • Kitco News           — noticias específicas de oro/metales preciosos
  • Yahoo Finance GC=F  — gold futures
  • Reuters Business    — filtrado por keywords oro
  • CNBC Commodities    — filtrado por keywords oro

Lógica:
  • Recoge titulares cada FETCH_INTERVAL minutos
  • Aplica scoring por keywords ponderadas (alcista / bajista)
  • Envía resumen a Telegram cada CHECK_INTERVAL horas
  • Envía inmediatamente si el sesgo cambia entre alcista ↔ bajista

Uso independiente:
    python gold_news_monitor.py
"""

import os
import re
import sys
import time
import requests
try:
    import defusedxml.ElementTree as ET
except ImportError:
    import xml.etree.ElementTree as ET
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

# ══════════════════════════════════════
# CONFIGURACIÓN
# ══════════════════════════════════════
TELEGRAM_TOKEN     = os.environ.get('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID   = os.environ.get('TELEGRAM_CHAT_ID')
try:
    TELEGRAM_THREAD_ID = int(os.environ.get('THREAD_ID_NEWS') or 0) or None
except (ValueError, TypeError):
    TELEGRAM_THREAD_ID = None

CHECK_INTERVAL  = 4 * 60 * 60   # segundos entre envíos periódicos (4h)
FETCH_INTERVAL  = 30 * 60       # segundos entre ciclos de fetch (30 min)
MIN_ARTICULOS   = 3             # mínimo artículos para emitir señal

# ══════════════════════════════════════
# FUENTES RSS
# ══════════════════════════════════════
FUENTES_RSS = [
    {
        'nombre':       'Kitco News',
        'url':          'https://www.kitco.com/rss/KitcoNews.rss',
        'filtro_gold':  False,   # ya es específico de metales preciosos
    },
    {
        'nombre':       'Yahoo Finance – GC=F',
        'url':          'https://finance.yahoo.com/rss/headline?s=GC%3DF',
        'filtro_gold':  False,
    },
    {
        'nombre':       'Reuters Business',
        'url':          'https://feeds.reuters.com/reuters/businessNews',
        'filtro_gold':  True,    # filtrar por keywords oro
    },
    {
        'nombre':       'CNBC Commodities',
        'url':          'https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100727362',
        'filtro_gold':  True,
    },
]

# Palabras clave para filtrar artículos de oro en fuentes generales
_KEYWORDS_FILTRO = [
    'gold', 'xauusd', 'xau', 'bullion', 'precious metal',
    'comex', 'spot gold', 'gold futures', 'oro', 'metal prices',
]

# ══════════════════════════════════════
# SENTIMIENTO — ALCISTA (peso positivo)
# ══════════════════════════════════════
KEYWORDS_ALCISTA = {
    # Movimiento de precio
    'rally':             2, 'surges':           2, 'surge':            2,
    'rises':             1, 'gains':            1, 'climbs':           1,
    'soars':             2, 'jumps':            2, 'spikes':           2,
    'record high':       3, 'all-time high':    3, 'new high':         2,
    'breakout':          2, 'highest':          2, 'outperforms':      1,
    'upside':            1, 'bullish':          2, 'buy signal':       2,
    # Política monetaria — dovish
    'rate cut':          2, 'rate cuts':        2, 'dovish':           2,
    'fed pivot':         3, 'pause':            1, 'lower rates':      2,
    'easing':            1, 'quantitative easing': 2,
    # Inflación
    'inflation':         1, 'cpi':              1, 'stagflation':      2,
    'hyperinflation':    3, 'inflation fears':  2, 'higher inflation': 2,
    'inflation rises':   2, 'hot inflation':    2,
    # Debilidad del dólar
    'dollar weakens':    2, 'dollar falls':     2, 'weaker dollar':    2,
    'dollar drops':      2, 'usd falls':        2, 'usd weakness':     2,
    'dollar index falls':2, 'dxy falls':        2, 'dxy drops':        2,
    # Geopolítica / riesgo
    'geopolitical':      1, 'conflict':         1, 'war':              1,
    'tensions':          1, 'crisis':           1, 'uncertainty':      1,
    'safe haven':        2, 'safe-haven':       2, 'flight to safety': 3,
    # Demanda institucional
    'central bank buying': 3, 'central banks buy': 3,
    'gold demand':       2, 'etf inflows':      2, 'inflows':          1,
    # Macro
    'tariffs':           1, 'trade war':        2, 'sanctions':        1,
    'debt ceiling':      2, 'fiscal deficit':   1,
    'recession':         2, 'recession fears':  2, 'slowdown':         1,
}

# ══════════════════════════════════════
# SENTIMIENTO — BAJISTA (peso negativo)
# ══════════════════════════════════════
KEYWORDS_BAJISTA = {
    # Movimiento de precio
    'falls':             1, 'drops':            1, 'declines':         1,
    'slumps':            2, 'tumbles':          2, 'plunges':          2,
    'sinks':             2, 'retreats':         1, 'lowest':           2,
    'bearish':           2, 'sell signal':      2, 'sell-off':         2,
    'selling pressure':  2, 'profit taking':    1, 'overbought':       1,
    # Política monetaria — hawkish
    'rate hike':         2, 'rate hikes':       2, 'hawkish':          2,
    'tightening':        1, 'higher rates':     2, 'aggressive fed':   2,
    'fed hikes':         2, 'fed raises':       2,
    # Inflación baja
    'inflation cooling': 2, 'disinflation':     2, 'deflation':        2,
    'inflation eases':   2, 'cpi lower':        2, 'cpi cools':        2,
    # Fortaleza del dólar
    'dollar strengthens':2, 'dollar rises':     2, 'stronger dollar':  2,
    'dollar gains':      2, 'usd gains':        2, 'usd strength':     2,
    'dollar index rises':2, 'dxy rises':        2, 'dxy gains':        2,
    # Risk-on
    'risk-on':           1, 'risk on':          1, 'stocks rally':     1,
    'equities rise':     1, 'stock market gains':1,
    # Reducción de tensión
    'ceasefire':         1, 'peace talks':      1, 'tensions ease':    2,
    'de-escalation':     2, 'deal reached':     1,
    # Demanda baja
    'etf outflows':      2, 'outflows':         1,
    'gold selling':      2, 'gold dumped':      2,
}

# ══════════════════════════════════════
# ESTADO INTERNO
# ══════════════════════════════════════
_ultimo_envio = None      # datetime del último mensaje enviado
_ultimo_sesgo = None      # str del último sesgo notificado


# ══════════════════════════════════════
# TELEGRAM
# ══════════════════════════════════════
def enviar_telegram(mensaje: str):
    """Envía mensaje HTML a Telegram (hilo Swing gold)."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠️  [NEWS] Telegram no configurado — saltando envío")
        return
    url     = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": mensaje, "parse_mode": "HTML"}
    if TELEGRAM_THREAD_ID:
        payload["message_thread_id"] = TELEGRAM_THREAD_ID
    for intento in range(1, 4):
        try:
            r = requests.post(url, json=payload, timeout=10)
            if r.status_code == 200:
                print(f"✅ [NEWS] Telegram enviado (intento {intento})")
                return
            print(f"❌ [NEWS] HTTP {r.status_code} (intento {intento}): {r.text[:80]}")
        except Exception as e:
            print(f"❌ [NEWS] Excepción (intento {intento}): {e}")
        time.sleep(2 ** intento)


# ══════════════════════════════════════
# FETCH RSS
# ══════════════════════════════════════
def _fetch_rss(fuente: dict) -> list:
    """Descarga y parsea un feed RSS.

    Devuelve lista de dicts con claves: titulo, resumen, fuente, texto.
    """
    articulos = []
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (compatible; GoldNewsBot/1.0)'}
        r = requests.get(fuente['url'], headers=headers, timeout=15)
        if r.status_code != 200:
            print(f"⚠️  [NEWS] {fuente['nombre']} → HTTP {r.status_code}")
            return articulos

        root = ET.fromstring(r.content)
        ns   = {'atom': 'http://www.w3.org/2005/Atom'}

        items = root.findall('.//item')              # RSS 2.0
        if not items:
            items = root.findall('.//atom:entry', ns)  # Atom

        for item in items[:20]:
            titulo  = (item.findtext('title') or
                       item.findtext('atom:title', namespaces=ns) or '').strip()
            resumen = (item.findtext('description') or
                       item.findtext('summary') or
                       item.findtext('atom:summary', namespaces=ns) or '').strip()

            # Limpiar HTML básico
            resumen_limpio = re.sub(r'<[^>]+>', ' ', resumen)
            resumen_limpio = re.sub(r'\s+', ' ', resumen_limpio).strip()[:300]

            texto = (titulo + ' ' + resumen_limpio).lower()

            # Filtro de relevancia para fuentes generales
            if fuente['filtro_gold']:
                if not any(kw in texto for kw in _KEYWORDS_FILTRO):
                    continue

            if titulo:
                articulos.append({
                    'titulo':  titulo,
                    'resumen': resumen_limpio,
                    'fuente':  fuente['nombre'],
                    'texto':   texto,
                })
    except ET.ParseError as e:
        print(f"⚠️  [NEWS] {fuente['nombre']} → XML error: {e}")
    except Exception as e:
        print(f"⚠️  [NEWS] {fuente['nombre']} → {e}")
    return articulos


# ══════════════════════════════════════
# ANÁLISIS DE SENTIMIENTO
# ══════════════════════════════════════
def _score_articulo(texto: str) -> int:
    """Devuelve score de sentimiento: positivo = alcista, negativo = bajista."""
    score = 0
    for kw, peso in KEYWORDS_ALCISTA.items():
        if kw in texto:
            score += peso
    for kw, peso in KEYWORDS_BAJISTA.items():
        if kw in texto:
            score -= peso
    return score


def analizar_noticias() -> dict:
    """Recoge noticias de todas las fuentes y devuelve análisis de sentimiento."""
    todos = []
    titulos_vistos: set = set()   # deduplicar artículos repetidos entre feeds
    for fuente in FUENTES_RSS:
        arts = _fetch_rss(fuente)
        nuevos = []
        for art in arts:
            # Normalizar título para comparación (minúsculas, sin espacios extra)
            titulo_norm = art['titulo'].lower().strip()
            if titulo_norm not in titulos_vistos:
                titulos_vistos.add(titulo_norm)
                nuevos.append(art)
        todos.extend(nuevos)
        print(f"   📰 {fuente['nombre']}: {len(nuevos)} artículos únicos ({len(arts)} totales)")

    if not todos:
        return {'sesgo': 'NEUTRAL', 'score_medio': 0.0, 'total': 0,
                'top_alcistas': [], 'top_bajistas': []}

    scored = [{**a, 'score': _score_articulo(a['texto'])} for a in todos]
    scored.sort(key=lambda x: abs(x['score']), reverse=True)

    score_medio = sum(a['score'] for a in scored) / len(scored)

    if score_medio >= 1.5:
        sesgo = 'ALCISTA_FUERTE'
    elif score_medio >= 0.5:
        sesgo = 'ALCISTA'
    elif score_medio <= -1.5:
        sesgo = 'BAJISTA_FUERTE'
    elif score_medio <= -0.5:
        sesgo = 'BAJISTA'
    else:
        sesgo = 'NEUTRAL'

    top_alcistas = sorted([a for a in scored if a['score'] > 0],
                          key=lambda x: x['score'], reverse=True)[:3]
    top_bajistas = sorted([a for a in scored if a['score'] < 0],
                          key=lambda x: x['score'])[:3]

    return {
        'sesgo':        sesgo,
        'score_medio':  round(score_medio, 2),
        'total':        len(scored),
        'top_alcistas': top_alcistas,
        'top_bajistas': top_bajistas,
    }


# ══════════════════════════════════════
# FORMATO TELEGRAM
# ══════════════════════════════════════
_MAP_SESGO = {
    'ALCISTA_FUERTE': ('🟢🟢', 'ALCISTA FUERTE',  '📈 Las noticias apuntan con fuerza al alza'),
    'ALCISTA':        ('🟢',   'ALCISTA',          '📈 Sesgo fundamental positivo para el oro'),
    'NEUTRAL':        ('⚪',   'NEUTRAL',          '↔️  Sin tendencia clara en noticias'),
    'BAJISTA':        ('🔴',   'BAJISTA',          '📉 Sesgo fundamental negativo para el oro'),
    'BAJISTA_FUERTE': ('🔴🔴', 'BAJISTA FUERTE',   '📉 Las noticias apuntan con fuerza a la baja'),
}


def _formatear_mensaje(resultado: dict) -> str:
    sesgo       = resultado['sesgo']
    score_medio = resultado['score_medio']
    total       = resultado['total']
    ahora       = datetime.now().strftime('%d/%m/%Y %H:%M')

    emoji, etiqueta, conclusion = _MAP_SESGO.get(sesgo, _MAP_SESGO['NEUTRAL'])

    lineas = [
        f"📰 <b>ANÁLISIS NOTICIAS ORO</b> {emoji}",
        f"━━━━━━━━━━━━━━━━━━━━",
        f"🎯 <b>Sesgo Fundamental:</b> {etiqueta}",
        f"📊 <b>Score medio:</b> {score_medio:+.1f}  |  <b>Artículos:</b> {total}",
        f"💡 {conclusion}",
        f"━━━━━━━━━━━━━━━━━━━━",
    ]

    if resultado['top_alcistas']:
        lineas.append("📈 <b>Factores ALCISTAS:</b>")
        for art in resultado['top_alcistas']:
            titulo = art['titulo'][:90] + ('…' if len(art['titulo']) > 90 else '')
            lineas.append(f"  • {titulo}")
            lineas.append(f"    <i>({art['fuente']})</i>")

    if resultado['top_bajistas']:
        if resultado['top_alcistas']:
            lineas.append("")
        lineas.append("📉 <b>Factores BAJISTAS:</b>")
        for art in resultado['top_bajistas']:
            titulo = art['titulo'][:90] + ('…' if len(art['titulo']) > 90 else '')
            lineas.append(f"  • {titulo}")
            lineas.append(f"    <i>({art['fuente']})</i>")

    lineas += [
        f"━━━━━━━━━━━━━━━━━━━━",
        f"⏰ {ahora}",
        f"⚠️ <i>Solo orientativo. Combinar siempre con análisis técnico.</i>",
    ]

    return "\n".join(lineas)


def _sesgo_cambio_relevante(nuevo: str, anterior: str) -> bool:
    """True si el cambio de sesgo es suficientemente relevante para notificar."""
    if anterior is None:
        return True
    bullish = {'ALCISTA', 'ALCISTA_FUERTE'}
    bearish = {'BAJISTA', 'BAJISTA_FUERTE'}
    if anterior in bullish and nuevo in bearish:
        return True
    if anterior in bearish and nuevo in bullish:
        return True
    if anterior == 'NEUTRAL' and nuevo != 'NEUTRAL':
        return True
    return False


# ══════════════════════════════════════
# BUCLE PRINCIPAL
# ══════════════════════════════════════
def main():
    """Ejecuta el monitor en bucle continuo (para uso como thread)."""
    global _ultimo_envio, _ultimo_sesgo

    print(f"[{datetime.now().strftime('%H:%M:%S')}] 📰 Monitor de noticias GOLD iniciado")
    print(f"   Resumen cada {CHECK_INTERVAL // 3600}h | Re-fetch cada {FETCH_INTERVAL // 60} min")

    enviar_telegram(
        "📰 <b>Monitor de Noticias ORO iniciado</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"⏱️ Resumen cada {CHECK_INTERVAL // 3600} horas\n"
        "📡 Fuentes: Kitco · Yahoo Finance · Reuters · CNBC\n"
        "🎯 Detecta sesgo fundamental XAUUSD\n"
        "⚠️ <i>Complemento del análisis técnico</i>"
    )

    while True:
        try:
            ahora = datetime.now()
            print(f"\n[{ahora.strftime('%H:%M:%S')}] 📰 Analizando noticias gold...")

            resultado = analizar_noticias()
            sesgo     = resultado['sesgo']
            total     = resultado['total']

            print(f"   ✅ {total} artículos | Sesgo: {sesgo} | Score: {resultado['score_medio']:+.2f}")

            debe_enviar = False
            if total >= MIN_ARTICULOS:
                if _ultimo_envio is None:
                    debe_enviar = True
                elif (ahora - _ultimo_envio).total_seconds() >= CHECK_INTERVAL:
                    debe_enviar = True
                elif _sesgo_cambio_relevante(sesgo, _ultimo_sesgo):
                    debe_enviar = True

            if debe_enviar:
                msg = _formatear_mensaje(resultado)
                enviar_telegram(msg)
                _ultimo_envio = ahora
                _ultimo_sesgo = sesgo
                print(f"   📨 Mensaje enviado → {sesgo}")
            else:
                print(f"   ⏭️  Sin envío (sin cambio relevante o < {MIN_ARTICULOS} artículos)")

        except Exception as e:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] ❌ [NEWS] Error en ciclo: {e}")

        time.sleep(FETCH_INTERVAL)


if __name__ == '__main__':
    main()
