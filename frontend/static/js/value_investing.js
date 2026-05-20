'use strict';

function viApp() {
  return {
    vistaActual: 'calendario',
    tickerActual: '',
    cargando: false,
    cargandoEmpresa: false,
    error: '',

    menuAbierto: false,
    ultimaActualizacion: '',

    semanas: [],
    categorias: [],
    catFiltro: 'todas',
    categoriaData: [],

    empresaDetalle: null,
    _epsChart: null,

    stats: {
      total: 0,
      esta_semana: 0,
      proxima_semana: 0,
    },

    botStats: {
      online: false,
      win_rate: null,
      activas: null,
      senales_hoy: null,
      avg_profit: null,
      total_30d: null,
    },

    async init(vistaInicial = 'calendario', tickerInicial = '') {
      this.vistaActual = vistaInicial || 'calendario';
      this.tickerActual = (tickerInicial || '').toUpperCase();

      await Promise.all([
        this.cargarCalendario(),
        this.cargarBotStats(),
      ]);

      if (this.vistaActual === 'empresa' && this.tickerActual) {
        await this.abrirEmpresa(this.tickerActual, false);
      }
    },

    async cargarBotStats() {
      try {
        const [rStats, rActivas] = await Promise.all([
          fetch('/api/v1/stats/global'),
          fetch('/api/v1/senales/activas'),
        ]);
        if (rStats.ok) {
          const d = await rStats.json();
          const g = d.global_30d || {};
          this.botStats.win_rate  = g.win_rate  ?? null;
          this.botStats.avg_profit = g.avg_profit ?? null;
          this.botStats.total_30d  = g.total      ?? null;
          this.botStats.senales_hoy = (d.hoy || {}).total ?? null;
          this.botStats.online = true;
        }
        if (rActivas.ok) {
          const d = await rActivas.json();
          this.botStats.activas = d.total ?? null;
        }
      } catch (_) {
        /* silent — sidebar mostrará '—' si hay error */
      }
    },

    async cargarCalendario() {
      this.cargando = true;
      this.error = '';
      try {
        const r = await fetch('/api/v1/vi/calendario');
        if (!r.ok) throw new Error('No se pudo cargar el calendario');
        const data = await r.json();
        this.semanas = data.semanas || [];
        this.categorias = data.categorias || [];
        this.stats = data.stats || { total: 0, esta_semana: 0, proxima_semana: 0 };
        this._buildCategoriasData();
        this._stampActualizacion();
      } catch (err) {
        this.error = err?.message || 'Error cargando calendario';
      } finally {
        this.cargando = false;
      }
    },

    _buildCategoriasData() {
      const map = {};
      for (const semana of this.semanas) {
        for (const emp of (semana.empresas || [])) {
          const cat = emp.categoria || 'Sin categoría';
          if (!map[cat]) {
            map[cat] = {
              nombre: cat,
              emoji: emp.categoria_emoji || '📁',
              peso: emp.categoria_peso || 0,
              empresas: [],
            };
          }
          map[cat].empresas.push(emp);
        }
      }
      this.categoriaData = Object.values(map);
    },

    get semanasFiltradas() {
      if (this.catFiltro === 'todas') return this.semanas;
      return (this.semanas || [])
        .map((s) => ({
          ...s,
          empresas: (s.empresas || []).filter((e) => e.categoria === this.catFiltro),
        }))
        .filter((s) => s.empresas.length > 0);
    },

    get categoriasFiltradas() {
      if (this.catFiltro === 'todas') return this.categoriaData;
      return (this.categoriaData || []).filter((c) => c.nombre === this.catFiltro);
    },

    cambiarVista(vista) {
      this.vistaActual = vista;
      if (vista !== 'empresa' && this._epsChart) {
        this._epsChart.destroy();
        this._epsChart = null;
      }
    },

    async abrirEmpresa(ticker, pushState = true) {
      this.cargandoEmpresa = true;
      this.error = '';
      this.tickerActual = (ticker || '').toUpperCase();
      this.vistaActual = 'empresa';

      try {
        const r = await fetch(`/api/v1/vi/empresa/${this.tickerActual}`);
        if (!r.ok) throw new Error('No se pudo cargar el detalle de empresa');
        this.empresaDetalle = await r.json();
        this._stampActualizacion();

        if (pushState) {
          window.history.replaceState({}, '', `/value-investing/empresa/${this.tickerActual}`);
        }

        this.$nextTick(() => this._renderEpsChart());
      } catch (err) {
        this.error = err?.message || 'Error cargando empresa';
      } finally {
        this.cargandoEmpresa = false;
      }
    },

    _renderEpsChart() {
      if (!this.empresaDetalle || !window.Chart) return;
      const rows = this.empresaDetalle.historico_earnings || [];
      const canvas = document.getElementById('epsChart');
      if (!canvas || rows.length === 0) return;

      if (this._epsChart) this._epsChart.destroy();

      const labels = rows.map((r) => r.periodo);
      const estimados = rows.map((r) => r.eps_estimado);
      const reales = rows.map((r) => r.eps_real);
      const realColors = rows.map((r) => (r.beat ? '#00e676' : '#ff1744'));

      this._epsChart = new Chart(canvas.getContext('2d'), {
        type: 'bar',
        data: {
          labels,
          datasets: [
            {
              label: 'EPS Estimado',
              data: estimados,
              backgroundColor: 'rgba(41,182,246,0.45)',
              borderColor: '#29b6f6',
              borderWidth: 1,
            },
            {
              label: 'EPS Real',
              data: reales,
              backgroundColor: realColors,
              borderWidth: 0,
            },
          ],
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: {
            legend: { labels: { color: '#cdd9e5' } },
          },
          scales: {
            x: { ticks: { color: '#9fb0bf' }, grid: { color: 'rgba(255,255,255,0.04)' } },
            y: { ticks: { color: '#9fb0bf' }, grid: { color: 'rgba(255,255,255,0.05)' } },
          },
        },
      });
    },

    _diasPara(dateStr) {
      if (!dateStr) return null;
      const d = new Date(dateStr);
      if (Number.isNaN(d.getTime())) return null;

      const now = new Date();
      const target = new Date(d.getFullYear(), d.getMonth(), d.getDate());
      const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
      return Math.round((target - today) / 86400000);
    },

    _stampActualizacion() {
      this.ultimaActualizacion = new Date().toLocaleTimeString('es-ES', { hour: '2-digit', minute: '2-digit' });
    },
  };
}
