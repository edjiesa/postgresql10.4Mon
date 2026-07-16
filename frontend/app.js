// Global variables
let activeTab = 'dashboard';
let databasesCache = [];
let activeDetailDbId = null;
let detailPollInterval = null;
let dashboardPollInterval = null;

// On document load
document.addEventListener('DOMContentLoaded', () => {
    initApp();
});

function initApp() {
    // Initial fetch of databases and settings
    fetchDatabases();
    fetchAlertSettings();
    fetchLogs();
    
    // Start background polling for dashboard (every 10 seconds)
    dashboardPollInterval = setInterval(fetchDatabases, 10000);
}

// Navigation / Tabs switching
function switchTab(tabName) {
    activeTab = tabName;
    
    // Toggle active nav classes
    document.querySelectorAll('.nav-item').forEach(btn => btn.classList.remove('active'));
    document.getElementById(`nav-${tabName}`).classList.add('active');
    
    // Toggle active tab content
    document.querySelectorAll('.tab-content').forEach(content => content.classList.remove('active'));
    document.getElementById(`tab-${tabName}`).classList.add('active');
    
    // Update Header Text
    const pageTitle = document.getElementById('page-title');
    const pageSubtitle = document.getElementById('page-subtitle');
    
    if (tabName === 'dashboard') {
        pageTitle.innerText = "Performance Dashboard";
        pageSubtitle.innerText = "Real-time status of monitored PostgreSQL servers";
        fetchDatabases();
    } else if (tabName === 'alerts') {
        pageTitle.innerText = "Alert Configuration";
        pageSubtitle.innerText = "Configure Telegram, Discord, Slack, or n8n endpoints for alerts";
        fetchAlertSettings();
    } else if (tabName === 'logs') {
        pageTitle.innerText = "Event Log History";
        pageSubtitle.innerText = "Audit logs of performance alerts and database issues";
        fetchLogs();
    }
}

// Show Toast Message
function showToast(message, type = 'info') {
    const container = document.getElementById('toast-container');
    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    toast.innerText = message;
    
    container.appendChild(toast);
    
    // Remove toast after 4s
    setTimeout(() => {
        toast.style.animation = 'fadeOut 0.5s forwards';
        setTimeout(() => toast.remove(), 500);
    }, 4000);
}

// --- Databases CRUD & UI Rendering ---

async function fetchDatabases() {
    try {
        const response = await fetch('/api/databases');
        if (!response.ok) throw new Error("Failed to fetch databases");
        const data = await response.json();
        databasesCache = data;
        renderDatabases(data);
    } catch (error) {
        console.error(error);
        showToast("Error scanning database metrics: " + error.message, 'error');
    }
}

