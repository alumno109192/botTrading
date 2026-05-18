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
    scores:              [],        // [{simbolo, tf, score_sell, score_buy, max_score, ts}]

    /* ── UI ── */
    menuAbierto:         false,
    ultimaActualizacion: '',
    toasts:              [],
    _toastCounter:       0,
    _equityChart:        null,
    _equityPuntos:       [],   // guardamos para re-render al cambiar de pestaña

    /* ── Push ── */
    pushEstado:          'desconocido',  // 'desconocido' | 'activo' | 'inactivo' | 'no-soportado'
    pushBateriaAviso:   false,           // true → muestra banner instrucciones batería Android

    /* ── Seguimiento ── */
    _idsConocidos:       new Set(),
    _estadosConocidos:   {},

    /* ── SSE ── */
    _sse:                null,           // EventSource activo
    init(vistaInicial = 'activas') {
      this.vistaActual = vistaInicial;
      _solicitarNotifPermiso();
      this.initPush();
      this.cargarTodo();
      setInterval(() => this.cargarTodo(), POLL_INTERVAL_MS);
      this.initSSE();

      // Re-renderizar gráfico cuando el usuario vuelve a Stats
      this.$watch('vistaActual', v => {
        if (v === 'stats' && this._equityPuntos.length > 0) {
          this.$nextTick(() => this.renderEquityChart(this._equityPuntos));
        }
      });
    },

    /* ══════════════════════════════════════════
       SERVER-SENT EVENTS
    ══════════════════════════════════════════ */
    initSSE() {
      if (!window.EventSource) return;
      const conectar = () => {
        if (this._sse) this._sse.close();
        const es = new EventSource('/api/v1/events');
        this._sse = es;

        // Evento de señal: nueva señal o actualización de estado
        es.addEventListener('senal', (e) => {
          try {
            const d = JSON.parse(e.data);
            const tipo = d.tipo || 'actualizacion';

            // Pre-registrar el ID antes de cargarActivas() para evitar
            // que ésta muestre un segundo toast por el mismo evento.
            if (d.senal_id) this._idsConocidos.add(d.senal_id);

            if (tipo === 'nueva') {
              const dir = d.direccion === 'COMPRA' ? '▲ BUY' : '▼ SELL';
              this.toast('nueva',
                `🔔 Nueva señal ${dir}`,
                `${d.simbolo} ${d.timeframe} @ ${this.formatPrecio(d.precio_entrada)}`,
                '/dashboard/activas'
              );
            } else if (['tp1','tp2','tp3'].includes(tipo)) {
              this.toast('tp',
                `✅ ${tipo.toUpperCase()} alcanzado`,
                `${d.simbolo} ${d.timeframe}`
              );
            } else if (tipo === 'sl') {
              this.toast('sl',
                `🛑 Stop Loss activado`,
                `${d.simbolo} ${d.timeframe}`
              );
            }
            // Recargar señales activas para reflejar el nuevo estado
            this.cargarActivas();
            this.cargarHistorial();
          } catch (_) {}
        });

        // Evento de precio: actualización en tiempo real
        es.addEventListener('precio', (e) => {
          try {
            const d = JSON.parse(e.data);
            if (d.symbol === 'XAUUSD' && d.precio != null) {
              this.precioActual = d.precio;
            }
          } catch (_) {}
        });

        es.onerror = () => {
          es.close();
          // Reconectar tras 5 segundos si la conexión se pierde
          setTimeout(() => conectar(), 5000);
        };
      };
      conectar();
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
        this.cargarScores(),
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
                `${s.asset || s.simbolo} ${s.timeframe} @ ${this.formatPrecio(s.precio_entrada)}`,
                `/dashboard/activas#s${s.id}`
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
          _bar:          _calcBar(s),
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

    async cargarScores() {
      try {
        const r = await fetch('/api/v1/scores');
        if (r.ok) { const d = await r.json(); this.scores = d.scores || []; }
      } catch (_) {}
    },

    /* ══════════════════════════════════════════
       GETTERS REACTIVOS
    ══════════════════════════════════════════ */
    get senalesFiltradas() {
      // La pestaña Activas muestra ACTIVA + PENDIENTE_CONFIRM, nunca ESPERANDO
      const activas = this.senalesActivas.filter(s => s.estado !== 'ESPERANDO');
      if (!this.tfSeleccionados.length) return activas;
      return activas.filter(s => this.tfSeleccionados.includes(s.timeframe));
    },

    get senalesPendientes() {
      // Pendientes = señales BUY/SELL LIMIT esperando que el precio llegue a la entrada
      return this.senalesActivas.filter(s => s.estado === 'ESPERANDO');
    },

    get senalesActivasCount() {
      // Cuenta solo ACTIVA + PENDIENTE_CONFIRM (excluye ESPERANDO que va a su propia pestaña)
      return this.senalesActivas.filter(s => s.estado !== 'ESPERANDO').length;
    },

    get senalesPendientesFiltradas() {
      if (!this.tfSeleccionados.length) return this.senalesPendientes;
      return this.senalesPendientes.filter(s => this.tfSeleccionados.includes(s.timeframe));
    },

    get scoresPorSimbolo() {
      const map = {};
      for (const s of this.scores) {
        if (!map[s.simbolo]) map[s.simbolo] = { simbolo: s.simbolo, items: [] };
        map[s.simbolo].items.push(s);
      }
      return Object.values(map);
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
    toast(tipo, titulo, mensaje, url = '/dashboard/activas') {
      const id = ++this._toastCounter;
      this.toasts.push({ id, tipo, titulo, mensaje, url, visible: true });
      _browserNotify(titulo, mensaje, url);
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

    /* ══════════════════════════════════════════
       WEB PUSH NOTIFICATIONS
    ══════════════════════════════════════════ */

    /** Inicializa el estado del botón push leyendo la suscripción activa. */
    async initPush() {
      if (!('serviceWorker' in navigator) || !('PushManager' in window)) {
        this.pushEstado = 'no-soportado';
        return;
      }
      try {
        const reg = await navigator.serviceWorker.register('/sw.js', { scope: '/' });
        await navigator.serviceWorker.ready;
        const sub = await reg.pushManager.getSubscription();
        this.pushEstado = sub ? 'activo' : 'inactivo';
      } catch (e) {
        console.warn('Push init error:', e);
        this.pushEstado = 'inactivo';
      }
    },

    /** Activa o desactiva las notificaciones push al hacer clic en el botón. */
    async togglePushNotif() {
      if (this.pushEstado === 'no-soportado') return;

      if (this.pushEstado === 'activo') {
        await this._desactivarPush();
      } else {
        await this._activarPush();
      }
    },

    async _activarPush() {
      try {
        const permiso = await Notification.requestPermission();
        if (permiso !== 'granted') {
          this.mostrarToast('⚠️ Notificaciones bloqueadas', 'Actívalas en los ajustes del navegador', 'warn');
          return;
        }
        // Obtener clave pública VAPID del servidor
        const r = await fetch('/api/v1/push/vapid-public-key');
        if (!r.ok) { this.mostrarToast('❌ Error', 'Servidor no devolvió clave VAPID', 'error'); return; }
        const { publicKey } = await r.json();

        const reg = await navigator.serviceWorker.ready;
        const sub = await reg.pushManager.subscribe({
          userVisibleOnly: true,
          applicationServerKey: _urlBase64ToUint8Array(publicKey),
        });

        // Guardar suscripción en el servidor
        const save = await fetch('/api/v1/push/subscribe', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(sub.toJSON()),
        });
        if (save.ok) {
          this.pushEstado = 'activo';
          this.mostrarToast('🔔 Notificaciones activadas', 'Recibirás alertas de nuevas señales', 'ok');
          // En Android mostrar banner con instrucciones de batería (solo la primera vez)
          if (/android/i.test(navigator.userAgent)) {
            this.pushBateriaAviso = true;
          }
        } else {
          this.mostrarToast('❌ Error al guardar suscripción', '', 'error');
        }
      } catch (e) {
        console.error('Push subscribe error:', e);
        this.mostrarToast('❌ Error activando push', e.message, 'error');
      }
    },

    async _desactivarPush() {
      try {
        const reg = await navigator.serviceWorker.ready;
        const sub = await reg.pushManager.getSubscription();
        if (sub) {
          await fetch('/api/v1/push/unsubscribe', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ endpoint: sub.endpoint }),
          });
          await sub.unsubscribe();
        }
        this.pushEstado = 'inactivo';
        this.mostrarToast('🔕 Notificaciones desactivadas', '', 'warn');
      } catch (e) {
        console.error('Push unsubscribe error:', e);
      }
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

