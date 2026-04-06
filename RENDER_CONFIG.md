# Configuración de Render para Logs

## ⚠️ Problema: Logs no aparecen en tiempo real

Render bufferiza la salida de Python por defecto, causando que los logs de los detectores y `/cron` no aparezcan inmediatamente.

## ✅ Solución: Configurar PYTHONUNBUFFERED

### Opción 1: Variable de entorno en Render (Recomendado)

1. Ve a tu servicio en Render: https://dashboard.render.com/
2. Click en tu servicio **"bottrading"**
3. Ve a **"Environment"** en el menú lateral
4. Click en **"Add Environment Variable"**
5. Agrega:
   - **Key**: `PYTHONUNBUFFERED`
   - **Value**: `1`
6. Click **"Save Changes"**
7. Render redespleará automáticamente

### Opción 2: Modificar comando de inicio

En la configuración del servicio en Render, cambia el comando de inicio de:
```
python app.py
```

A:
```
PYTHONUNBUFFERED=1 python app.py
```

## 📋 Variables de entorno necesarias

Tu servicio necesita estas variables configuradas en Render:

### Obligatorias (para que funcione):
- `TELEGRAM_TOKEN` - Token del bot de Telegram
- `TELEGRAM_CHAT_ID` - ID del chat para notificaciones

### Opcionales (para tracking en BD):
- `TURSO_DATABASE_URL` - URL de base de datos Turso
- `TURSO_AUTH_TOKEN` - Token de autenticación Turso

### Recomendada (para logs):
- `PYTHONUNBUFFERED=1` - Desactiva buffer de Python

## 🔍 Verificar que funciona

Después de agregar `PYTHONUNBUFFERED=1` y redesplegar, deberías ver en los logs:

```
[18:45:10] 🔔 CRON ping recibido - Threads vivos: 4/4
[18:46:47] 💚 Keep-alive ping OK
[18:47:47] 💚 Keep-alive ping OK
```

## ℹ️ Más información

El código también incluye `sys.stdout.reconfigure(line_buffering=True)` y `sys.stdout.flush()` como respaldo, pero la variable de entorno es la solución más limpia.