function renderDatabases(databases) {
    const listElement = document.getElementById('databases-list');
    
    if (databases.length === 0) {
        listElement.innerHTML = `
            <div class="loading-state">
                <p style="font-size: 1.25rem; font-weight:600; color:white;">No Databases Configured</p>
                <p>Click the "Add Database" button above to register your first remote PostgreSQL server.</p>
            </div>
        `;
        return;
    }
    
    listElement.innerHTML = '';
    
    databases.forEach(db => {
        const metrics = db.metrics || {};
        const isOnline = metrics.status === 'online';
        const isPending = metrics.status === 'pending';
        const isOffline = metrics.status === 'offline';
        
        let statusText = 'Pending';
        let statusClass = 'pending';
        if (isOnline) {
            statusText = 'Online';
            statusClass = 'online';
        } else if (isOffline) {
            statusText = 'Offline';
            statusClass = 'offline';
        }
        
        // Connection percentage calculation
        const activeConn = metrics.active_connections || 0;
        const maxConn = metrics.max_connections || 100;
        const connPct = maxConn > 0 ? Math.min(Math.round((activeConn / maxConn) * 100), 100) : 0;
        
        let meterColorClass = '';
        if (connPct >= 90) meterColorClass = 'critical';
        else if (connPct >= db.max_conn_threshold) meterColorClass = 'warning';
        
        // Active queries alerts count
        const slowQueryCount = (metrics.slow_queries || []).length;
        const blockingLockCount = (metrics.blocking_queries || []).length;
        
        const card = document.createElement('div');
        card.className = `db-card glass ${isOffline ? 'offline' : ''}`;
        
        // Assemble Card Body
        let bodyContent = '';
        if (isOffline) {
            bodyContent = `
                <div class="offline-notice">
                    <span>⚠️</span>
                    <div>
                        <strong>Connection Error:</strong>
                        <p style="font-size:0.75rem; margin-top:2px;">${metrics.error || 'Server unreachable'}</p>
                    </div>
                </div>
            `;
        } else {
            bodyContent = `
                <div class="info-grid">
                    <div class="info-item">
                        <span class="info-label">DB Size</span>
                        <span class="info-val">${metrics.db_size || '-'}</span>
                    </div>
                    <div class="info-item">
                        <span class="info-label">Cache Hit</span>
                        <span class="info-val">${metrics.cache_hit_ratio !== undefined ? metrics.cache_hit_ratio + '%' : '-'}</span>
                    </div>
                    <div class="info-item">
                        <span class="info-label">Index Hit</span>
                        <span class="info-val">${metrics.index_hit_ratio !== undefined ? metrics.index_hit_ratio + '%' : '-'}</span>
                    </div>
                    <div class="info-item">
                        <span class="info-label">Slow/Locked</span>
                        <span class="info-val">
                            <span class="${slowQueryCount > 0 ? 'text-warning' : ''}" style="font-weight:700;">${slowQueryCount}</span> / 
                            <span class="${blockingLockCount > 0 ? 'text-critical' : ''}" style="font-weight:700;">${blockingLockCount}</span>
                        </span>
                    </div>
                </div>

                <div class="meter-group">
                    <div class="meter-label">
                        <span>Connection Pool Usage</span>
                        <span>${activeConn}/${maxConn} (${connPct}%)</span>
                    </div>
                    <div class="meter-bar-container">
                        <div class="meter-bar-fill ${meterColorClass}" style="width: ${connPct}%;"></div>
                    </div>
                </div>
            `;
        }
        
        card.innerHTML = `
            <div class="card-header">
                <div class="db-identity">
                    <span class="db-title">${db.name}</span>
                    <span class="db-endpoint">${db.host}:${db.port} | ${db.dbname}</span>
                </div>
                <div class="status-indicator">
                    <span class="status-dot ${statusClass}"></span>
                    <span>${statusText}</span>
                </div>
            </div>
            <div class="card-body">
                ${bodyContent}
            </div>
            <div class="card-footer">
                <button class="btn btn-primary" onclick="openDetailModal(${db.id})" ${isOffline || isPending ? 'disabled' : ''}>
                    Manage
                </button>
                <button class="btn btn-secondary btn-icon-only" title="Edit Server Config" onclick="openDbModal(${db.id})">
                    ⚙️
                </button>
                <div class="spacer"></div>
                <button class="btn btn-danger btn-icon-only" title="Delete Database" onclick="deleteDatabase(${db.id}, '${db.name}')">
                    🗑️
                </button>
            </div>
        `;
        
        listElement.appendChild(card);
    });
}

// --- Database Add/Edit Dialog Modal ---

function openDbModal(dbId = null) {
    const modal = document.getElementById('db-modal');
    const form = document.getElementById('db-form');
    const title = document.getElementById('db-modal-title');
    form.reset();
    
    if (dbId) {
        // Edit Mode
        const db = databasesCache.find(d => d.id === dbId);
        if (!db) return;
        
        title.innerText = `Edit Database: ${db.name}`;
        document.getElementById('db-form-id').value = db.id;
        document.getElementById('db-name').value = db.name;
        document.getElementById('db-ssl').value = db.sslmode;
        document.getElementById('db-host').value = db.host;
        document.getElementById('db-port').value = db.port;
        document.getElementById('db-username').value = db.username;
        document.getElementById('db-password').value = db.password;
        document.getElementById('db-dbname').value = db.dbname;
        document.getElementById('db-check-interval').value = db.check_interval;
        document.getElementById('db-slow-query').value = db.slow_query_threshold;
        document.getElementById('db-max-conn').value = db.max_conn_threshold;
    } else {
        // Create Mode
        title.innerText = "Add PostgreSQL Database";
        document.getElementById('db-form-id').value = '';
    }
    
    modal.classList.add('active');
}

function closeDbModal() {
    document.getElementById('db-modal').classList.remove('active');
}

