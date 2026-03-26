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
    let _autoRefreshTimer = null;
    const AUTO_REFRESH_MS = 5 * 60 * 1000; // 5 minutes
    const IDLE_THRESHOLD_MS = 30 * 1000;    // Consider idle after 30s of no interaction
    let _lastInteraction = Date.now();
    let _lastRefresh = Date.now();
    let _countdownTimer = null;

    // ── Init ────────────────────────────────────────────────────────

    document.addEventListener('DOMContentLoaded', () => {
        if (PBAuth.init() && PBAuth.isAuthenticated()) {
            showDashboard();
        } else {
            showLogin();
        }
        attachEvents();

        // Idle detection — only meaningful interactions (not mouse hover)
        for (const evt of ['click', 'keydown', 'scroll', 'touchstart']) {
            document.addEventListener(evt, () => { _lastInteraction = Date.now(); }, { passive: true });
        }

        // Background tab: refresh immediately when user returns after enough idle time
        document.addEventListener('visibilitychange', () => {
            if (document.visibilityState === 'visible' && currentProject) {
                const elapsed = Date.now() - _lastRefresh;
                if (elapsed >= AUTO_REFRESH_MS) {
                    _lastRefresh = Date.now();
                    loadOverview(currentProject.project_id);
                    loadTimeseries(currentProject.project_id);
                    loadBreakdown(currentProject.project_id);
                    loadEvents(currentProject.project_id);
                }
            }
        });
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
                if (currentProject.github_repo) loadGitHub(currentProject.project_id, _currentDays);
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
                if (currentProject.github_repo) loadGitHub(currentProject.project_id, _currentDays);
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

        // Manual refresh
        document.getElementById('btn-refresh')?.addEventListener('click', () => {
            if (currentProject) {
                _lastRefresh = Date.now();
                loadOverview(currentProject.project_id);
                loadTimeseries(currentProject.project_id);
                loadBreakdown(currentProject.project_id);
                loadEvents(currentProject.project_id);
                if (currentProject.github_repo) loadGitHub(currentProject.project_id, _currentDays);
            }
        });

        // Admin management
        document.getElementById('btn-manage-admins')?.addEventListener('click', () => {
            document.getElementById('admin-modal')?.classList.remove('hidden');
            loadAdmins();
        });
        document.getElementById('btn-close-admins')?.addEventListener('click', () => {
            document.getElementById('admin-modal')?.classList.add('hidden');
        });
        document.getElementById('btn-invite')?.addEventListener('click', handleInviteAdmin);

        // API Key show/copy (admin only)
        document.getElementById('btn-show-key')?.addEventListener('click', () => {
            if (!PBAuth.isAdmin()) return;
            const el = document.getElementById('project-api-key');
            const showBtn = document.getElementById('btn-show-key');
            const copyBtn = document.getElementById('btn-copy-key');
            if (el && currentProject) {
                el.textContent = currentProject.api_key;
                el.classList.add('select-all');
                showBtn?.classList.add('hidden');
                copyBtn?.classList.remove('hidden');
            }
        });
        document.getElementById('btn-copy-key')?.addEventListener('click', () => {
            const key = document.getElementById('project-api-key')?.textContent;
            if (key && key !== '••••••••••••') navigator.clipboard.writeText(key);
        });
        document.getElementById('btn-regen-key')?.addEventListener('click', handleRegenKey);
        document.getElementById('btn-delete-project')?.addEventListener('click', handleDeleteProject);

        // GitHub integration
        document.getElementById('btn-save-github')?.addEventListener('click', handleSaveGitHub);
        document.getElementById('btn-fetch-github')?.addEventListener('click', handleFetchGitHub);

        // Auto-parse full GitHub URL → owner/repo
        document.getElementById('project-github-repo')?.addEventListener('blur', (e) => {
            const val = e.target.value.trim();
            const match = val.match(/github\.com\/([^/]+\/[^/]+)/);
            if (match) {
                e.target.value = match[1].replace(/\.git$/, '');
            }
        });

        // When repo changes, require new PAT and show overwrite warning
        document.getElementById('project-github-repo')?.addEventListener('input', (e) => {
            const saved = e.target.dataset.saved || '';
            const current = e.target.value.trim();
            const warning = document.getElementById('github-overwrite-warning');
            const tokenField = document.getElementById('project-github-token');
            if (saved && current !== saved) {
                warning?.classList.remove('hidden');
                if (tokenField) {
                    tokenField.value = '';
                    tokenField.placeholder = 'New token required — repo changed';
                    tokenField.required = true;
                }
            } else if (!current) {
                warning?.classList.add('hidden');
            }
        });

        // Show overwrite warning when PAT field is edited
        document.getElementById('project-github-token')?.addEventListener('input', () => {
            const repoSaved = document.getElementById('project-github-repo')?.dataset.saved || '';
            if (repoSaved) {
                document.getElementById('github-overwrite-warning')?.classList.remove('hidden');
            }
        });
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
        const role = PBAuth.getRole();
        document.getElementById('user-email').textContent = `${PBAuth.getUser()} (${role})`;

        // Hide admin-only elements for viewers
        if (!PBAuth.isAdmin()) {
            document.querySelectorAll('.admin-only').forEach(el => el.classList.add('hidden'));
        }
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

            // Restore last selected project
            const lastProject = localStorage.getItem('pb_last_project');
            if (lastProject) {
                const exists = (data.projects || []).some(p => p.project_id === lastProject);
                if (exists) {
                    sel.value = lastProject;
                    loadProject(lastProject);
                }
            }
        } catch (err) {
            console.error('Failed to load projects:', err);
        }
    }

    async function loadProject(projectId) {
        try {
            const project = await PBAuth.api(`/projects/${projectId}`);
            currentProject = project;
            localStorage.setItem('pb_last_project', projectId);

            document.getElementById('no-project')?.classList.add('hidden');
            document.getElementById('project-dashboard')?.classList.remove('hidden');

            // Start auto-refresh
            _startAutoRefresh(projectId);

            // Settings panel
            document.getElementById('project-id').textContent = project.project_id;
            // API key always starts masked — admin clicks Show to reveal
            document.getElementById('project-api-key').textContent = '••••••••••••';
            document.getElementById('btn-show-key')?.classList.remove('hidden');
            document.getElementById('btn-copy-key')?.classList.add('hidden');
            const baseUrl = window.PB_CONFIG?.apiBase || window.location.origin;
            document.getElementById('project-endpoint').textContent = `${baseUrl}/ingest`;
            const displayKey = PBAuth.isAdmin() ? project.api_key : '••••••••••••';
            document.getElementById('sdk-snippet').textContent =
                `from pulseboard import PulseBoard\n` +
                `pb = PulseBoard(api_key="${displayKey}", endpoint="${baseUrl}/ingest")\n` +
                `pb.startup(version="1.0")`;

            // GitHub fields
            const ghRepo = document.getElementById('project-github-repo');
            const ghToken = document.getElementById('project-github-token');
            if (ghRepo) {
                ghRepo.value = project.github_repo || '';
                ghRepo.dataset.saved = project.github_repo || '';
            }
            if (ghToken) {
                if (project.github_token_set) {
                    ghToken.value = '';
                    ghToken.placeholder = 'Token is active — leave empty to keep, or enter new to replace';
                } else {
                    ghToken.value = '';
                    ghToken.placeholder = 'ghp_... or github_pat_...';
                }
            }
            document.getElementById('github-overwrite-warning')?.classList.add('hidden');

            // Load data
            const loads = [
                loadOverview(projectId),
                loadTimeseries(projectId),
                loadBreakdown(projectId),
                loadEvents(projectId),
            ];
            if (project.github_repo) {
                loads.push(loadGitHub(projectId, _currentDays));
            } else {
                document.getElementById('github-section')?.classList.add('hidden');
            }
            await Promise.all(loads);
        } catch (err) {
            console.error('Failed to load project:', err);
        }
    }

    // ── Data Loading ─────────────────────────────────────────────────

    async function loadOverview(projectId) {
        const data = await PBAuth.api(`/stats/${projectId}/overview`);
        const fmt = (n) => (n || 0).toLocaleString();
        const fmtCost = (n) => `~$${(n || 0).toFixed(2)}`;

        document.getElementById('stat-today').textContent = fmt(data.today?.events);
        document.getElementById('stat-today-cost').textContent = fmtCost(data.today?.cost_usd);
        document.getElementById('stat-7d').textContent = fmt(data.last_7d?.events);
        document.getElementById('stat-7d-unique').textContent = `${fmt(data.last_7d?.unique_deployments)} unique deployments`;
        document.getElementById('stat-7d-cost').textContent = fmtCost(data.last_7d?.cost_usd);
        document.getElementById('stat-30d').textContent = fmt(data.last_30d?.events);
        document.getElementById('stat-30d-unique').textContent = `${fmt(data.last_30d?.unique_deployments)} unique deployments`;
        document.getElementById('stat-30d-cost').textContent = fmtCost(data.last_30d?.cost_usd);
        document.getElementById('stat-lifetime').textContent = fmt(data.lifetime?.events);
        document.getElementById('stat-lifetime-unique').textContent = `${fmt(data.lifetime?.unique_deployments)} unique deployments`;
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

        // Active Deployments
        const deployments = data.deployments || [];
        const depCountEl = document.getElementById('deployment-count');
        if (depCountEl) depCountEl.textContent = deployments.length;
        const depList = document.getElementById('deployment-list');
        if (depList) {
            depList.innerHTML = deployments.length === 0
                ? '<p class="text-pb-muted text-center py-4">No deployment data</p>'
                : deployments.map(d => `
                    <div class="flex items-center gap-2 py-1 border-b border-pb-border/30 last:border-0" title="Full ID: ${esc(d.full_id)}">
                        <span class="font-mono text-pb-accent flex-shrink-0">${esc(d.id)}</span>
                        <span class="text-pb-text truncate">${esc(d.version || 'unknown')}</span>
                        <span class="ml-auto text-pb-muted flex-shrink-0">${[d.os, d.country].filter(Boolean).join(' · ')}</span>
                    </div>
                `).join('');
        }

        // OS doughnut + count list
        const osData = data.os || [];
        PBCharts.doughnut('chart-os', osData);
        const osListEl = document.getElementById('os-count-list');
        if (osListEl) {
            const osTotal = osData.reduce((s, o) => s + o.count, 0);
            osListEl.innerHTML = osData.map(o => {
                const pct = osTotal > 0 ? Math.round(o.count / osTotal * 100) : 0;
                return `<div class="flex items-center gap-1 text-xs" title="${esc(o.name)} — ${o.count} unique deployments (${pct}%)">
                    <span class="text-pb-text flex-1">${esc(o.name)}</span>
                    <span class="text-pb-muted font-mono">${o.count}</span>
                    <span class="text-[9px] text-pb-muted">(${pct}%)</span>
                </div>`;
            }).join('') || '<p class="text-[10px] text-pb-muted">No OS data</p>';
        }

        // Version list
        const versionList = document.getElementById('version-list');
        const versions = data.versions || [];
        const maxCount = versions[0]?.count || 1;
        versionList.innerHTML = versions.map(v => `
            <div class="flex items-center gap-2" title="${esc(v.name || 'Older version')} — ${v.count} unique deployments">
                <span class="text-xs font-mono w-2/5 truncate ${v.name ? 'text-pb-text' : 'text-pb-muted italic'}">${esc(v.name || 'Older version')}</span>
                <div class="flex-1 h-2 rounded-full bg-pb-bg overflow-hidden">
                    <div class="h-full rounded-full bg-gradient-to-r from-pb-accent to-purple-500" style="width: ${Math.round(v.count / maxCount * 100)}%"></div>
                </div>
                <span class="text-xs text-pb-muted w-8 text-right flex-shrink-0">${v.count}</span>
            </div>
        `).join('');

        // Country list
        const countryList = document.getElementById('country-list');
        const countries = data.countries || [];
        const maxC = countries[0]?.count || 1;
        countryList.innerHTML = countries.map(c => `
            <div class="flex items-center gap-2" title="${esc(c.name)} — ${c.count} unique deployments">
                <span class="text-xs w-8 text-center flex-shrink-0">${countryFlag(c.name)}</span>
                <span class="text-xs min-w-[2rem] truncate text-pb-text">${esc(c.name)}</span>
                <div class="flex-1 h-2 rounded-full bg-pb-bg overflow-hidden">
                    <div class="h-full rounded-full bg-gradient-to-r from-pb-green to-cyan-500" style="width: ${Math.round(c.count / maxC * 100)}%"></div>
                </div>
                <span class="text-xs text-pb-muted w-10 text-right flex-shrink-0">${c.count}</span>
            </div>
        `).join('');

        // Model list
        const modelList = document.getElementById('model-list');
        const models = data.models || [];
        const maxM = models[0]?.count || 1;
        modelList.innerHTML = models.length === 0
            ? '<p class="text-xs text-pb-muted">No model data yet</p>'
            : models.map(m => `
                <div class="flex items-center gap-2" title="${esc(m.name || 'untagged')} — ${m.count} events">
                    <span class="text-xs font-mono w-2/5 truncate ${m.name ? 'text-pb-text' : 'text-pb-muted italic'}">${esc(m.name || 'untagged')}</span>
                    <div class="flex-1 h-2 rounded-full bg-pb-bg overflow-hidden">
                        <div class="h-full rounded-full bg-gradient-to-r from-pb-amber to-orange-500" style="width: ${Math.round(m.count / maxM * 100)}%"></div>
                    </div>
                    <span class="text-xs text-pb-muted w-10 text-right flex-shrink-0">${m.count}</span>
                </div>
            `).join('');

        // Event type list
        const etList = document.getElementById('event-type-list');
        const ets = data.event_types || [];
        const maxET = ets[0]?.count || 1;
        etList.innerHTML = ets.length === 0
            ? '<p class="text-xs text-pb-muted">No event data yet</p>'
            : ets.map(e => `
                <div class="flex items-center gap-2" title="${esc(e.name || 'untagged')} — ${e.count} events">
                    <span class="text-xs font-mono w-2/5 truncate ${e.name ? 'text-pb-text' : 'text-pb-muted italic'}">${esc(e.name || 'untagged')}</span>
                    <div class="flex-1 h-2 rounded-full bg-pb-bg overflow-hidden">
                        <div class="h-full rounded-full bg-gradient-to-r from-purple-500 to-pink-500" style="width: ${Math.round(e.count / maxET * 100)}%"></div>
                    </div>
                    <span class="text-xs text-pb-muted w-10 text-right flex-shrink-0">${e.count}</span>
                </div>
            `).join('');

        // Cost banner
        const costBanner = document.getElementById('total-cost-banner');
        if (costBanner) costBanner.textContent = `~$${(data.total_cost_usd || 0).toFixed(2)}`;
    }

    async function loadEvents(projectId) {
        const data = await PBAuth.api(`/stats/${projectId}/events?limit=100`);
        const list = document.getElementById('events-list');
        const events = data.events || [];
        const tz = Intl.DateTimeFormat().resolvedOptions().timeZone;

        list.innerHTML = events.map(e => {
            const ts = e.timestamp_id?.split('#')[0] || '';
            const time = ts ? new Date(ts).toLocaleString(undefined, {
                month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit',
                timeZoneName: 'short',
            }) : '';
            const props = typeof e.properties === 'object' ? e.properties : {};
            const uid = (e.distinct_id || '').substring(0, 8);
            const cost = parseFloat(e.cost_usd || props.cost_usd || 0);
            const costStr = cost > 0 ? `$${cost.toFixed(2)}` : '';
            return `
                <div class="flex items-center gap-2 py-1.5 border-b border-pb-border/30 last:border-0 min-w-[550px]">
                    <span class="px-1.5 py-0.5 rounded bg-pb-accent/10 text-pb-accent font-medium flex-shrink-0">${esc(e.event_type)}</span>
                    <span class="text-pb-muted flex-shrink-0 font-mono" title="Deployment: ${esc(e.distinct_id || '')}">${uid}</span>
                    <span class="text-pb-muted flex-shrink-0">${[props.version, props.os, e.country].filter(Boolean).join(' · ')}</span>
                    ${costStr ? `<span class="text-pb-accent flex-shrink-0 font-medium">${costStr}</span>` : ''}
                    <span class="ml-auto text-pb-muted/60 flex-shrink-0 whitespace-nowrap">${time}</span>
                </div>
            `;
        }).join('') || '<p class="text-pb-muted text-center py-4">No events in this period</p>';
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

    // ── GitHub Integration ─────────────────────────────────────────

    async function loadGitHub(projectId, days) {
        try {
            const params = (days !== undefined && days >= 0) ? `?${_buildDateParams()}` : '';
            const data = await PBAuth.api(`/stats/${projectId}/github${params}`);
            const section = document.getElementById('github-section');
            if (!section) return;
            section.classList.remove('hidden');

            // Dynamic period label
            const periodLabel = _currentDays === 0 ? '(lifetime)' : _currentDays === -1 ? '(custom)' : _currentDays === 1 ? '(today)' : `(${_currentDays}d)`;
            const clonesPeriod = document.getElementById('gh-clones-period');
            const viewsPeriod = document.getElementById('gh-views-period');
            if (clonesPeriod) clonesPeriod.textContent = periodLabel;
            if (viewsPeriod) viewsPeriod.textContent = periodLabel;

            document.getElementById('gh-stars').textContent = (data.stars || 0).toLocaleString();
            document.getElementById('gh-watchers').textContent = `${(data.watchers || 0).toLocaleString()} watchers`;
            document.getElementById('gh-forks').textContent = (data.forks || 0).toLocaleString();
            document.getElementById('gh-issues').textContent = `${data.open_issues || 0} open issues · ${data.contributors || 0} contributors`;

            if (data.has_traffic) {
                // Sum from daily data (respects period filter) when available
                const daily = data.daily || [];
                if (daily.length > 0) {
                    const totalClones = daily.reduce((s, d) => s + (d.clones || 0), 0);
                    const totalUniqueClones = daily.reduce((s, d) => s + (d.clones_unique || 0), 0);
                    const totalViews = daily.reduce((s, d) => s + (d.views || 0), 0);
                    const totalUniqueViews = daily.reduce((s, d) => s + (d.views_unique || 0), 0);
                    document.getElementById('gh-clones').textContent = totalClones.toLocaleString();
                    document.getElementById('gh-clones-unique').textContent = `${totalUniqueClones} unique cloners`;
                    document.getElementById('gh-views').textContent = totalViews.toLocaleString();
                    document.getElementById('gh-views-unique').textContent = `${totalUniqueViews} unique visitors`;
                } else {
                    document.getElementById('gh-clones').textContent = (data.total_clones_14d || 0).toLocaleString();
                    document.getElementById('gh-clones-unique').textContent = `${data.unique_cloners_14d || 0} unique cloners`;
                    document.getElementById('gh-views').textContent = (data.total_views_14d || 0).toLocaleString();
                    document.getElementById('gh-views-unique').textContent = `${data.unique_visitors_14d || 0} unique visitors`;
                }
            } else {
                document.getElementById('gh-clones').textContent = '—';
                const clonesBadge = document.getElementById('gh-clones-unique');
                if (clonesBadge) { clonesBadge.textContent = 'needs Administration:read'; clonesBadge.className = 'text-[10px] px-1.5 py-0.5 rounded bg-pb-amber/10 text-pb-amber mt-1.5 inline-block'; }
                document.getElementById('gh-views').textContent = '—';
                const viewsBadge = document.getElementById('gh-views-unique');
                if (viewsBadge) { viewsBadge.textContent = 'needs Administration:read'; viewsBadge.className = 'text-[10px] px-1.5 py-0.5 rounded bg-pb-amber/10 text-pb-amber mt-1.5 inline-block'; }
            }

            const fetchedAt = document.getElementById('gh-fetched-at');
            if (fetchedAt) {
                let msg = data.fetched_at ? `Last fetched: ${new Date(data.fetched_at).toLocaleString()}` : '';
                if (data.language) msg += ` · ${data.language}`;
                if (data.traffic_note) msg += ` · ${data.traffic_note}`;
                fetchedAt.textContent = msg;
            }

            // Referrers
            const refEl = document.getElementById('gh-referrers');
            if (refEl) {
                refEl.innerHTML = (data.referrers || []).map(r => `
                    <div class="flex justify-between"><span>${esc(r.referrer)}</span><span class="text-pb-muted">${r.count} (${r.uniques} unique)</span></div>
                `).join('') || '<p class="text-pb-muted">No referrer data</p>';
            }

            // Popular paths
            const pathEl = document.getElementById('gh-paths');
            if (pathEl) {
                pathEl.innerHTML = (data.popular_paths || []).map(p => `
                    <div class="flex justify-between"><span class="truncate mr-2">${esc(p.path)}</span><span class="text-pb-muted flex-shrink-0">${p.count}</span></div>
                `).join('') || '<p class="text-pb-muted">No path data</p>';
            }

            // GitHub traffic chart
            if (data.daily && data.daily.length > 0) {
                PBCharts.githubTraffic('chart-github', data.daily);
            }
        } catch (err) {
            console.error('GitHub data load failed:', err);
        }
    }

    async function handleSaveGitHub() {
        if (!currentProject) return;
        const repoField = document.getElementById('project-github-repo');
        const repo = repoField?.value?.trim() || '';
        const savedRepo = repoField?.dataset.saved || '';
        const tokenInput = document.getElementById('project-github-token')?.value?.trim();
        const status = document.getElementById('github-status');
        const repoChanged = repo !== savedRepo;

        // If repo changed, require a new token
        if (repoChanged && repo && !tokenInput) {
            if (status) { status.textContent = 'New token required when changing the repository'; status.className = 'text-[10px] text-pb-amber'; }
            return;
        }

        const body = { github_repo: repo };
        if (tokenInput) {
            body.github_token = tokenInput;
        } else if (repoChanged && !repo) {
            // Clearing the repo — also clear the token
            body.github_token = '';
        }

        if (status) { status.textContent = 'Validating...'; status.className = 'text-[10px] text-pb-accent'; }

        try {
            const result = await PBAuth.api(`/projects/${currentProject.project_id}`, { method: 'PATCH', body });
            currentProject = result;
            const gs = result.github_status || {};
            if (gs.valid) {
                const trafficMsg = gs.traffic_access
                    ? 'traffic access OK — fetching data...'
                    : 'no traffic access — edit PAT and add Administration:read, or use Classic PAT with repo scope';
                const color = gs.traffic_access ? 'text-[10px] text-green-400' : 'text-[10px] text-pb-amber';
                if (status) { status.textContent = `Connected: ${gs.full_name} (${gs.stars} stars) — ${trafficMsg}`; status.className = color; }
                // Auto-fetch traffic data after successful save
                try {
                    await PBAuth.api('/github/fetch', { method: 'POST' });
                    if (status && gs.traffic_access) { status.textContent = `Connected: ${gs.full_name} (${gs.stars} stars) — traffic data fetched`; }
                } catch (_) {}
                loadGitHub(currentProject.project_id);
            } else {
                if (status) { status.textContent = gs.error || 'Validation failed'; status.className = 'text-[10px] text-red-400'; }
            }
        } catch (err) {
            if (status) { status.textContent = err.message; status.className = 'text-[10px] text-red-400'; }
        }
    }

    async function handleFetchGitHub() {
        if (!currentProject) return;
        const status = document.getElementById('github-status');
        if (status) { status.textContent = 'Fetching traffic...'; status.className = 'text-[10px] text-pb-accent'; }
        try {
            await PBAuth.api('/github/fetch', { method: 'POST' });
            if (status) { status.textContent = 'Traffic data fetched'; status.className = 'text-[10px] text-green-400'; }
            loadGitHub(currentProject.project_id);
        } catch (err) {
            if (status) { status.textContent = err.message; status.className = 'text-[10px] text-red-400'; }
        }
    }

    // ── Auto Refresh ─────────────────────────────────────────────

    function _startAutoRefresh(projectId) {
        if (_autoRefreshTimer) clearInterval(_autoRefreshTimer);
        if (_countdownTimer) clearInterval(_countdownTimer);
        _lastRefresh = Date.now();

        _autoRefreshTimer = setInterval(() => {
            if (document.visibilityState === 'hidden') return;  // Don't refresh in background
            const idle = (Date.now() - _lastInteraction) > IDLE_THRESHOLD_MS;
            if (!idle) return;
            if (currentProject?.project_id !== projectId) return;

            _lastRefresh = Date.now();
            loadOverview(projectId);
            loadTimeseries(projectId);
            loadBreakdown(projectId);
            loadEvents(projectId);
        }, AUTO_REFRESH_MS);

        // Countdown display — update every second
        _countdownTimer = setInterval(() => {
            const el = document.getElementById('refresh-countdown');
            if (!el) return;

            if (document.visibilityState === 'hidden') {
                el.textContent = 'idle';
                return;
            }

            const elapsed = Date.now() - _lastRefresh;
            const remaining = Math.max(0, AUTO_REFRESH_MS - elapsed);
            const mins = Math.floor(remaining / 60000);
            const secs = Math.floor((remaining % 60000) / 1000);
            const idle = (Date.now() - _lastInteraction) > IDLE_THRESHOLD_MS;
            el.textContent = idle ? `${mins}:${String(secs).padStart(2, '0')}` : 'active';
        }, 1000);
    }

    // ── Admin Management ────────────────────────────────────────

    async function loadAdmins() {
        const list = document.getElementById('admin-list');
        if (!list) return;
        list.innerHTML = '<p class="text-xs text-pb-muted text-center py-2">Loading...</p>';
        try {
            const data = await PBAuth.api('/admin/users');
            const users = data.users || [];
            list.innerHTML = users.map(u => {
                const roleColor = u.role === 'Admin' ? 'bg-pb-accent/10 text-pb-accent' : 'bg-emerald-500/10 text-emerald-400';
                const statusColor = u.status === 'CONFIRMED' ? 'bg-green-500/10 text-green-400' : 'bg-amber-500/10 text-amber-400';
                return `
                    <div class="flex items-center justify-between p-2 rounded-lg bg-pb-bg border border-pb-border">
                        <div class="flex items-center gap-2">
                            <span class="text-sm">${esc(u.email)}</span>
                            <span class="text-[10px] px-1.5 py-0.5 rounded font-medium ${roleColor}">${u.role}</span>
                            <span class="text-[10px] px-1.5 py-0.5 rounded ${statusColor}">${u.status}</span>
                        </div>
                        <span class="text-[10px] text-pb-muted">${new Date(u.created).toLocaleDateString()}</span>
                    </div>
                `;
            }).join('') || '<p class="text-xs text-pb-muted text-center py-2">No users</p>';
        } catch (err) {
            list.innerHTML = `<p class="text-xs text-red-400 text-center py-2">${err.message}</p>`;
        }
    }

    async function handleInviteAdmin() {
        const email = document.getElementById('invite-email')?.value?.trim();
        const role = document.getElementById('invite-role')?.value || 'Viewer';
        const status = document.getElementById('invite-status');
        if (!email) return;

        try {
            await PBAuth.api('/admin/invite', { method: 'POST', body: { email, role } });
            if (status) {
                status.className = 'text-xs mt-1 text-green-400';
                status.textContent = `Invited ${email} — temporary password sent via email`;
                status.classList.remove('hidden');
            }
            document.getElementById('invite-email').value = '';
            loadAdmins();
        } catch (err) {
            if (status) {
                status.className = 'text-xs mt-1 text-red-400';
                status.textContent = err.message;
                status.classList.remove('hidden');
            }
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
