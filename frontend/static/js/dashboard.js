/**
 * BotTrading Dashboard — Lógica principal
 * Alpine.js component + Chart.js + polling cada 30s
 */

'use strict';

/* ══════════════════════════════════════════════════════
   CONSTANTES
══════════════════════════════════════════════════════ */
const POLL_INTERVAL_MS   = 30_000;   // 30 segundos
const TIMEFRAMES_ORDEN   = ['1W', '1D', '4H', '1H', '15M', '5M'];
const TOAST_DURACION_MS  = 6_000;

/* ══════════════════════════════════════════════════════
   ALPINE.JS DATA FUNCTION
══════════════════════════════════════════════════════ */
function dashboardApp() {
  return {
    /* ── Estado ── */
    vistaActual:      'activas',
    timeframes:       TIMEFRAMES_ORDEN,
    tfSeleccionados:  [...TIMEFRAMES_ORDEN],

    /* ── Datos ── */
    status:           { threads_vivos: 0, threads_totales: 0, evento_proximo: null },
    senalesActivas:   [],
    historial:        [],
    statsGlobal:      {},
    statsPorTF:       [],
    precioActual:     null,

    /* ── UI ── */
    ultimaActualizacion: '—',
    toasts:           [],
    _toastCounter:    0,
    _equityChart:     null,

    /* ── IDs conocidos para detectar nuevas señales ── */
    _idsConocidos:    new Set(),
    _estadosConocidos: {},   // id → estado (para detectar TP alcanzado)

    /* ══════════════════════════════════════════
       INIT
    ══════════════════════════════════════════ */
    init() {
      this.cargarTodo();
      setInterval(() => this.cargarTodo(), POLL_INTERVAL_MS);
    },

    /* ══════════════════════════════════════════
       CARGA DE DATOS
    ══════════════════════════════════════════ */
    async cargarTodo() {
      await Promise.allSettled([
        this.cargarStatus(),
        this.cargarActivas(),
        this.cargarHistorial(),
        this.cargarStats(),
        this.cargarPrecio('XAUUSD'),
      ]);
      this.ultimaActualizacion = new Date().toLocaleTimeString('es-ES', {
        hour: '2-digit', minute: '2-digit', second: '2-digit'
      });
    },

    async cargarStatus() {
      try {
        const r = await fetch('/api/v1/status');
        if (!r.ok) return;
        this.status = await r.json();
      } catch (_) { /* ignora errores de red */ }
    },

    async cargarActivas() {
      try {
        const r = await fetch('/api/v1/senales/activas');
        if (!r.ok) return;
        const data = await r.json();
        const nuevas = data.senales || [];

        // Detectar nuevas señales
        nuevas.forEach(s => {
          if (!this._idsConocidos.has(s.id)) {
            if (this._idsConocidos.size > 0) {  // no notificar en la primera carga
              this.toast('nueva',
                `Nueva señal ${s.direccion === 'COMPRA' ? 'BUY 📈' : 'SELL 📉'}`,
                `${s.asset || s.simbolo} ${s.timeframe} @ ${this.formatPrecio(s.precio_entrada)}`
              );
            }
            this._idsConocidos.add(s.id);
          }
        });

        // Preservar estado _expanded entre polls
        const expandidos = new Set(
          this.senalesActivas.filter(s => s._expanded).map(s => s.id)
        );

        // Marcar TPs alcanzados (precio_actual cruza el nivel)
        this.senalesActivas = nuevas.map(s => ({
          ...s,
          _expanded: expandidos.has(s.id),
          tp1_alcanzado: _tpAlcanzado(s, 'tp1'),
          tp2_alcanzado: _tpAlcanzado(s, 'tp2'),
          tp3_alcanzado: _tpAlcanzado(s, 'tp3'),
        }));

      } catch (_) { /* ignora errores de red */ }
    },

    async cargarHistorial() {
      try {
        const r = await fetch('/api/v1/senales/historial?horas=168&limit=50');
        if (!r.ok) return;
        const data = await r.json();

        // Detectar TPs alcanzados desde la última poll (señal pasó de ACTIVA a TP1/TP2/TP3)
        (data.senales || []).forEach(s => {
          const estadoAnterior = this._estadosConocidos[s.id];
          if (estadoAnterior && estadoAnterior === 'ACTIVA' && ['TP1','TP2','TP3'].includes(s.estado)) {
            this.toast('tp',
              `✅ TP alcanzado — ${s.estado}`,
              `${s.asset || s.simbolo} ${s.timeframe} | PnL: ${this.formatPnl(s.beneficio_final_pct)}`
            );
          }
          if (['SL'].includes(s.estado) && estadoAnterior === 'ACTIVA') {
            this.toast('sl',
              `🛑 Stop Loss activado`,
              `${s.asset || s.simbolo} ${s.timeframe} | PnL: ${this.formatPnl(s.beneficio_final_pct)}`
            );
          }
          this._estadosConocidos[s.id] = s.estado;
        });

        this.historial = data.senales || [];
      } catch (_) { /* ignora errores de red */ }
    },

    async cargarStats() {
      try {
        const [rGlobal, rTF, rEquity] = await Promise.all([
          fetch('/api/v1/stats/global'),
          fetch('/api/v1/stats/por_tf'),
          fetch('/api/v1/stats/equity_curve'),
        ]);
        if (rGlobal.ok)   this.statsGlobal = await rGlobal.json();
        if (rTF.ok)       this.statsPorTF  = (await rTF.json()).por_timeframe || [];
        if (rEquity.ok) {
          const eq = await rEquity.json();
          this.renderEquityChart(eq.puntos || []);
        }
      } catch (_) { /* ignora errores de red */ }
    },

    async cargarPrecio(symbol) {
      try {
        const r = await fetch(`/api/v1/precio/${symbol}`);
        if (!r.ok) return;
        const data = await r.json();
        this.precioActual = data.precio;
      } catch (_) { /* ignora errores de red */ }
    },

    /* ══════════════════════════════════════════
       COMPUTED (getters reactivos Alpine)
    ══════════════════════════════════════════ */
    get senalesFiltradas() {
      if (this.tfSeleccionados.length === 0) return this.senalesActivas;
      return this.senalesActivas.filter(s => this.tfSeleccionados.includes(s.timeframe));
    },

    get historialFiltrado() {
      if (this.tfSeleccionados.length === 0) return this.historial;
      return this.historial.filter(s => this.tfSeleccionados.includes(s.timeframe));
    },

    /* ══════════════════════════════════════════
       UI HELPERS
    ══════════════════════════════════════════ */
    toggleTF(tf) {
      if (this.tfSeleccionados.includes(tf)) {
        this.tfSeleccionados = this.tfSeleccionados.filter(t => t !== tf);
      } else {
        this.tfSeleccionados = [...this.tfSeleccionados, tf];
      }
    },

    /* ══════════════════════════════════════════
       FORMATTERS
    ══════════════════════════════════════════ */
    formatPrecio(v) {
      if (v == null) return '—';
      const n = Number(v);
      if (isNaN(n)) return '—';
      return n >= 1000
        ? n.toLocaleString('es-ES', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
        : n.toFixed(5);
    },

    formatPnl(v) {
      if (v == null) return '—';
      const n = Number(v);
      if (isNaN(n)) return '—';
      const sign = n >= 0 ? '+' : '';
      return `${sign}${n.toFixed(3)}%`;
    },

    formatFecha(ts) {
      if (!ts) return '—';
      try {
        const d = new Date(ts.replace(' ', 'T'));
        return d.toLocaleString('es-ES', {
          month: '2-digit', day: '2-digit',
          hour: '2-digit', minute: '2-digit',
        });
      } catch (_) { return ts; }
    },

    duracion(ts) {
      if (!ts) return '—';
      try {
        const inicio = new Date(ts.replace(' ', 'T'));
        const diff   = Date.now() - inicio.getTime();
        const h      = Math.floor(diff / 3_600_000);
        const m      = Math.floor((diff % 3_600_000) / 60_000);
        if (h > 0) return `${h}h ${m}m`;
        return `${m}m`;
      } catch (_) { return '—'; }
    },

    nivelClass(nivel) {
      const map = {
        'MAXIMA': 'nivel-maxima',
        'FUERTE': 'nivel-fuerte',
        'MEDIA':  'nivel-media',
        'ALERTA': 'nivel-alerta',
      };
      return map[nivel] || 'border border-border text-muted';
    },

    estadoClass(estado) {
      if (['TP1','TP2','TP3'].includes(estado)) return 'bg-buy/20 text-buy';
      if (estado === 'SL') return 'bg-sell/20 text-sell';
      return 'bg-border/40 text-muted';
    },

    estadoLabel(estado) {
      const labels = { TP1: 'TP1 ✅', TP2: 'TP2 ✅', TP3: 'TP3 ✅', SL: 'SL ❌',
                       CANCELADA: 'CANCEL.', CADUCADA: 'CADUC.' };
      return labels[estado] || estado;
    },

    /* ══════════════════════════════════════════
       TOAST SYSTEM
    ══════════════════════════════════════════ */
    toast(tipo, titulo, mensaje) {
      const id = ++this._toastCounter;
      this.toasts.push({ id, tipo, titulo, mensaje, visible: true });

      // Disparar también una notificación del navegador si está permitida
      _browserNotify(titulo, mensaje);

      setTimeout(() => this.cerrarToast(this.toasts.findIndex(t => t.id === id)), TOAST_DURACION_MS);
    },

    cerrarToast(idx) {
      if (idx < 0 || idx >= this.toasts.length) return;
      this.toasts[idx].visible = false;
      setTimeout(() => {
        this.toasts = this.toasts.filter((_, i) => i !== idx);
      }, 300);
    },

    /* ══════════════════════════════════════════
       CHART.JS — EQUITY CURVE
    ══════════════════════════════════════════ */
    renderEquityChart(puntos) {
      const canvas = document.getElementById('equityChart');
      if (!canvas) return;

      const labels = puntos.map(p => p.fecha);
      const values = puntos.map(p => p.pnl_acumulado || 0);
      const positiveColor = 'rgba(0, 210, 106, 0.8)';
      const negativeColor = 'rgba(255, 77, 77, 0.8)';

      const gradientPositive = _buildGradient(canvas, '#00d26a', 'rgba(0,210,106,0)');

      if (this._equityChart) {
        this._equityChart.data.labels = labels;
        this._equityChart.data.datasets[0].data = values;
        this._equityChart.update('none');
        return;
      }

      this._equityChart = new Chart(canvas, {
        type: 'line',
        data: {
          labels,
          datasets: [{
            label: 'P&L Acumulado %',
            data: values,
            borderColor: positiveColor,
            borderWidth: 2,
            pointRadius: puntos.length > 20 ? 0 : 3,
            pointHoverRadius: 5,
            pointBackgroundColor: positiveColor,
            fill: true,
            backgroundColor: gradientPositive || 'rgba(0,210,106,0.1)',
            tension: 0.3,
            segment: {
              borderColor: ctx =>
                ctx.p1.parsed.y >= 0 ? positiveColor : negativeColor,
            },
          }],
        },
        options: {
          responsive: true,
          maintainAspectRatio: true,
          interaction: { intersect: false, mode: 'index' },
          plugins: {
            legend: { display: false },
            tooltip: {
              backgroundColor: '#161b22',
              borderColor: '#30363d',
              borderWidth: 1,
              titleColor: '#8b949e',
              bodyColor: '#e6edf3',
              callbacks: {
                label: ctx => ` ${ctx.parsed.y >= 0 ? '+' : ''}${ctx.parsed.y.toFixed(3)}%`,
              },
            },
          },
          scales: {
            x: {
              grid:   { color: '#30363d' },
              ticks:  { color: '#8b949e', font: { size: 10, family: 'monospace' } },
            },
            y: {
              grid:   { color: '#30363d' },
              ticks:  {
                color: '#8b949e',
                font: { size: 10, family: 'monospace' },
                callback: v => `${v >= 0 ? '+' : ''}${v.toFixed(2)}%`,
              },
            },
          },
        },
      });
    },
  };
}

/* ══════════════════════════════════════════════════════
   UTILIDADES PRIVADAS (fuera del componente Alpine)
══════════════════════════════════════════════════════ */

/**
 * Determina si un TP está "alcanzado" en función del precio actual vs la dirección.
 */
function _tpAlcanzado(s, campo) {
  const tp      = s[campo];
  const actual  = s.precio_actual;
  const entrada = s.precio_entrada;
  if (!tp || !actual || !entrada) return false;
  if (s.direccion === 'COMPRA') return actual >= tp;
  if (s.direccion === 'VENTA')  return actual <= tp;
  return false;
}

/**
 * Construye un gradiente vertical para el gráfico de Chart.js.
 */
function _buildGradient(canvas, colorTop, colorBottom) {
  try {
    const ctx = canvas.getContext('2d');
    const gradient = ctx.createLinearGradient(0, 0, 0, canvas.offsetHeight || 220);
    gradient.addColorStop(0,   colorTop);
    gradient.addColorStop(1,   colorBottom);
    return gradient;
  } catch (_) {
    return null;
  }
}

/**
 * Notificación del navegador (requiere permiso del usuario).
 */
let _notifPermiso = Notification.permission;

function _browserNotify(titulo, cuerpo) {
  if (_notifPermiso === 'denied') return;
  if (_notifPermiso === 'default') {
    Notification.requestPermission().then(p => {
      _notifPermiso = p;
      if (p === 'granted') new Notification(titulo, { body: cuerpo, icon: '/static/icon.png' });
    });
    return;
  }
  try {
    new Notification(titulo, { body: cuerpo, icon: '/static/icon.png' });
  } catch (_) { /* Safari puede fallar */ }
}
