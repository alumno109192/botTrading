"""
tests/unit/test_telegram_adapter.py — Tests unitarios para adapters/telegram.py

Cubre:
  - enviar_telegram: éxito en el primer intento
  - enviar_telegram: reintento tras fallo y éxito posterior
  - enviar_telegram: fallo tras todos los intentos → retorna False
  - enviar_telegram: excepción de red → reintenta
  - enviar_telegram: thread_id incluido en el payload
"""
import pytest
from unittest.mock import patch, MagicMock
import sys
import os

# Asegurar variables de entorno mínimas para que el módulo cargue
os.environ.setdefault('TELEGRAM_TOKEN',   'fake-token')
os.environ.setdefault('TELEGRAM_CHAT_ID', '123456789')

from adapters.telegram import enviar_telegram


def _make_response(status_code: int):
    """Crea un mock de requests.Response con el status_code indicado."""
    mock_resp = MagicMock()
    mock_resp.status_code = status_code
    mock_resp.text = 'OK' if status_code == 200 else 'Error'
    return mock_resp


class TestEnviarTelegram:
    def test_exito_en_primer_intento(self):
        with patch('adapters.telegram.requests.post',
                   return_value=_make_response(200)) as mock_post:
            resultado = enviar_telegram('Hola mundo')
        assert resultado is True
        assert mock_post.call_count == 1

    def test_fallo_primer_intento_exito_segundo(self):
        """Primer intento falla, segundo tiene éxito."""
        responses = [_make_response(500), _make_response(200)]
        with patch('adapters.telegram.requests.post', side_effect=responses):
            with patch('adapters.telegram.time.sleep'):  # no esperar en tests
                resultado = enviar_telegram('Test retry')
        assert resultado is True

    def test_fallo_todos_los_intentos_retorna_false(self):
        """Tres intentos fallidos → retorna False."""
        with patch('adapters.telegram.requests.post',
                   return_value=_make_response(500)):
            with patch('adapters.telegram.time.sleep'):
                resultado = enviar_telegram('Test triple fallo')
        assert resultado is False

    def test_excepcion_de_red_reintenta_y_falla(self):
        """ConnectionError en cada intento → retorna False."""
        with patch('adapters.telegram.requests.post',
                   side_effect=ConnectionError('sin red')):
            with patch('adapters.telegram.time.sleep'):
                resultado = enviar_telegram('Test conexion')
        assert resultado is False

    def test_excepcion_primer_intento_exito_segundo(self):
        """Excepción en el primero, éxito en el segundo."""
        responses = [ConnectionError('timeout'), _make_response(200)]
        with patch('adapters.telegram.requests.post', side_effect=responses):
            with patch('adapters.telegram.time.sleep'):
                resultado = enviar_telegram('Test exception then ok')
        assert resultado is True

    def test_thread_id_incluido_en_payload(self):
        """Cuando se provee thread_id debe añadirse al payload."""
        with patch('adapters.telegram.requests.post',
                   return_value=_make_response(200)) as mock_post:
            enviar_telegram('Mensaje con thread', thread_id=42)
        call_kwargs = mock_post.call_args[1]
        payload = call_kwargs.get('json') or mock_post.call_args[0][1]
        assert payload.get('message_thread_id') == 42

    def test_sin_thread_id_no_incluye_campo(self):
        """Sin thread_id el campo message_thread_id no debe aparecer."""
        with patch('adapters.telegram.requests.post',
                   return_value=_make_response(200)) as mock_post:
            enviar_telegram('Mensaje sin thread')
        call_kwargs = mock_post.call_args[1]
        payload = call_kwargs.get('json') or mock_post.call_args[0][1]
        assert 'message_thread_id' not in payload

    def test_parse_mode_html(self):
        """El parse_mode siempre debe ser HTML."""
        with patch('adapters.telegram.requests.post',
                   return_value=_make_response(200)) as mock_post:
            enviar_telegram('Mensaje <b>bold</b>')
        call_kwargs = mock_post.call_args[1]
        payload = call_kwargs.get('json') or mock_post.call_args[0][1]
        assert payload.get('parse_mode') == 'HTML'

    def test_mensaje_incluido_en_payload(self):
        """El texto del mensaje debe llegar exactamente en el payload."""
        mensaje = '🔴 TEST — Gold 4H'
        with patch('adapters.telegram.requests.post',
                   return_value=_make_response(200)) as mock_post:
            enviar_telegram(mensaje)
        call_kwargs = mock_post.call_args[1]
        payload = call_kwargs.get('json') or mock_post.call_args[0][1]
        assert payload.get('text') == mensaje
