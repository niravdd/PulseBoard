/**
 * PulseBoard — Chart.js configuration and helpers.
 */
(function () {
    'use strict';

    const COLORS = {
        accent: '#6366f1',
        accentLight: 'rgba(99, 102, 241, 0.15)',
        green: '#10b981',
        greenLight: 'rgba(16, 185, 129, 0.15)',
        amber: '#f59e0b',
        red: '#ef4444',
        purple: '#a855f7',
        cyan: '#06b6d4',
        pink: '#ec4899',
        muted: '#64748b',
        grid: 'rgba(45, 53, 85, 0.5)',
        text: '#94a3b8',
    };

    const PIE_PALETTE = [COLORS.accent, COLORS.green, COLORS.amber, COLORS.purple, COLORS.cyan, COLORS.pink, COLORS.red, '#8b5cf6', '#14b8a6', '#f97316'];

    // Global Chart.js defaults
    Chart.defaults.color = COLORS.text;
    Chart.defaults.borderColor = COLORS.grid;
    Chart.defaults.font.family = "'Inter', -apple-system, sans-serif";
    Chart.defaults.font.size = 11;

    window.PBCharts = {
        _instances: {},

        /**
         * Render or update the timeseries line chart.
         * @param {string} canvasId
         * @param {Array} series — [{date, events, unique}, ...]
         */
        timeseries(canvasId, series) {
            const ctx = document.getElementById(canvasId);
            if (!ctx) return;

            if (this._instances[canvasId]) {
                this._instances[canvasId].destroy();
            }

            this._instances[canvasId] = new Chart(ctx, {
                type: 'line',
                data: {
                    labels: series.map(s => s.date),
                    datasets: [
                        {
                            label: 'Events',
                            data: series.map(s => s.events),
                            borderColor: COLORS.accent,
                            backgroundColor: COLORS.accentLight,
                            fill: true,
                            tension: 0.3,
                            pointRadius: 3,
                            pointHoverRadius: 6,
                        },
                        {
                            label: 'Unique Deployments',
                            data: series.map(s => s.unique),
                            borderColor: COLORS.green,
                            backgroundColor: COLORS.greenLight,
                            fill: true,
                            tension: 0.3,
                            pointRadius: 3,
                            pointHoverRadius: 6,
                        },
                    ],
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    interaction: { intersect: false, mode: 'index' },
                    plugins: {
                        legend: { position: 'top', labels: { usePointStyle: true, pointStyle: 'circle', padding: 16 } },
                        tooltip: { backgroundColor: '#1a1f35', borderColor: '#2d3555', borderWidth: 1, cornerRadius: 8, padding: 12 },
                    },
                    scales: {
                        x: { grid: { display: false }, ticks: { maxRotation: 45 } },
                        y: { beginAtZero: true, grid: { color: COLORS.grid } },
                    },
                },
            });
        },

        /**
         * Render or update a doughnut chart.
         * @param {string} canvasId
         * @param {Array} data — [{name, count}, ...]
         */
        doughnut(canvasId, data) {
            const ctx = document.getElementById(canvasId);
            if (!ctx) return;

            if (this._instances[canvasId]) {
                this._instances[canvasId].destroy();
            }

            this._instances[canvasId] = new Chart(ctx, {
                type: 'doughnut',
                data: {
                    labels: data.map(d => d.name),
                    datasets: [{
                        data: data.map(d => d.count),
                        backgroundColor: PIE_PALETTE.slice(0, data.length),
                        borderWidth: 0,
                        hoverOffset: 6,
                    }],
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    cutout: '65%',
                    plugins: {
                        legend: { position: 'bottom', labels: { usePointStyle: true, pointStyle: 'circle', padding: 10, font: { size: 10 } } },
                        tooltip: { backgroundColor: '#1a1f35', borderColor: '#2d3555', borderWidth: 1, cornerRadius: 8 },
                    },
                },
            });
        },

        destroyAll() {
            Object.values(this._instances).forEach(c => c.destroy());
            this._instances = {};
        },
    };
})();