// Read Form Data into JS object
defFormObject = () => {
    return {
        name: document.getElementById('db-name').value,
        sslmode: document.getElementById('db-ssl').value,
        host: document.getElementById('db-host').value,
        port: parseInt(document.getElementById('db-port').value),
        username: document.getElementById('db-username').value,
        password: document.getElementById('db-password').value,
        dbname: document.getElementById('db-dbname').value,
        check_interval: parseInt(document.getElementById('db-check-interval').value),
        slow_query_threshold: parseInt(document.getElementById('db-slow-query').value),
        max_conn_threshold: parseInt(document.getElementById('db-max-conn').value),
        is_active: 1
    };
};

async function testConnection() {
    const data = defFormObject();
    showToast("Testing connection to remote PostgreSQL...", "info");
    
    try {
        const response = await fetch('/api/databases/test', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data)
        });
        
        if (!response.ok) throw new Error("Server communication failure.");
        const result = await response.json();
        
        if (result.success) {
            showToast(result.message, "success");
        } else {
            showToast("Connection failed: " + result.message, "error");
        }
    } catch (e) {
        showToast("Error running test: " + e.message, "error");
    }
}

async function saveDbForm(event) {
    event.preventDefault();
    const dbId = document.getElementById('db-form-id').value;
    const data = defFormObject();
    
    const url = dbId ? `/api/databases/${dbId}` : '/api/databases';
    const method = dbId ? 'PUT' : 'POST';
    
    showToast("Saving database settings...", "info");
    
    try {
        const response = await fetch(url, {
            method: method,
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data)
        });
        
        const result = await response.json();
        if (!response.ok) {
            throw new Error(result.detail || "Failed to save configuration.");
        }
        
        showToast(dbId ? "Database updated successfully." : "Database added successfully.", "success");
        closeDbModal();
        fetchDatabases();
    } catch (e) {
        showToast(e.message, "error");
    }
}

async function deleteDatabase(id, name) {
    if (!confirm(`Are you sure you want to delete database "${name}"?\nThis stops monitoring and logging.`)) {
        return;
    }
    
    try {
        const response = await fetch(`/api/databases/${id}`, { method: 'DELETE' });
        if (!response.ok) throw new Error("Delete request failed.");
        showToast(`Database "${name}" deleted.`, "success");
        fetchDatabases();
    } catch (e) {
        showToast("Error deleting database: " + e.message, "error");
    }
}

// --- Detail Metrics & Live Query Management Modal ---

async function openDetailModal(dbId) {
    activeDetailDbId = dbId;
    
    // Reset tab to overview
    switchModalTab('overview');
    
    // Trigger immediate update
    await fetchDetailMetrics();
    
    document.getElementById('detail-modal').classList.add('active');
    
    // Start live query monitoring poll (every 5 seconds)
    detailPollInterval = setInterval(fetchDetailMetrics, 5000);
}

function closeDetailModal() {
    document.getElementById('detail-modal').classList.remove('active');
    activeDetailDbId = null;
    if (detailPollInterval) {
        clearInterval(detailPollInterval);
        detailPollInterval = null;
    }
    fetchDatabases(); // Refresh main dashboard after details view closes
}