/**
 * Calcula la posición de la barra SL → TP.
 * SL siempre en 0 % (izquierda), el TP más alejado en 100 % (derecha).
 * Funciona igual para COMPRA y VENTA.
 */
function _calcBar(s) {
  const isBuy  = s.direccion === 'COMPRA';
  const sl     = parseFloat(s.sl) || 0;
  const ent    = parseFloat(s.precio_entrada) || 0;
  const act    = parseFloat(s.precio_actual) || ent;
  const tp1    = parseFloat(s.tp1) || 0;
  const tp2    = parseFloat(s.tp2) || 0;
  const tp3    = parseFloat(s.tp3) || 0;
  const tpEdge = tp3 || tp2 || tp1;  // TP más alejado disponible

  if (!sl || !ent || !tpEdge) return null;

  // Normalizar a [0,100] donde 0=SL y 100=tpEdge
  const pct = v => isBuy
    ? Math.max(0, Math.min(100, (v - sl) / (tpEdge - sl) * 100))
    : Math.max(0, Math.min(100, (sl - v) / (sl - tpEdge) * 100));

  const pctEntry  = pct(ent);
  const pctActual = pct(act);
  const pctTp1    = tp1 ? pct(tp1) : null;
  const pctTp2    = tp2 ? pct(tp2) : null;
  const pctTp3    = tp3 ? pct(tp3) : null;

  // Moverse a la derecha = alejarse del SL = hacia TP = verde
  const towardsTP  = pctActual >= pctEntry;
  const fillLeft   = Math.min(pctActual, pctEntry);
  const fillWidth  = Math.abs(pctActual - pctEntry);
  const pctDisplay = fillWidth.toFixed(1);

  return { pctActual, pctEntry, pctTp1, pctTp2, pctTp3, fillLeft, fillWidth, towardsTP, pctDisplay };
}

