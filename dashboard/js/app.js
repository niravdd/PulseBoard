/**
 * PulseBoard — Dashboard Application
 *
 * Handles login flow, project selection, data loading, and chart rendering.
 */
(function () {
    'use strict';

    let currentProject = null;
    let _challengeSession = null;
    let _currentDays = 30;
    let _customFrom = '';
    let _customTo = '';

    // ── Init ────────────────────────────────────────────────────────

    document.addEventListener('DOMContentLoaded', () => {
        if (PBAuth.init() && PBAuth.isAuthenticated()) {
            showDashboard();
        } else {
            showLogin();
        }
        attachEvents();
    });

    function attachEvents() {
        // Login
        document.getElementById('login-btn')?.addEventListener('click', handleLogin);
        document.getElementById('login-password')?.addEventListener('keydown', e => { if (e.key === 'Enter') handleLogin(); });

        // Logout
        document.getElementById('btn-logout')?.addEventListener('click', () => {
            PBAuth.logout();
            showLogin();
        });

        // Project selector
        document.getElementById('project-selector')?.addEventListener('change', (e) => {
            if (e.target.value) loadProject(e.target.value);
            else {
                document.getElementById('project-dashboard')?.classList.add('hidden');
                document.getElementById('no-project')?.classList.remove('hidden');
            }
        });

        // Chart period selector
        document.getElementById('chart-period')?.addEventListener('change', () => {
            if (currentProject) loadTimeseries(currentProject.project_id);
        });

        // Period filter buttons
        document.getElementById('period-filter')?.addEventListener('click', (e) => {
            const btn = e.target.closest('[data-days]');
            if (!btn) return;
            _currentDays = parseInt(btn.dataset.days);
            // Update active button styling
            document.querySelectorAll('#period-filter button').forEach(b => {
                b.className = b === btn
                    ? 'px-3 py-1 rounded-lg text-xs font-medium transition-colors bg-pb-accent text-white'
                    : 'px-3 py-1 rounded-lg text-xs font-medium transition-colors bg-pb-bg border border-pb-border hover:border-pb-accent';
            });
            _customFrom = '';
            _customTo = '';
            document.getElementById('custom-range')?.classList.add('hidden');
            if (currentProject) {
                loadTimeseries(currentProject.project_id);
                loadBreakdown(currentProject.project_id);
            }
        });

        // Custom date range
        document.getElementById('btn-custom-range')?.addEventListener('click', () => {
            document.getElementById('custom-range')?.classList.toggle('hidden');
        });
        document.getElementById('btn-apply-range')?.addEventListener('click', () => {
            _customFrom = document.getElementById('range-from')?.value || '';
            _customTo = document.getElementById('range-to')?.value || '';
            if (!_customFrom || !_customTo) return;
            _currentDays = -1;  // Flag: using custom range
            // Clear preset button highlights
            document.querySelectorAll('#period-filter button[data-days]').forEach(b => {
                b.className = 'px-3 py-1 rounded-lg text-xs font-medium transition-colors bg-pb-bg border border-pb-border hover:border-pb-accent';
            });
            document.getElementById('btn-custom-range').className = 'px-3 py-1 rounded-lg text-xs font-medium transition-colors bg-pb-accent text-white';
            if (currentProject) {
                loadTimeseries(currentProject.project_id);
                loadBreakdown(currentProject.project_id);
            }
        });

        // New project
        document.getElementById('btn-new-project')?.addEventListener('click', () => {
            document.getElementById('new-project-modal')?.classList.remove('hidden');
        });
        document.getElementById('btn-cancel-project')?.addEventListener('click', () => {
            document.getElementById('new-project-modal')?.classList.add('hidden');
        });
        document.getElementById('btn-create-project')?.addEventListener('click', handleCreateProject);

        // Project settings buttons
        document.getElementById('btn-copy-key')?.addEventListener('click', () => {
            const key = document.getElementById('project-api-key')?.textContent;
            if (key) navigator.clipboard.writeText(key);
        });
        document.getElementById('btn-regen-key')?.addEventListener('click', handleRegenKey);
        document.getElementById('btn-delete-project')?.addEventListener('click', handleDeleteProject);
    }

    // ── Auth ─────────────────────────────────────────────────────────

    async function handleLogin() {
        const email = document.getElementById('login-email')?.value?.trim();
        const password = document.getElementById('login-password')?.value;
        const newPassword = document.getElementById('login-new-password')?.value;
        const errorEl = document.getElementById('login-error');

        if (!email || !password) return;

        errorEl?.classList.add('hidden');

        try {
            if (_challengeSession && newPassword) {
                // Complete new password challenge
                await PBAuth.completeNewPassword(email, newPassword, _challengeSession);
                _challengeSession = null;
                showDashboard();
                return;
            }

            const result = await PBAuth.login(email, password);

            if (result.challenge === 'NEW_PASSWORD_REQUIRED') {
                _challengeSession = result.session;
                document.getElementById('new-password-group')?.classList.remove('hidden');
                document.getElementById('login-btn').textContent = 'Set New Password';
                return;
            }

            showDashboard();
        } catch (err) {
            if (errorEl) {
                errorEl.textContent = err.message;
                errorEl.classList.remove('hidden');
            }
        }
    }

    // ── Views ────────────────────────────────────────────────────────

    function showLogin() {
        document.getElementById('login-screen')?.classList.remove('hidden');
        document.getElementById('dashboard')?.classList.add('hidden');
    }

    function showDashboard() {
        document.getElementById('login-screen')?.classList.add('hidden');
        document.getElementById('dashboard')?.classList.remove('hidden');
        document.getElementById('user-email').textContent = PBAuth.getUser();
        loadProjects();
    }

    // ── Projects ─────────────────────────────────────────────────────

    async function loadProjects() {
        try {
            const data = await PBAuth.api('/projects');
            const sel = document.getElementById('project-selector');
            sel.innerHTML = '<option value="">Select project...</option>';
            (data.projects || []).forEach(p => {
                const opt = document.createElement('option');
                opt.value = p.project_id;
                opt.textContent = p.name;
                sel.appendChild(opt);
            });
        } catch (err) {
            console.error('Failed to load projects:', err);
        }
    }

    async function loadProject(projectId) {
        try {
            const project = await PBAuth.api(`/projects/${projectId}`);
            currentProject = project;

            document.getElementById('no-project')?.classList.add('hidden');
            document.getElementById('project-dashboard')?.classList.remove('hidden');

            // Settings panel
            document.getElementById('project-id').textContent = project.project_id;
            document.getElementById('project-api-key').textContent = project.api_key;
            const baseUrl = window.PB_CONFIG?.apiBase || window.location.origin;
            document.getElementById('project-endpoint').textContent = `${baseUrl}/ingest`;
            document.getElementById('sdk-snippet').textContent =
                `from pulseboard import PulseBoard\n` +
                `pb = PulseBoard(api_key="${project.api_key}", endpoint="${baseUrl}/ingest")\n` +
                `pb.startup(version="1.0")`;

            // Load data
            await Promise.all([
                loadOverview(projectId),
                loadTimeseries(projectId),
                loadBreakdown(projectId),
                loadEvents(projectId),
            ]);
        } catch (err) {
            console.error('Failed to load project:', err);
        }
    }

    // ── Data Loading ─────────────────────────────────────────────────

    async function loadOverview(projectId) {
        const data = await PBAuth.api(`/stats/${projectId}/overview`);
        const fmt = (n) => (n || 0).toLocaleString();
        const fmtCost = (n) => `$${(n || 0).toFixed(2)}`;

        document.getElementById('stat-today').textContent = fmt(data.today?.events);
        document.getElementById('stat-today-cost').textContent = fmtCost(data.today?.cost_usd);
        document.getElementById('stat-7d').textContent = fmt(data.last_7d?.events);
        document.getElementById('stat-7d-unique').textContent = `${fmt(data.last_7d?.unique_deployments)} unique`;
        document.getElementById('stat-7d-cost').textContent = fmtCost(data.last_7d?.cost_usd);
        document.getElementById('stat-30d').textContent = fmt(data.last_30d?.events);
        document.getElementById('stat-30d-unique').textContent = `${fmt(data.last_30d?.unique_deployments)} unique`;
        document.getElementById('stat-30d-cost').textContent = fmtCost(data.last_30d?.cost_usd);
        document.getElementById('stat-lifetime').textContent = fmt(data.lifetime?.events);
        document.getElementById('stat-lifetime-unique').textContent = `${fmt(data.lifetime?.unique_deployments)} unique`;
        document.getElementById('stat-lifetime-cost').textContent = fmtCost(data.lifetime?.cost_usd);

        const topVer = data.top_version?.[0];
        document.getElementById('stat-version').textContent = topVer?.name || '-';
        const topOs = data.top_os?.[0];
        document.getElementById('stat-os').textContent = topOs ? `${topOs.name} (${topOs.count})` : '-';
    }

    function _buildDateParams() {
        if (_currentDays === -1 && _customFrom && _customTo) {
            return `from=${_customFrom}&to=${_customTo}`;
        }
        return `days=${_currentDays}`;
    }

    async function loadTimeseries(projectId) {
        const period = document.getElementById('chart-period')?.value || 'daily';
        const data = await PBAuth.api(`/stats/${projectId}/timeseries?period=${period}&${_buildDateParams()}`);
        PBCharts.timeseries('chart-timeseries', data.series || []);
    }

    async function loadBreakdown(projectId) {
        const data = await PBAuth.api(`/stats/${projectId}/breakdown?${_buildDateParams()}`);

        // OS doughnut
        PBCharts.doughnut('chart-os', data.os || []);

        // Version list
        const versionList = document.getElementById('version-list');
        const versions = data.versions || [];
        const maxCount = versions[0]?.count || 1;
        versionList.innerHTML = versions.map(v => `
            <div class="flex items-center gap-2">
                <span class="text-xs font-mono w-20 truncate text-pb-text">${esc(v.name)}</span>
                <div class="flex-1 h-2 rounded-full bg-pb-bg overflow-hidden">
                    <div class="h-full rounded-full bg-gradient-to-r from-pb-accent to-purple-500" style="width: ${Math.round(v.count / maxCount * 100)}%"></div>
                </div>
                <span class="text-xs text-pb-muted w-10 text-right">${v.count}</span>
            </div>
        `).join('');

        // Country list
        const countryList = document.getElementById('country-list');
        const countries = data.countries || [];
        const maxC = countries[0]?.count || 1;
        countryList.innerHTML = countries.map(c => `
            <div class="flex items-center gap-2">
                <span class="text-xs w-8 text-center">${countryFlag(c.name)}</span>
                <span class="text-xs w-16 truncate text-pb-text">${esc(c.name)}</span>
                <div class="flex-1 h-2 rounded-full bg-pb-bg overflow-hidden">
                    <div class="h-full rounded-full bg-gradient-to-r from-pb-green to-cyan-500" style="width: ${Math.round(c.count / maxC * 100)}%"></div>
                </div>
                <span class="text-xs text-pb-muted w-10 text-right">${c.count}</span>
            </div>
        `).join('');

        // Model list
        const modelList = document.getElementById('model-list');
        const models = data.models || [];
        const maxM = models[0]?.count || 1;
        modelList.innerHTML = models.length === 0
            ? '<p class="text-xs text-pb-muted">No model data yet</p>'
            : models.map(m => `
                <div class="flex items-center gap-2">
                    <span class="text-xs font-mono w-28 truncate text-pb-text">${esc(m.name)}</span>
                    <div class="flex-1 h-2 rounded-full bg-pb-bg overflow-hidden">
                        <div class="h-full rounded-full bg-gradient-to-r from-pb-amber to-orange-500" style="width: ${Math.round(m.count / maxM * 100)}%"></div>
                    </div>
                    <span class="text-xs text-pb-muted w-10 text-right">${m.count}</span>
                </div>
            `).join('');

        // Event type list
        const etList = document.getElementById('event-type-list');
        const ets = data.event_types || [];
        const maxET = ets[0]?.count || 1;
        etList.innerHTML = ets.length === 0
            ? '<p class="text-xs text-pb-muted">No event data yet</p>'
            : ets.map(e => `
                <div class="flex items-center gap-2">
                    <span class="text-xs font-mono w-28 truncate text-pb-text">${esc(e.name)}</span>
                    <div class="flex-1 h-2 rounded-full bg-pb-bg overflow-hidden">
                        <div class="h-full rounded-full bg-gradient-to-r from-purple-500 to-pink-500" style="width: ${Math.round(e.count / maxET * 100)}%"></div>
                    </div>
                    <span class="text-xs text-pb-muted w-10 text-right">${e.count}</span>
                </div>
            `).join('');

        // Cost banner
        const costBanner = document.getElementById('total-cost-banner');
        if (costBanner) costBanner.textContent = `$${(data.total_cost_usd || 0).toFixed(2)}`;
    }

    async function loadEvents(projectId) {
        const data = await PBAuth.api(`/stats/${projectId}/events?limit=30`);
        const list = document.getElementById('events-list');
        const events = data.events || [];

        list.innerHTML = events.map(e => {
            const time = new Date(e.timestamp_id?.split('#')[0] || '').toLocaleString();
            const props = typeof e.properties === 'object' ? e.properties : {};
            return `
                <div class="flex items-center gap-2 py-1.5 border-b border-pb-border/30 last:border-0">
                    <span class="px-1.5 py-0.5 rounded bg-pb-accent/10 text-pb-accent font-medium">${esc(e.event_type)}</span>
                    <span class="text-pb-muted">${props.version || ''}</span>
                    <span class="text-pb-muted">${props.os || ''}</span>
                    <span class="text-pb-muted">${e.country || ''}</span>
                    <span class="ml-auto text-pb-muted/60">${time}</span>
                </div>
            `;
        }).join('');
    }

    // ── Project Actions ──────────────────────────────────────────────

    async function handleCreateProject() {
        const name = document.getElementById('new-project-name')?.value?.trim();
        const desc = document.getElementById('new-project-desc')?.value?.trim();
        if (!name) return;

        try {
            const result = await PBAuth.api('/projects', { method: 'POST', body: { name, description: desc } });
            document.getElementById('new-project-modal')?.classList.add('hidden');
            await loadProjects();
            // Auto-select the new project
            document.getElementById('project-selector').value = result.project_id;
            loadProject(result.project_id);
        } catch (err) {
            alert('Create failed: ' + err.message);
        }
    }

    async function handleRegenKey() {
        if (!currentProject) return;
        if (!confirm('Regenerate API key? Existing integrations will stop working until updated.')) return;
        try {
            const result = await PBAuth.api(`/projects/${currentProject.project_id}/regen-key`, { method: 'POST' });
            document.getElementById('project-api-key').textContent = result.api_key;
            currentProject.api_key = result.api_key;
        } catch (err) {
            alert('Failed: ' + err.message);
        }
    }

    async function handleDeleteProject() {
        if (!currentProject) return;
        if (!confirm(`Delete "${currentProject.name}"? This removes all telemetry data permanently.`)) return;
        try {
            await PBAuth.api(`/projects/${currentProject.project_id}`, { method: 'DELETE' });
            currentProject = null;
            await loadProjects();
            document.getElementById('project-dashboard')?.classList.add('hidden');
            document.getElementById('no-project')?.classList.remove('hidden');
        } catch (err) {
            alert('Failed: ' + err.message);
        }
    }

    // ── Helpers ──────────────────────────────────────────────────────

    function esc(str) {
        if (!str) return '';
        const div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML;
    }

    function countryFlag(code) {
        if (!code || code.length !== 2) return '';
        return String.fromCodePoint(...[...code.toUpperCase()].map(c => 0x1F1E6 - 65 + c.charCodeAt(0)));
    }
})();
