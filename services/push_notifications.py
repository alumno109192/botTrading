"""
services/push_notifications.py — Envío de Web Push Notifications (VAPID)

Flujo:
  1. El navegador (móvil o escritorio) se suscribe → guarda endpoint + keys en BD
  2. Cuando base_detector genera una señal → llama a enviar_push_senal()
  3. Esta función lee todas las suscripciones de BD y envía la notificación push
  4. El Service Worker (sw.js) la muestra como notificación nativa del sistema

Clic en la notificación → abre /dashboard/activas con los detalles de la señal.
"""

import os
import json
import logging

logger = logging.getLogger('bottrading')

_VAPID_PUBLIC_KEY  = os.environ.get('VAPID_PUBLIC_KEY', '')
_VAPID_PRIVATE_KEY = os.environ.get('VAPID_PRIVATE_KEY', '')
_VAPID_EMAIL       = os.environ.get('VAPID_CLAIMS_EMAIL', 'mailto:admin@bottrading.app')

def _push_disponible() -> bool:
    """Devuelve True si pywebpush está instalado y las claves VAPID están configuradas."""
    if not _VAPID_PUBLIC_KEY or not _VAPID_PRIVATE_KEY:
        return False
    try:
        import pywebpush  # noqa: F401
        return True
    except ImportError:
        return False


def enviar_push_senal(senal_id: int, titulo: str, cuerpo: str,
                      simbolo: str = '', timeframe: str = '',
                      direccion: str = '', url: str = '') -> int:
    """
    Envía una notificación push a todos los suscriptores registrados.

    Args:
        senal_id:   ID de la señal en BD (para el link de detalle)
        titulo:     Título de la notificación (ej. "🔥 SELL FUERTE — ORO 1H")
        cuerpo:     Cuerpo corto (precio, TP1, SL)
        simbolo:    Símbolo para construir la URL de detalle
        timeframe:  Timeframe de la señal
        direccion:  COMPRA / VENTA

    Returns:
        Número de notificaciones enviadas con éxito.
    """
    if not _push_disponible():
        return 0

    try:
        from pywebpush import webpush, WebPushException
        from adapters.database import get_db
    except ImportError:
        return 0

    db = get_db()
    if db is None:
        return 0

    # Leer suscripciones activas
    try:
        result = db.ejecutar_query(
            "SELECT endpoint, subscription_json FROM push_subscriptions"
        )
        suscripciones = [dict(r) for r in result.rows] if result.rows else []
    except Exception as e:
        logger.error(f"❌ Push: error leyendo suscripciones: {e}")
        return 0

    if not suscripciones:
        return 0

    # URL de detalle al hacer clic
    url_detalle = url if url else "/dashboard/activas"

    payload = json.dumps({
        'title':     titulo,
        'body':      cuerpo,
        'tag':       f"signal-{senal_id}",
        'senal_id':  senal_id,
        'url':       url_detalle,
    })

    # Encabezados VAPID
    vapid_claims = {
        'sub': _VAPID_EMAIL,
    }

    enviados    = 0
    a_eliminar  = []  # endpoints caducados / inválidos

    for sub_row in suscripciones:
        try:
            sub_info = json.loads(sub_row['subscription_json'])
            webpush(
                subscription_info=sub_info,
                data=payload,
                vapid_private_key=_VAPID_PRIVATE_KEY,
                vapid_claims=vapid_claims,
                # Urgency high → FCM entrega inmediatamente aunque Android
                # tenga Chrome en modo "Optimizado" de batería.
                # TTL 24h → el push se reintenta hasta 24h si el dispositivo
                # está offline en el momento del envío.
                headers={"urgency": "high"},
                ttl=86400,
            )
            enviados += 1
        except Exception as exc:
            exc_str = str(exc)
            # 410 Gone / 404 Not Found → suscripción caducada, eliminar
            # 403 con "aud claim" → endpoint FCM legacy deprecado por Google, eliminar
            if ('410' in exc_str or '404' in exc_str or 'Gone' in exc_str
                    or ('403' in exc_str and 'aud' in exc_str.lower())):
                a_eliminar.append(sub_row['endpoint'])
                logger.info(f"📲 Push: suscripción caducada/deprecada, se eliminará: {sub_row['endpoint'][:50]}...")
            else:
                logger.warning(f"⚠️ Push: error enviando a {sub_row['endpoint'][:50]}: {exc_str[:100]}")

    # Limpiar suscripciones caducadas
    for endpoint in a_eliminar:
        try:
            db.ejecutar_query(
                "DELETE FROM push_subscriptions WHERE endpoint = ?", (endpoint,)
            )
        except Exception:
            pass

    if enviados:
        logger.info(f"📲 Push enviado a {enviados}/{len(suscripciones)} suscriptores — señal #{senal_id}")

    return enviados


def crear_tabla_push_si_no_existe() -> None:
    """Crea la tabla push_subscriptions en BD si no existe.
    Llamar al arrancar la app.
    """
    try:
        from adapters.database import get_db
        db = get_db()
        if db is None:
            return
        db.ejecutar_query("""
            CREATE TABLE IF NOT EXISTS push_subscriptions (
                endpoint         TEXT PRIMARY KEY,
                subscription_json TEXT NOT NULL,
                created_at       TEXT DEFAULT (datetime('now'))
            )
        """)
        logger.info("✅ Tabla push_subscriptions verificada")
    except Exception as e:
        logger.warning(f"⚠️ No se pudo crear tabla push_subscriptions: {e}")
