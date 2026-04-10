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

            const hasCost = series.some(s => (s.cost_usd || 0) > 0);

            const datasets = [
                {
                    label: 'Events',
                    data: series.map(s => s.events),
                    borderColor: COLORS.accent,
                    backgroundColor: COLORS.accentLight,
                    fill: true,
                    tension: 0.3,
                    pointRadius: 3,
                    pointHoverRadius: 6,
                    yAxisID: 'y',
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
                    yAxisID: 'y',
                },
            ];

            if (hasCost) {
                datasets.push({
                    label: 'Cost (USD)',
                    data: series.map(s => s.cost_usd || 0),
                    borderColor: COLORS.amber,
                    backgroundColor: 'rgba(245, 158, 11, 0.08)',
                    fill: true,
                    tension: 0.3,
                    pointRadius: 2,
                    pointHoverRadius: 5,
                    borderDash: [4, 2],
                    yAxisID: 'yCost',
                });
            }

            const scales = {
                x: { grid: { display: false }, ticks: { maxRotation: 45 } },
                y: { beginAtZero: true, grid: { color: COLORS.grid }, position: 'left' },
            };
            if (hasCost) {
                scales.yCost = {
                    beginAtZero: true,
                    position: 'right',
                    grid: { display: false },
                    ticks: {
                        callback: (v) => v === 0 ? '' : `$${v < 0.01 ? v.toFixed(4) : v.toFixed(2)}`,
                        font: { size: 10 },
                        color: COLORS.amber,
                    },
                };
            }

            this._instances[canvasId] = new Chart(ctx, {
                type: 'line',
                data: { labels: series.map(s => s.date), datasets },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    interaction: { intersect: false, mode: 'index' },
                    plugins: {
                        legend: { position: 'top', labels: { usePointStyle: true, pointStyle: 'circle', padding: 16 } },
                        tooltip: {
                            backgroundColor: '#1a1f35', borderColor: '#2d3555', borderWidth: 1, cornerRadius: 8, padding: 12,
                            callbacks: {
                                label: (ctx) => {
                                    if (ctx.dataset.yAxisID === 'yCost') {
                                        const v = ctx.parsed.y;
                                        return ` Cost: ${v < 0.01 ? `~$${v.toFixed(4)}` : `~$${v.toFixed(2)}`}`;
                                    }
                                    return ` ${ctx.dataset.label}: ${ctx.parsed.y}`;
                                },
                            },
                        },
                    },
                    scales,
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

        /**
         * Render GitHub traffic chart (clones + views over time).
         */
        githubTraffic(canvasId, daily) {
            const ctx = document.getElementById(canvasId);
            if (!ctx) return;
            if (this._instances[canvasId]) this._instances[canvasId].destroy();

            this._instances[canvasId] = new Chart(ctx, {
                type: 'bar',
                data: {
                    labels: daily.map(d => d.date),
                    datasets: [
                        {
                            label: 'Clones',
                            data: daily.map(d => d.clones || 0),
                            backgroundColor: COLORS.accent,
                            borderRadius: 3,
                        },
                        {
                            label: 'Views',
                            data: daily.map(d => d.views || 0),
                            backgroundColor: COLORS.green,
                            borderRadius: 3,
                        },
                    ],
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {
                        legend: { position: 'top', labels: { usePointStyle: true, pointStyle: 'circle', padding: 12, font: { size: 10 } } },
                        tooltip: { backgroundColor: '#1a1f35', borderColor: '#2d3555', borderWidth: 1, cornerRadius: 8 },
                    },
                    scales: {
                        x: { grid: { display: false }, ticks: { maxRotation: 45, font: { size: 9 } } },
                        y: { beginAtZero: true, grid: { color: COLORS.grid } },
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
