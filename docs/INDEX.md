# 📚 Documentación Bot Trading

**Índice Maestro** — Toda la documentación técnica organizada por tema.

---

## 🚀 Inicio Rápido

**Nueva en el proyecto?** Empieza aquí:

1. [**GUIA_INICIO.md**](GUIA_INICIO.md) — Instalación, configuración, ejecución
2. [**README.md**](README.md) — Overview de la aplicación

---

## 🏗️ Arquitectura & Diseño

| Documento | Propósito |
|-----------|-----------|
| [**ARQUITECTURA.md**](ARQUITECTURA.md) | Diseño del sistema, threads, flujo de datos |
| [**TIMEFRAMES_MULTIPLES.md**](TIMEFRAMES_MULTIPLES.md) | Multi-timeframe confluence, sesgo biased |
| [**CONFIGURACION_INTERVALOS.md**](CONFIGURACION_INTERVALOS.md) | Parámetros por timeframe (1D/4H/1H/15M/5M) |

---

## 📊 Indicadores Técnicos

| Documento | Propósito |
|-----------|-----------|
| [**INDICADORES.md**](INDICADORES.md) | RSI, ATR, ADX, Bollinger, MACD, OBV, patrones |
| [**detectors/README.md**](../detectors/README.md) | Estructura de detectors y archivos |

---

## 🔧 Desarrollo

| Documento | Propósito |
|-----------|-----------|
| [**DETECTORS.md**](DETECTORS.md) | Cómo crear un nuevo detector |
| [**TRACKING.md**](TRACKING.md) | Sistema de seguimiento de señales (TP/SL) |
| [**AUDITORIA.md**](AUDITORIA.md) | **[RECIENTE]** Auditoría v2 técnica — 22 fixes |

---

## 📈 Operación & Monitoreo

| Documento | Propósito |
|-----------|-----------|
| [**RENDER_CONFIG.md**](RENDER_CONFIG.md) | Configuración en Render (producción) |
| [**ERRORES.md**](ERRORES.md) | Troubleshooting común |
| [**PENDIENTES.md**](PENDIENTES.md) | Roadmap y tareas futuras |

---

## 📝 Historial

- [**HISTORIAL.md**](HISTORIAL.md) — Wiki histórico del proyecto (evolución, cambios pasados)

---

## 🎯 Flujo Típico de Trabajo

```
┌─ Auditoría: leer AUDITORIA.md
├─ Entender arquitectura: ARQUITECTURA.md
├─ Modificar detector: DETECTORS.md + INDICADORES.md
├─ Probar en local: GUIA_INICIO.md
├─ Deploy: RENDER_CONFIG.md
└─ Troubleshoot: ERRORES.md
```

---

## 📊 Contenido por Documento

### Archivos Consolidados ✅

| Archivo | Contiene | Tamaño |
|---------|----------|--------|
| GUIA_INICIO.md | Setup, instalación, ejecución, .env, primeros pasos | ~10KB |
| ARQUITECTURA.md | Diseño sistema, threads, contexto, flujo datos | ~40KB |
| INDICADORES.md | Técnicas, fórmulas, patrones velas | ~40KB |
| DETECTORS.md | Template detector, ejemplos, timeframes | ~33KB |
| TRACKING.md | Monitor señales, TP/SL, histórico precios | ~37KB |
| AUDITORIA.md | Hallazgos técnicos, deuda, fixes | 15KB |
| RENDER_CONFIG.md | Secrets, variables, deployment | 2.5KB |
| ERRORES.md | Troubleshooting, errores comunes | 2.3KB |
| PENDIENTES.md | Roadmap vivo, tareas | 22KB |

**Total documentación útil: ~203 KB**

---

## 🗑️ Archivos Eliminados (Histórico)

- ~~PLAN_MEJORA_Y_APP.md~~ → reemplazado por AUDITORIA.md
- ~~PROXIMOS_PASOS.md~~ → consolidado en PENDIENTES.md
- ~~INTEGRACION_4H.md~~ → contenido en ARQUITECTURA.md
- ~~INTEGRACION_COMPLETA.md~~ → contenido en ARQUITECTURA.md
- ~~INSTALACION_TRACKING.md~~ → consolidado en TRACKING.md
- ~~EJECUTAR.md~~ → consolidado en GUIA_INICIO.md

---

## 💡 Dónde Buscar

**"¿Cómo instalo?"**
→ [GUIA_INICIO.md](GUIA_INICIO.md)

**"¿Cómo funciona el sistema?"**
→ [ARQUITECTURA.md](ARQUITECTURA.md)

**"¿Cómo creo un nuevo detector?"**
→ [DETECTORS.md](DETECTORS.md)

**"¿Qué indicadores hay disponibles?"**
→ [INDICADORES.md](INDICADORES.md)

**"¿Cómo debo hacer deploy?"**
→ [RENDER_CONFIG.md](RENDER_CONFIG.md)

**"Tengo un error, ¿cómo lo arreglo?"**
→ [ERRORES.md](ERRORES.md)

**"¿Qué hay que arreglar/mejorar?"**
→ [PENDIENTES.md](PENDIENTES.md) o [AUDITORIA.md](AUDITORIA.md)

---

*Última actualización: 2026-04-18 (Consolidación de documentación)*
