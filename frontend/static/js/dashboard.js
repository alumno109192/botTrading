/**
 * BotTrading Dashboard — Lógica principal v2
 * Alpine.js component + Chart.js + polling cada 30s
 */

'use strict';

/* ══════════════════════════════════════════════════════
   CONSTANTES
══════════════════════════════════════════════════════ */
const POLL_INTERVAL_MS   = 30_000;
const TIMEFRAMES_ORDEN   = ['1W', '1D', '4H', '1H', '15M', '5M'];
const TOAST_DURACION_MS  = 6_000;

/* ══════════════════════════════════════════════════════
   ALPINE.JS DATA FUNCTION
══════════════════════════════════════════════════════ */
function dashboardApp() {
  return {
    /* ── Estado ── */
    vistaActual:         'activas',
    timeframes:          TIMEFRAMES_ORDEN,
    tfSeleccionados:     [...TIMEFRAMES_ORDEN],

    /* ── Datos ── */
    status:              { threads_vivos: 0, threads_totales: 0, evento_proximo: null },
    senalesActivas:      [],
    historial:           [],
    histDirFiltro:       'todos',   // 'todos' | 'COMPRA' | 'VENTA'
    statsGlobal:         {},
    statsPorTF:          [],
    precioActual:        null,

    /* ── UI ── */
    ultimaActualizacion: '',
    toasts:              [],
    _toastCounter:       0,
    _equityChart:        null,
    _equityPuntos:       [],   // guardamos para re-render al cambiar de pestaña

    /* ── Seguimiento ── */
    _idsConocidos:       new Set(),
    _estadosConocidos:   {},

    /* ══════════════════════════════════════════
       INIT
    ══════════════════════════════════════════ */
    init() {
      this.cargarTodo();
      setInterval(() => this.cargarTodo(), POLL_INTERVAL_MS);

      // Re-renderizar gráfico cuando el usuario vuelve a Stats
      this.$watch('vistaActual', v => {
        if (v === 'stats' && this._equityPuntos.length > 0) {
          this.$nextTick(() => this.renderEquityChart(this._equityPuntos));
        }
      });
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
        if (r.ok) this.status = await r.json();
      } catch (_) {}
    },

    async cargarActivas() {
      try {
        const r = await fetch('/api/v1/senales/activas');
        if (!r.ok) return;
        const data = await r.json();
        const nuevas = data.senales || [];

        nuevas.forEach(s => {
          if (!this._idsConocidos.has(s.id)) {
            if (this._idsConocidos.size > 0) {
              this.toast('nueva',
                `Nueva señal ${s.direccion === 'COMPRA' ? '▲ BUY' : '▼ SELL'}`,
                `${s.asset || s.simbolo} ${s.timeframe} @ ${this.formatPrecio(s.precio_entrada)}`
              );
            }
            this._idsConocidos.add(s.id);
          }
        });

        const expandidos = new Set(
          this.senalesActivas.filter(s => s._expanded).map(s => s.id)
        );

        this.senalesActivas = nuevas.map(s => ({
          ...s,
          _expanded:     expandidos.has(s.id),
          tp1_alcanzado: _tpAlcanzado(s, 'tp1'),
          tp2_alcanzado: _tpAlcanzado(s, 'tp2'),
          tp3_alcanzado: _tpAlcanzado(s, 'tp3'),
        }));
      } catch (_) {}
    },

    async cargarHistorial() {
      try {
        const r = await fetch('/api/v1/senales/historial?horas=168&limit=50');
        if (!r.ok) return;
        const data = await r.json();

        (data.senales || []).forEach(s => {
          const prev = this._estadosConocidos[s.id];
          if (prev && prev !== s.estado) {
            if (['TP1','TP2','TP3'].includes(s.estado)) {
              this.toast('tp', `✅ ${s.estado} alcanzado`,
                `${s.asset || s.simbolo} ${s.timeframe} | ${this.formatPnl(s.beneficio_final_pct)}`);
            } else if (s.estado === 'SL') {
              this.toast('sl', `🛑 Stop Loss activado`,
                `${s.asset || s.simbolo} ${s.timeframe} | ${this.formatPnl(s.beneficio_final_pct)}`);
            }
          }
          this._estadosConocidos[s.id] = s.estado;
        });

        this.historial = data.senales || [];
      } catch (_) {}
    },

    async cargarStats() {
      try {
        const [rG, rTF, rEq] = await Promise.all([
          fetch('/api/v1/stats/global'),
          fetch('/api/v1/stats/por_tf'),
          fetch('/api/v1/stats/equity_curve'),
        ]);
        if (rG.ok)  this.statsGlobal = await rG.json();
        if (rTF.ok) this.statsPorTF  = (await rTF.json()).por_timeframe || [];
        if (rEq.ok) {
          const eq = await rEq.json();
          this._equityPuntos = eq.puntos || [];
          // Solo renderizar si el canvas ya existe en el DOM
          if (this.vistaActual === 'stats') {
            this.$nextTick(() => this.renderEquityChart(this._equityPuntos));
          }
        }
      } catch (_) {}
    },

    async cargarPrecio(symbol) {
      try {
        const r = await fetch(`/api/v1/precio/${symbol}`);
        if (r.ok) { const d = await r.json(); this.precioActual = d.precio; }
      } catch (_) {}
    },

    /* ══════════════════════════════════════════
       GETTERS REACTIVOS
    ══════════════════════════════════════════ */
    get senalesFiltradas() {
      if (!this.tfSeleccionados.length) return this.senalesActivas;
      return this.senalesActivas.filter(s => this.tfSeleccionados.includes(s.timeframe));
    },

    get historialFiltrado() {
      let h = this.historial;
      if (this.tfSeleccionados.length)
        h = h.filter(s => this.tfSeleccionados.includes(s.timeframe));
      if (this.histDirFiltro !== 'todos')
        h = h.filter(s => s.direccion === this.histDirFiltro);
      return h;
    },

    /* ══════════════════════════════════════════
       UI HELPERS
    ══════════════════════════════════════════ */
    toggleTF(tf) {
      this.tfSeleccionados = this.tfSeleccionados.includes(tf)
        ? this.tfSeleccionados.filter(t => t !== tf)
        : [...this.tfSeleccionados, tf];
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
      return `${n >= 0 ? '+' : ''}${n.toFixed(3)}%`;
    },

    formatFecha(ts) {
      if (!ts) return '—';
      try {
        const d = new Date(ts.includes('T') ? ts : ts.replace(' ', 'T'));
        return d.toLocaleString('es-ES', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
      } catch (_) { return ts; }
    },

    duracion(ts) {
      if (!ts) return '—';
      try {
        const ms = Date.now() - new Date((ts.includes('T') ? ts : ts.replace(' ', 'T'))).getTime();
        const h  = Math.floor(ms / 3_600_000);
        const m  = Math.floor((ms % 3_600_000) / 60_000);
        return h > 0 ? `${h}h ${m}m` : `${m}m`;
      } catch (_) { return '—'; }
    },

    estadoLabel(estado) {
      const L = { TP1:'TP1 ✅', TP2:'TP2 ✅', TP3:'TP3 ✅', SL:'SL ❌', CANCELADA:'CANCEL.', CADUCADA:'CADUC.' };
      return L[estado] || estado;
    },

    /* ══════════════════════════════════════════
       TOAST SYSTEM
    ══════════════════════════════════════════ */
    toast(tipo, titulo, mensaje) {
      const id = ++this._toastCounter;
      this.toasts.push({ id, tipo, titulo, mensaje, visible: true });
      _browserNotify(titulo, mensaje);
      setTimeout(() => {
        const idx = this.toasts.findIndex(t => t.id === id);
        this.cerrarToast(idx);
      }, TOAST_DURACION_MS);
    },

    cerrarToast(idx) {
      if (idx < 0 || idx >= this.toasts.length) return;
      this.toasts[idx].visible = false;
      setTimeout(() => { this.toasts = this.toasts.filter((_, i) => i !== idx); }, 300);
    },

    /* ══════════════════════════════════════════
       EQUITY CHART
    ══════════════════════════════════════════ */
    renderEquityChart(puntos) {
      const canvas = document.getElementById('equityChart');
      if (!canvas) return;

      const labels = puntos.map(p => p.fecha);
      const values = puntos.map(p => p.pnl_acumulado || 0);
      const buyColor  = 'rgba(0, 230, 118, 0.9)';
      const sellColor = 'rgba(255, 23, 68, 0.9)';

      // Gradient
      let bgGradient = 'rgba(0,230,118,0.05)';
      try {
        const ctx = canvas.getContext('2d');
        const g   = ctx.createLinearGradient(0, 0, 0, canvas.offsetHeight || 200);
        g.addColorStop(0,   'rgba(0,230,118,0.15)');
        g.addColorStop(0.5, 'rgba(0,230,118,0.04)');
        g.addColorStop(1,   'rgba(0,230,118,0)');
        bgGradient = g;
      } catch (_) {}

      if (this._equityChart) {
        this._equityChart.data.labels           = labels;
        this._equityChart.data.datasets[0].data = values;
        this._equityChart.update('none');
        return;
      }

      this._equityChart = new Chart(canvas, {
        type: 'line',
        data: {
          labels,
          datasets: [{
            label:           'P&L %',
            data:            values,
            borderColor:     buyColor,
            borderWidth:     2,
            pointRadius:     puntos.length > 20 ? 0 : 3,
            pointHoverRadius: 5,
            pointBackgroundColor: buyColor,
            fill:            true,
            backgroundColor: bgGradient,
            tension:         0.4,
            segment: {
              borderColor: ctx => ctx.p1.parsed.y >= 0 ? buyColor : sellColor,
            },
          }],
        },
        options: {
          responsive:            true,
          maintainAspectRatio:   false,
          interaction:           { intersect: false, mode: 'index' },
          plugins: {
            legend: { display: false },
            tooltip: {
              backgroundColor: '#111827',
              borderColor:     '#1e2d3d',
              borderWidth:     1,
              titleColor:      '#546e7a',
              bodyColor:       '#cdd9e5',
              padding:         10,
              callbacks: {
                label: ctx => ` ${ctx.parsed.y >= 0 ? '+' : ''}${ctx.parsed.y.toFixed(3)}%`,
              },
            },
          },
          scales: {
            x: {
              grid:  { color: 'rgba(30,45,61,0.6)' },
              ticks: { color: '#546e7a', font: { size: 10, family: 'JetBrains Mono, monospace' } },
            },
            y: {
              grid:  { color: 'rgba(30,45,61,0.6)' },
              ticks: {
                color: '#546e7a',
                font:  { size: 10, family: 'JetBrains Mono, monospace' },
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
   UTILIDADES PRIVADAS
══════════════════════════════════════════════════════ */
function _tpAlcanzado(s, campo) {
  const tp = s[campo], actual = s.precio_actual, entrada = s.precio_entrada;
  if (!tp || !actual || !entrada) return false;
  return s.direccion === 'COMPRA' ? actual >= tp : actual <= tp;
}

let _notifPermiso = (typeof Notification !== 'undefined') ? Notification.permission : 'denied';

function _browserNotify(titulo, cuerpo) {
  if (typeof Notification === 'undefined' || _notifPermiso === 'denied') return;
  if (_notifPermiso === 'default') {
    Notification.requestPermission().then(p => {
      _notifPermiso = p;
      if (p === 'granted') new Notification(titulo, { body: cuerpo });
    });
    return;
  }
  try { new Notification(titulo, { body: cuerpo }); } catch (_) {}
}