async function fetchDetailMetrics() {
    if (!activeDetailDbId) return;
    
    try {
        const response = await fetch(`/api/databases/${activeDetailDbId}`);
        if (!response.ok) throw new Error("Failed to query database details");
        const db = await response.json();
        
        const metrics = db.metrics || {};
        if (metrics.status === 'offline') {
            closeDetailModal();
            showToast(`Database '${db.name}' went offline.`, 'error');
            return;
        }
        
        // Fill header
        document.getElementById('detail-db-name').innerText = `${db.name} Details`;
        
        // Fill Mini stats
        document.getElementById('detail-val-version').innerText = metrics.pg_version ? metrics.pg_version.split(',')[0] : '-';
        document.getElementById('detail-val-size').innerText = metrics.db_size || '-';
        document.getElementById('detail-val-conn').innerText = `${metrics.active_connections || 0} / ${metrics.max_connections || 100}`;
        document.getElementById('detail-val-cache').innerText = metrics.cache_hit_ratio !== undefined ? metrics.cache_hit_ratio + '%' : '-';
        document.getElementById('detail-val-index').innerText = metrics.index_hit_ratio !== undefined ? metrics.index_hit_ratio + '%' : '-';
        
        // Storage & Temp File metrics
        const tempFiles = metrics.temp_files !== undefined ? metrics.temp_files : 0;
        const tempBytes = metrics.temp_bytes !== undefined ? metrics.temp_bytes : 0;
        document.getElementById('detail-val-temp-files').innerText = tempFiles.toLocaleString();
        document.getElementById('detail-val-temp-bytes').innerText = formatBytes(tempBytes);
        

        
        // Render Active Queries Table (showing all running queries)
        const activeQueries = metrics.active_queries || [];
        document.getElementById('detail-slow-count').innerText = activeQueries.length;
        const slowTbody = document.getElementById('detail-slow-tbody');
        
        if (activeQueries.length === 0) {
            slowTbody.innerHTML = `
                <tr>
                    <td colspan="7" class="text-center" style="color:var(--text-muted); padding:2rem;">No active queries currently executing in the engine.</td>
                </tr>
            `;
        } else {
            slowTbody.innerHTML = '';
            activeQueries.forEach(q => {
                const isSlow = q.duration_seconds >= db.slow_query_threshold;
                const durationStyle = isSlow ? 'font-weight:700; color:var(--color-critical);' : 'font-weight:600; color:var(--color-online);';
                const slowWarning = isSlow ? ' <span class="badge badge-critical" style="margin-left:5px; font-size:0.65rem; padding:1px 4px; border:none; display:inline;">SLOW ⚠️</span>' : '';
                
                const tr = document.createElement('tr');
                tr.innerHTML = `
                    <td style="font-family:var(--font-mono); font-weight:600;">${q.pid}</td>
                    <td>${q.username}</td>
                    <td>${q.client_ip || 'local'}</td>
                    <td style="${durationStyle}">${q.duration_seconds}s${slowWarning}</td>
                    <td><span class="status-indicator">${q.state}</span></td>
                    <td><code class="query-text" title="${escapeHtml(q.query)}">${escapeHtml(q.query)}</code></td>
                    <td>
                        <button class="btn btn-danger btn-icon-only" style="padding:0.4rem; height:28px; width:28px;" title="Kill Query" onclick="killQuery(${db.id}, ${q.pid}, '${escapeHtml(q.query)}')">
                            ⚡
                        </button>
                    </td>
                `;
                slowTbody.appendChild(tr);
            });
        }
        
        // Render Idle & In-Transaction Queries Table
        const idleQueries = metrics.idle_queries || [];
        document.getElementById('detail-idle-count').innerText = idleQueries.length;
        const idleTbody = document.getElementById('detail-idle-tbody');
        
        if (idleQueries.length === 0) {
            idleTbody.innerHTML = `
                <tr>
                    <td colspan="7" class="text-center" style="color:var(--text-muted); padding:2rem;">No idle or inactive sessions detected.</td>
                </tr>
            `;
        } else {
            idleTbody.innerHTML = '';
            idleQueries.forEach(q => {
                const tr = document.createElement('tr');
                tr.innerHTML = `
                    <td style="font-family:var(--font-mono); font-weight:600;">${q.pid}</td>
                    <td>${q.username}</td>
                    <td>${q.client_ip || 'local'}</td>
                    <td style="font-weight:600; color:var(--text-muted);">${q.duration_seconds}s</td>
                    <td><span class="status-indicator">${q.state}</span></td>
                    <td><code class="query-text" title="${escapeHtml(q.query)}">${escapeHtml(q.query) || 'None'}</code></td>
                    <td>
                        <button class="btn btn-danger btn-icon-only" style="padding:0.4rem; height:28px; width:28px;" title="Kill Session" onclick="killQuery(${db.id}, ${q.pid}, 'Idle session')">
                            ⚡
                        </button>
                    </td>
                `;
                idleTbody.appendChild(tr);
            });
        }
        
        // Render Blocking Locks Table
        const blockingQueries = metrics.blocking_queries || [];
        document.getElementById('detail-lock-count').innerText = blockingQueries.length;
        const lockTbody = document.getElementById('detail-lock-tbody');
        
        if (blockingQueries.length === 0) {
            lockTbody.innerHTML = `
                <tr>
                    <td colspan="6" class="text-center" style="color:var(--text-muted); padding:2rem;">No blockages detected. Database is operating smoothly.</td>
                </tr>
            `;
        } else {
            lockTbody.innerHTML = '';
            blockingQueries.forEach(l => {
                const tr = document.createElement('tr');
                tr.innerHTML = `
                    <td style="color:var(--color-critical); font-family:var(--font-mono); font-weight:600;">${l.blocked_pid}</td>
                    <td><code class="query-text">${escapeHtml(l.blocked_statement)}</code></td>
                    <td style="color:var(--color-online); font-family:var(--font-mono); font-weight:600;">${l.blocking_pid}</td>
                    <td>${l.blocking_user}</td>
                    <td><code class="query-text">${escapeHtml(l.blocking_statement)}</code></td>
                    <td style="font-weight:600; color:var(--color-critical);">${l.blocked_duration_seconds}s</td>
                `;
                lockTbody.appendChild(tr);
            });
        }
        
        // Render Autovacuum Workers Table
        const vacuumWorkers = metrics.autovacuum_workers || [];
        document.getElementById('detail-vacuum-workers-count').innerText = vacuumWorkers.length;
        document.getElementById('detail-badge-vacuum').innerText = vacuumWorkers.length;
        const vacWorkersTbody = document.getElementById('detail-vacuum-workers-tbody');
        if (vacuumWorkers.length === 0) {
            vacWorkersTbody.innerHTML = `
                <tr>
                    <td colspan="5" class="text-center" style="color:var(--text-muted); padding:1.5rem;">No active autovacuum workers running.</td>
                </tr>
            `;
        } else {
            vacWorkersTbody.innerHTML = '';
            vacuumWorkers.forEach(w => {
                const tr = document.createElement('tr');
                tr.innerHTML = `
                    <td style="font-family:var(--font-mono); font-weight:600;">${w.pid}</td>
                    <td><code class="query-text">${escapeHtml(w.query)}</code></td>
                    <td><span class="status-indicator">${w.state}</span></td>
                    <td style="font-weight:600; color:var(--color-online);">${w.duration_seconds}s</td>
                    <td>
                        <button class="btn btn-danger btn-icon-only" style="padding:0.4rem; height:28px; width:28px;" title="Kill Worker" onclick="killQuery(${db.id}, ${w.pid}, 'Autovacuum worker')">
                            ⚡
                        </button>
                    </td>
                `;
                vacWorkersTbody.appendChild(tr);
            });
        }

        // Render Dead Tuples (Tables Needing Vacuum) Table
        const deadTables = metrics.dead_tuples_tables || [];
        const deadTbody = document.getElementById('detail-dead-tuples-tbody');
        if (deadTables.length === 0) {
            deadTbody.innerHTML = `
                <tr>
                    <td colspan="6" class="text-center" style="color:var(--text-muted); padding:1.5rem;">No tables stats available.</td>
                </tr>
            `;
        } else {
            deadTbody.innerHTML = '';
            deadTables.forEach(t => {
                const tr = document.createElement('tr');
                const isHighBloat = t.dead_tuples_ratio > 10 && t.dead_tuples > 1000;
                const ratioStyle = isHighBloat ? 'color:var(--color-critical); font-weight:700;' : 'font-weight:600;';
                
                tr.innerHTML = `
                    <td style="font-weight:600;">${t.table_name}</td>
                    <td style="font-family:var(--font-mono);">${t.dead_tuples.toLocaleString()}</td>
                    <td style="font-family:var(--font-mono); color:var(--text-muted);">${t.live_tuples.toLocaleString()}</td>
                    <td style="${ratioStyle}">${t.dead_tuples_ratio}%</td>
                    <td style="font-size:0.75rem; color:var(--text-muted);">${t.last_vacuum ? new Date(t.last_vacuum).toLocaleString() : 'Never'}</td>
                    <td style="font-size:0.75rem; color:var(--text-muted);">${t.last_autovacuum ? new Date(t.last_autovacuum).toLocaleString() : 'Never'}</td>
                `;
                deadTbody.appendChild(tr);
            });
        }

        // Render Replication Stats Table & Recovery Info
        const repStats = metrics.replication_stats || { is_replica: false, replica_lag_seconds: 0.0, standby_clients: [] };
        const standbyTbody = document.getElementById('detail-replica-standby-tbody');
        const receiverDiv = document.getElementById('detail-replica-receiver-info');
        const statusLabel = document.getElementById('detail-replica-status-label');

        if (repStats.is_replica) {
            statusLabel.innerText = "Replica";
            statusLabel.className = "badge badge-info";
            standbyTbody.innerHTML = `
                <tr>
                    <td colspan="5" class="text-center" style="color:var(--text-muted); padding:1.5rem;">Database is running in recovery mode (replica). Standby metrics are not applicable.</td>
                </tr>
            `;
            receiverDiv.style.display = 'block';
            document.getElementById('detail-replica-last-replay').innerText = repStats.last_replay_timestamp ? new Date(repStats.last_replay_timestamp).toLocaleString() : '-';
            const lagSecs = repStats.replica_lag_seconds;
            const lagElement = document.getElementById('detail-replica-lag-secs');
            lagElement.innerText = `${lagSecs}s`;
            lagElement.style.color = lagSecs > 60 ? 'var(--color-critical)' : 'var(--color-online)';
            document.getElementById('detail-replica-standby-count').innerText = '0';
        } else {
            statusLabel.innerText = "Primary";
            statusLabel.className = "badge badge-success";
            receiverDiv.style.display = 'none';
            const standbys = repStats.standby_clients || [];
            document.getElementById('detail-replica-standby-count').innerText = standbys.length;
            if (standbys.length === 0) {
                standbyTbody.innerHTML = `
                    <tr>
                        <td colspan="5" class="text-center" style="color:var(--text-muted); padding:1.5rem;">No standby replica servers connected.</td>
                    </tr>
                `;
            } else {
                standbyTbody.innerHTML = '';
                standbys.forEach(s => {
                    const tr = document.createElement('tr');
                    const lagStyle = s.lag_mb > 50 ? 'color:var(--color-critical); font-weight:700;' : 'font-weight:600; color:var(--color-online);';
                    tr.innerHTML = `
                        <td style="font-family:var(--font-mono); font-weight:600;">${s.standby_ip}</td>
                        <td>${s.application_name}</td>
                        <td><span class="status-indicator">${s.state}</span></td>
                        <td><span class="status-indicator">${s.sync_state}</span></td>
                        <td style="${lagStyle}">${s.lag_mb} MB</td>
                    `;
                    standbyTbody.appendChild(tr);
                });
            }
        }

        // Render Transaction ID Wraparound Tables
        const wrapStats = metrics.wraparound_stats || { db_wraparound: [], table_wraparound: [] };
        
        const dbXidTbody = document.getElementById('detail-txid-db-tbody');
        const dbWraps = wrapStats.db_wraparound || [];
        if (dbWraps.length === 0) {
            dbXidTbody.innerHTML = `
                <tr>
                    <td colspan="4" class="text-center" style="color:var(--text-muted); padding:1rem;">No database stats available.</td>
                </tr>
            `;
        } else {
            dbXidTbody.innerHTML = '';
            dbWraps.forEach(d => {
                const tr = document.createElement('tr');
                const isHighAge = d.wraparound_percent > 80;
                const progressColor = isHighAge ? 'var(--color-critical)' : 'var(--color-online)';
                tr.innerHTML = `
                    <td style="font-weight:600;">${d.datname}</td>
                    <td style="font-family:var(--font-mono);">${d.txid_age.toLocaleString()}</td>
                    <td style="font-family:var(--font-mono); color:var(--text-muted);">${d.txids_remaining.toLocaleString()}</td>
                    <td>
                        <div style="display:flex; align-items:center; gap:0.5rem;">
                            <div class="meter-bar-container" style="height:6px; flex-grow:1; background:rgba(255,255,255,0.05); border-radius:3px;">
                                <div class="meter-bar-fill ${isHighAge ? 'critical' : ''}" style="width:${d.wraparound_percent}%; height:100%; border-radius:3px;"></div>
                            </div>
                            <span style="font-size:0.75rem; font-weight:600; color:${progressColor}; width:45px; text-align:right;">${d.wraparound_percent}%</span>
                        </div>
                    </td>
                `;
                dbXidTbody.appendChild(tr);
            });
        }

        const tableXidTbody = document.getElementById('detail-txid-table-tbody');
        const tableWraps = wrapStats.table_wraparound || [];
        if (tableWraps.length === 0) {
            tableXidTbody.innerHTML = `
                <tr>
                    <td colspan="3" class="text-center" style="color:var(--text-muted); padding:1rem;">No table stats available.</td>
                </tr>
            `;
        } else {
            tableXidTbody.innerHTML = '';
            tableWraps.forEach(t => {
                const tr = document.createElement('tr');
                const isHighAge = t.table_wraparound_percent > 80;
                const progressColor = isHighAge ? 'var(--color-critical)' : 'var(--color-online)';
                tr.innerHTML = `
                    <td style="font-weight:600;">${t.table_name}</td>
                    <td style="font-family:var(--font-mono);">${t.table_age.toLocaleString()}</td>
                    <td>
                        <div style="display:flex; align-items:center; gap:0.5rem;">
                            <div class="meter-bar-container" style="height:6px; flex-grow:1; background:rgba(255,255,255,0.05); border-radius:3px;">
                                <div class="meter-bar-fill ${isHighAge ? 'critical' : ''}" style="width:${t.table_wraparound_percent}%; height:100%; border-radius:3px;"></div>
                            </div>
                            <span style="font-size:0.75rem; font-weight:600; color:${progressColor}; width:45px; text-align:right;">${t.table_wraparound_percent}%</span>
                        </div>
                    </td>
                `;
                tableXidTbody.appendChild(tr);
            });
        }

    } catch (e) {
        console.error(e);
        showToast("Error updating detail metrics: " + e.message, "error");
    }
}

