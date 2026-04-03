# Bot Trading - TradingView a Telegram

## Descripción del Proyecto

Bot Flask que recibe señales de trading desde TradingView mediante webhook y las envía formateadas a un chat de Telegram.

## Estructura del Proyecto

- `app.py` - Aplicación Flask principal con endpoint webhook
- `requirements.txt` - Dependencias Python
- `.env.example` - Plantilla de variables de entorno
- `README.md` - Documentación completa del proyecto

## Configuración

1. Crear archivo `.env` basado en `.env.example`
2. Obtener token de bot de Telegram desde @BotFather
3. Configurar variables de entorno con token y chat ID

## Ejecución

Para ejecutar el proyecto:
```bash
.\venv\Scripts\python.exe app.py
```

El servidor estará disponible en `http://0.0.0.0:5000`

## Estado del Proyecto

- [x] Proyecto scaffolded correctamente
- [x] Dependencias instaladas (Flask 3.0.2, requests 2.31.0, python-dotenv 1.0.1)
- [x] Sin errores de compilación
- [x] Documentación completa en README.md