let _notifPermiso = (typeof Notification !== 'undefined') ? Notification.permission : 'denied';

function _solicitarNotifPermiso() {
  if (typeof Notification === 'undefined') return;
  if (Notification.permission === 'default') {
    Notification.requestPermission().then(p => { _notifPermiso = p; });
  } else {
    _notifPermiso = Notification.permission;
  }
}

function _browserNotify(titulo, cuerpo, url = '/dashboard/activas') {
  if (typeof Notification === 'undefined' || _notifPermiso !== 'granted') return;
  try {
    const n = new Notification(titulo, {
      body:  cuerpo,
      icon:  '/static/img/icon-192.png',
      badge: '/static/img/icon-192.png',
      tag:   titulo,
      renotify: true,
    });
    n.onclick = () => {
      window.focus();
      window.location.href = url;
      n.close();
    };
  } catch (_) {}
}

/**
 * Convierte una clave VAPID public key en Base64URL a Uint8Array
 * para usarla en pushManager.subscribe({ applicationServerKey }).
 */
function _urlBase64ToUint8Array(base64String) {
  const padding = '='.repeat((4 - base64String.length % 4) % 4);
  const base64  = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
  const raw     = window.atob(base64);
  const arr     = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; ++i) arr[i] = raw.charCodeAt(i);
  return arr;
}