async function killQuery(dbId, pid, querySummary) {
    const summaryText = querySummary.length > 60 ? querySummary.substring(0, 60) + '...' : querySummary;
    if (!confirm(`Are you absolutely sure you want to kill PID ${pid}?\nQuery: "${summaryText}"`)) {
        return;
    }
    
    showToast(`Sending termination signal to PID ${pid}...`, 'info');
    try {
        const response = await fetch(`/api/databases/${dbId}/kill/${pid}`, { method: 'POST' });
        const result = await response.json();
        
        if (!response.ok) {
            throw new Error(result.detail || "Failed to terminate query.");
        }
        showToast(result.message, 'success');
        fetchDetailMetrics();
    } catch (e) {
        showToast("Kill failed: " + e.message, 'error');
    }
}

// --- Alert Configuration Channels Management ---

async function fetchAlertSettings() {
    try {
        const response = await fetch('/api/alerts');
        if (!response.ok) throw new Error("Failed to load alerts config");
        const channels = await response.json();
        
        // 1. Telegram
        const tg = channels.telegram || { config: {}, is_enabled: false };
        document.getElementById('tg-enabled').checked = tg.is_enabled;
        document.getElementById('tg-bot-token').value = tg.config.bot_token || '';
        document.getElementById('tg-chat-id').value = tg.config.chat_id || '';
        
        // 2. Discord
        const dc = channels.discord || { config: {}, is_enabled: false };
        document.getElementById('discord-enabled').checked = dc.is_enabled;
        document.getElementById('discord-webhook-url').value = dc.config.webhook_url || '';
        
        // 3. Slack
        const sc = channels.slack || { config: {}, is_enabled: false };
        document.getElementById('slack-enabled').checked = sc.is_enabled;
        document.getElementById('slack-webhook-url').value = sc.config.webhook_url || '';
        
        // 4. n8n
        const n8n = channels.n8n || { config: {}, is_enabled: false };
        document.getElementById('n8n-enabled').checked = n8n.is_enabled;
        document.getElementById('n8n-webhook-url').value = n8n.config.webhook_url || '';
        
    } catch (e) {
        showToast("Error loading alert configs: " + e.message, 'error');
    }
}

async function saveAlertChannel(channel) {
    let config = {};
    let is_enabled = false;
    
    if (channel === 'telegram') {
        is_enabled = document.getElementById('tg-enabled').checked;
        config = {
            bot_token: document.getElementById('tg-bot-token').value,
            chat_id: document.getElementById('tg-chat-id').value
        };
    } else if (channel === 'discord') {
        is_enabled = document.getElementById('discord-enabled').checked;
        config = {
            webhook_url: document.getElementById('discord-webhook-url').value
        };
    } else if (channel === 'slack') {
        is_enabled = document.getElementById('slack-enabled').checked;
        config = {
            webhook_url: document.getElementById('slack-webhook-url').value
        };
    } else if (channel === 'n8n') {
        is_enabled = document.getElementById('n8n-enabled').checked;
        config = {
            webhook_url: document.getElementById('n8n-webhook-url').value
        };
    }
    
    showToast(`Saving ${channel} configuration...`, 'info');
    try {
        const response = await fetch(`/api/alerts/${channel}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ config, is_enabled })
        });
        
        if (!response.ok) {
            const err = await response.json();
            throw new Error(err.detail || "Failed to update channel settings");
        }
        showToast(`${channel.toUpperCase()} settings saved successfully.`, 'success');
        fetchAlertSettings();
    } catch (e) {
        showToast(e.message, 'error');
    }
}

// --- Alert Logs History ---

async function fetchLogs() {
    try {
        const response = await fetch('/api/logs');
        if (!response.ok) throw new Error("Failed to load logs");
        const logs = await response.json();
        renderLogs(logs);
    } catch (e) {
        showToast("Error loading logs: " + e.message, 'error');
    }
}

function renderLogs(logs) {
    const tbody = document.getElementById('logs-tbody');
    const badge = document.getElementById('logs-badge');
    
    // Update sidebar logs count badge
    badge.innerText = logs.length;
    badge.setAttribute('data-count', logs.length);
    
    if (logs.length === 0) {
        tbody.innerHTML = `
            <tr>
                <td colspan="5" class="text-center" style="color:var(--text-muted); padding:2rem;">No alert logs recorded.</td>
            </tr>
        `;
        return;
    }
    
    tbody.innerHTML = '';
    logs.forEach(log => {
        const date = new Date(log.created_at + 'Z'); // Handle ISO as UTC
        const formattedDate = date.toLocaleString();
        
        let sevClass = '';
        if (log.severity === 'critical') sevClass = 'badge badge-critical';
        else if (log.severity === 'warning') sevClass = 'badge badge-warning';
        else sevClass = 'badge badge-info';
        
        // Formatting specific details
        let detailsHtml = '';
        if (log.details) {
            const details = log.details;
            if (log.alert_type === 'slow_query') {
                detailsHtml = `Duration: <strong>${details.duration_seconds}s</strong><br>PID: ${details.pid}<br><code class="query-text">${escapeHtml(details.query)}</code>`;
            } else if (log.alert_type === 'blocking_lock') {
                detailsHtml = `Blocked PID: ${details.blocked_pid} is locked by PID ${details.blocking_pid}<br>Blocked Statement: <code class="query-text">${escapeHtml(details.blocked_statement)}</code>`;
            } else if (log.alert_type === 'connection_limit') {
                detailsHtml = `Usage: <strong>${details.usage_percent}%</strong> (${details.active_connections}/${details.max_connections} active)`;
            } else {
                detailsHtml = escapeHtml(JSON.stringify(details));
            }
        } else {
            detailsHtml = 'N/A';
        }
        
        const tr = document.createElement('tr');
        tr.innerHTML = `
            <td style="white-space:nowrap; font-family:var(--font-mono);">${formattedDate}</td>
            <td style="font-weight:600;">${log.database_name}</td>
            <td><span class="status-indicator">${log.alert_type.replace('_', ' ').toUpperCase()}</span></td>
            <td><span class="${sevClass}" style="display:inline-block; width:80px; text-align:center;">${log.severity.toUpperCase()}</span></td>
            <td style="max-width:350px;">
                <div style="font-size:0.85rem; margin-bottom:4px;">${escapeHtml(log.message)}</div>
                <div style="font-size:0.75rem; color:var(--text-muted);">${detailsHtml}</div>
            </td>
        `;
        tbody.appendChild(tr);
    });
}

async function clearLogs() {
    if (!confirm("Are you sure you want to clear all log history?")) {
        return;
    }
    
    try {
        const response = await fetch('/api/logs', { method: 'DELETE' });
        if (!response.ok) throw new Error("Clear action failed.");
        showToast("Log history cleared.", "success");
        fetchLogs();
    } catch (e) {
        showToast("Error clearing logs: " + e.message, "error");
    }
}

// --- Utilities ---

function escapeHtml(str) {
    if (!str) return '';
    return str
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}



function formatBytes(bytes) {
    if (bytes === 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
}

function switchModalTab(tabName) {
    // Remove active from all tab buttons
    document.querySelectorAll('.modal-tab-btn').forEach(btn => btn.classList.remove('active'));
    // Add active to current
    document.getElementById(`modal-tab-btn-${tabName}`).classList.add('active');
    
    // Hide all tab content
    document.querySelectorAll('.modal-tab-content').forEach(content => content.style.display = 'none');
    // Show current
    document.getElementById(`modal-tab-content-${tabName}`).style.display = 'block';
}
