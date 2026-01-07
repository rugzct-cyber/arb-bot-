/**
 * HFT Arb Bot Dashboard - Real-time WebSocket Client
 * Professional trading interface with live orderbook updates
 */

// State
let ws = null;
let bots = [];
let selectedBotId = null;
let reconnectAttempts = 0;
const MAX_RECONNECT_ATTEMPTS = 10;

// DOM Elements
const wsStatus = document.getElementById('ws-status');
const botCount = document.getElementById('bot-count');
const avgLatency = document.getElementById('avg-latency');
const botsContainer = document.getElementById('bots-container');
const botsList = document.getElementById('bots-list');
const noBots = document.getElementById('no-bots');
const botDetails = document.getElementById('bot-details');

// ============================================
// WebSocket Connection
// ============================================

function connectWebSocket() {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/ws`;

    ws = new WebSocket(wsUrl);

    ws.onopen = () => {
        console.log('✅ WebSocket connected');
        updateConnectionStatus(true);
        reconnectAttempts = 0;
    };

    ws.onmessage = (event) => {
        try {
            const message = JSON.parse(event.data);
            handleMessage(message);
        } catch (err) {
            console.error('Failed to parse message:', err);
        }
    };

    ws.onclose = () => {
        console.log('❌ WebSocket disconnected');
        updateConnectionStatus(false);
        scheduleReconnect();
    };

    ws.onerror = (err) => {
        console.error('WebSocket error:', err);
    };
}

function scheduleReconnect() {
    if (reconnectAttempts >= MAX_RECONNECT_ATTEMPTS) {
        console.log('Max reconnect attempts reached');
        return;
    }

    reconnectAttempts++;
    const delay = Math.min(1000 * Math.pow(2, reconnectAttempts - 1), 30000);
    console.log(`Reconnecting in ${delay}ms (attempt ${reconnectAttempts})`);

    setTimeout(connectWebSocket, delay);
}

function updateConnectionStatus(connected) {
    const dot = wsStatus.querySelector('.status-dot');
    const text = wsStatus.querySelector('.status-text');

    if (connected) {
        dot.className = 'status-dot connected';
        text.textContent = 'Live';
    } else {
        dot.className = 'status-dot disconnected';
        text.textContent = 'Disconnected';
    }
}

// ============================================
// Message Handling
// ============================================

function handleMessage(message) {
    switch (message.type) {
        case 'init':
        case 'status':
            handleStatusUpdate(message.data);
            break;
        case 'bots_list':
            bots = message.data.bots || [];
            renderBots();
            break;
        case 'bot_update':
            handleBotUpdate(message.data);
            break;
    }
}

function handleStatusUpdate(data) {
    bots = data.bots || [];
    renderBots();
    updateLatencies(data.latencies || {});
    updateMetrics();
}

function handleBotUpdate(botData) {
    // Update specific bot in state
    const index = bots.findIndex(b => b.id === botData.id);
    if (index >= 0) {
        bots[index] = botData;
    } else {
        bots.push(botData);
    }

    renderBots();

    // Update details if this bot is selected
    if (selectedBotId === botData.id) {
        renderBotDetails(botData);
    }
}

// ============================================
// UI Rendering
// ============================================

function renderBots() {
    const runningCount = bots.filter(b => b.running).length;
    botCount.textContent = bots.length;

    if (bots.length === 0) {
        noBots.style.display = 'flex';
        botsList.innerHTML = '';
        return;
    }

    noBots.style.display = 'none';

    // Update existing cards or create new ones (avoid full re-render)
    const existingIds = new Set();

    bots.forEach(bot => {
        existingIds.add(bot.id);
        let card = botsList.querySelector(`.bot-card[data-id="${bot.id}"]`);

        if (!card) {
            // Create new card only if it doesn't exist
            const temp = document.createElement('div');
            temp.innerHTML = renderBotCard(bot);
            card = temp.firstElementChild;
            botsList.appendChild(card);

            // Add click handler
            card.addEventListener('click', (e) => {
                if (!e.target.closest('button')) {
                    selectBot(card.dataset.id);
                }
            });
        } else {
            // Update existing card values without recreating DOM
            updateBotCard(card, bot);
        }
    });

    // Remove cards for bots that no longer exist
    botsList.querySelectorAll('.bot-card').forEach(card => {
        if (!existingIds.has(card.dataset.id)) {
            card.remove();
        }
    });
}

function updateBotCard(card, bot) {
    // Update only the values that change, not the structure
    const isSelected = bot.id === selectedBotId;
    card.classList.toggle('selected', isSelected);

    // Update status
    const statusEl = card.querySelector('.bot-status:not(.ws)');
    if (statusEl) {
        statusEl.className = `bot-status ${bot.running ? 'running' : 'stopped'}`;
        statusEl.textContent = bot.running ? 'Running' : 'Stopped';
    }

    // Update spread
    const spread = bot.spread?.current || 0;
    const spreadEl = card.querySelector('.spread-value');
    if (spreadEl) {
        spreadEl.className = `spread-value ${spread > 0 ? 'positive' : 'negative'}`;
        spreadEl.textContent = `${spread.toFixed(3)}%`;
    }

    // Update direction
    const dirEl = card.querySelector('.spread-direction');
    if (dirEl) {
        const direction = bot.opportunity
            ? `${bot.opportunity.buy_exchange} → ${bot.opportunity.sell_exchange}`
            : `${bot.exchange_a} ↔ ${bot.exchange_b}`;
        dirEl.textContent = direction;
    }

    // Update metrics
    const metrics = card.querySelectorAll('.metric-val');
    if (metrics.length >= 4) {
        metrics[0].textContent = bot.stats.polls + bot.stats.ws_updates;
        metrics[1].textContent = bot.stats.profitable || 0;
        metrics[2].textContent = bot.latency?.avg_ms?.toFixed(0) || '--';
        metrics[3].textContent = formatRuntime(bot.stats.runtime);
    }

    // Update buttons
    const footer = card.querySelector('.bot-card-footer');
    if (footer) {
        const hasStopBtn = footer.querySelector('.btn-danger');
        const hasStartBtn = footer.querySelector('.btn-success');

        if (bot.running && !hasStopBtn) {
            footer.innerHTML = `
                <button class="btn btn-danger btn-sm" onclick="stopBot('${bot.id}')">⏹ Stop</button>
                <button class="btn btn-sm" style="background: var(--bg-tertiary);" onclick="removeBot('${bot.id}')">🗑️</button>
            `;
        } else if (!bot.running && !hasStartBtn) {
            footer.innerHTML = `
                <button class="btn btn-success btn-sm" onclick="startBot('${bot.id}')">▶ Start</button>
                <button class="btn btn-sm" style="background: var(--bg-tertiary);" onclick="removeBot('${bot.id}')">🗑️</button>
            `;
        }
    }
}

function renderBotCard(bot) {
    const isSelected = bot.id === selectedBotId;
    const statusClass = bot.running ? 'running' : 'stopped';
    const wsLabel = bot.ws_mode ? '<span class="bot-status ws">WS</span>' : '';

    const spread = bot.spread?.current || 0;
    const spreadClass = spread > 0 ? 'positive' : 'negative';
    const direction = bot.opportunity
        ? `${bot.opportunity.buy_exchange} → ${bot.opportunity.sell_exchange}`
        : `${bot.exchange_a} ↔ ${bot.exchange_b}`;

    return `
        <div class="bot-card ${isSelected ? 'selected' : ''}" data-id="${bot.id}">
            <div class="bot-card-header">
                <div>
                    <div class="bot-symbol">${bot.symbol}</div>
                    <div class="bot-exchanges">${bot.exchange_a} ↔ ${bot.exchange_b}</div>
                </div>
                <div style="display: flex; gap: 4px;">
                    ${wsLabel}
                    <span class="bot-status ${statusClass}">${bot.running ? 'Running' : 'Stopped'}</span>
                </div>
            </div>
            <div class="bot-card-body">
                <div class="bot-spread-display">
                    <span class="spread-value ${spreadClass}">${spread.toFixed(3)}%</span>
                    <span class="spread-direction">${direction}</span>
                </div>
                <div class="bot-metrics">
                    <div class="metric" title="Total orderbook polling updates">
                        <div class="metric-icon">📊</div>
                        <div class="metric-val">${bot.stats.polls + bot.stats.ws_updates}</div>
                        <div class="metric-lbl">Updates</div>
                    </div>
                    <div class="metric" title="Profitable spread opportunities found">
                        <div class="metric-icon">🎯</div>
                        <div class="metric-val">${bot.stats.profitable || 0}</div>
                        <div class="metric-lbl">Opps</div>
                    </div>
                    <div class="metric" title="Average response latency">
                        <div class="metric-icon">⚡</div>
                        <div class="metric-val">${bot.latency?.avg_ms?.toFixed(0) || '--'}</div>
                        <div class="metric-lbl">ms</div>
                    </div>
                    <div class="metric" title="Bot running time">
                        <div class="metric-icon">⏱️</div>
                        <div class="metric-val">${formatRuntime(bot.stats.runtime)}</div>
                        <div class="metric-lbl">Time</div>
                    </div>
                </div>
            </div>
            <div class="bot-card-footer">
                ${bot.running
            ? `<button class="btn btn-danger btn-sm" onclick="stopBot('${bot.id}')">⏹ Stop</button>`
            : `<button class="btn btn-success btn-sm" onclick="startBot('${bot.id}')">▶ Start</button>`
        }
                <button class="btn btn-sm btn-delete" onclick="removeBot('${bot.id}')">🗑️ Delete</button>
            </div>
        </div>
    `;
}

function selectBot(botId) {
    selectedBotId = botId;

    // Update selection UI
    document.querySelectorAll('.bot-card').forEach(card => {
        card.classList.toggle('selected', card.dataset.id === botId);
    });

    // Render details
    const bot = bots.find(b => b.id === botId);
    if (bot) {
        renderBotDetails(bot);
    }
}

function renderBotDetails(bot) {
    if (!bot) {
        botDetails.innerHTML = '<div class="empty-state"><p>Select a bot to view details</p></div>';
        return;
    }

    // Check if structure already exists for this bot
    const existingBotId = botDetails.dataset.botId;
    if (existingBotId === bot.id) {
        // Just update values, don't recreate structure
        updateBotDetails(bot);
        return;
    }

    // Create structure for first time
    botDetails.dataset.botId = bot.id;
    const opp = bot.opportunity;

    botDetails.innerHTML = `
        <div class="detail-section">
            <h4>📈 Current Opportunity</h4>
            <div id="detail-opportunity">
                ${renderOpportunityContent(opp)}
            </div>
        </div>
        
        <div class="detail-section">
            <h4>📚 Orderbook Depth</h4>
            <div class="orderbook-display" id="detail-orderbooks">
                ${renderOrderbookSide(bot.orderbooks?.a, 'Bids', 'bid')}
                ${renderOrderbookSide(bot.orderbooks?.b, 'Asks', 'ask')}
            </div>
        </div>
        
        <div class="detail-section">
            <h4>📊 Statistics</h4>
            <div style="display: grid; grid-template-columns: repeat(2, 1fr); gap: 8px;">
                <div style="padding: 8px; background: var(--bg-tertiary); border-radius: 4px;">
                    <div style="font-size: 0.65rem; color: var(--text-muted);">Best Spread Seen</div>
                    <div style="font-family: var(--font-mono);" id="stat-best-spread">${bot.spread?.best?.toFixed(3) || 0}%</div>
                </div>
                <div style="padding: 8px; background: var(--bg-tertiary); border-radius: 4px;">
                    <div style="font-size: 0.65rem; color: var(--text-muted);">Avg Spread</div>
                    <div style="font-family: var(--font-mono);" id="stat-avg-spread">${bot.spread?.avg?.toFixed(3) || 0}%</div>
                </div>
                <div style="padding: 8px; background: var(--bg-tertiary); border-radius: 4px;">
                    <div style="font-size: 0.65rem; color: var(--text-muted);">Min Latency</div>
                    <div style="font-family: var(--font-mono);" id="stat-min-lat">${formatLatency(bot.latency?.min_ms)}</div>
                </div>
                <div style="padding: 8px; background: var(--bg-tertiary); border-radius: 4px;">
                    <div style="font-size: 0.65rem; color: var(--text-muted);">Max Latency</div>
                    <div style="font-family: var(--font-mono);" id="stat-max-lat">${formatLatency(bot.latency?.max_ms)}</div>
                </div>
            </div>
        </div>
        
        <div class="detail-section">
            <h4>📝 Logs</h4>
            <div class="logs-container" id="detail-logs">
                ${(bot.logs || []).map(log => `<div class="log-entry">${escapeHtml(log)}</div>`).join('')}
            </div>
        </div>
    `;
}

function updateBotDetails(bot) {
    // Update opportunity section
    const oppContainer = document.getElementById('detail-opportunity');
    if (oppContainer) {
        oppContainer.innerHTML = renderOpportunityContent(bot.opportunity);
    }

    // Update orderbooks (less frequent updates are OK here)
    const obContainer = document.getElementById('detail-orderbooks');
    if (obContainer) {
        obContainer.innerHTML = `
            ${renderOrderbookSide(bot.orderbooks?.a, 'Bids', 'bid')}
            ${renderOrderbookSide(bot.orderbooks?.b, 'Asks', 'ask')}
        `;
    }

    // Update stats (targeted updates - no flicker)
    const bestSpread = document.getElementById('stat-best-spread');
    if (bestSpread) bestSpread.textContent = `${bot.spread?.best?.toFixed(3) || 0}%`;

    const avgSpread = document.getElementById('stat-avg-spread');
    if (avgSpread) avgSpread.textContent = `${bot.spread?.avg?.toFixed(3) || 0}%`;

    const minLat = document.getElementById('stat-min-lat');
    if (minLat) minLat.textContent = formatLatency(bot.latency?.min_ms);

    const maxLat = document.getElementById('stat-max-lat');
    if (maxLat) maxLat.textContent = formatLatency(bot.latency?.max_ms);

    // Update logs (append new ones only)
    const logsContainer = document.getElementById('detail-logs');
    if (logsContainer && bot.logs) {
        const currentCount = logsContainer.children.length;
        const newLogs = bot.logs.slice(currentCount);
        newLogs.forEach(log => {
            const div = document.createElement('div');
            div.className = 'log-entry';
            div.textContent = log;
            logsContainer.appendChild(div);
        });
        // Keep scrolled to bottom
        logsContainer.scrollTop = logsContainer.scrollHeight;
    }
}

function renderOpportunityContent(opp) {
    if (!opp) {
        return '<p style="color: var(--text-muted);">No current opportunity</p>';
    }

    return `
        <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 8px; font-family: var(--font-mono);">
            <div style="padding: 8px; background: var(--bg-tertiary); border-radius: 4px;">
                <div style="font-size: 0.7rem; color: var(--text-muted);">Buy @ ${opp.buy_exchange}</div>
                <div style="font-size: 1rem; color: var(--color-bid);">$${opp.buy_price?.toFixed(2)}</div>
            </div>
            <div style="padding: 8px; background: var(--bg-tertiary); border-radius: 4px;">
                <div style="font-size: 0.7rem; color: var(--text-muted);">Sell @ ${opp.sell_exchange}</div>
                <div style="font-size: 1rem; color: var(--color-ask);">$${opp.sell_price?.toFixed(2)}</div>
            </div>
        </div>
        <div style="margin-top: 12px; display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; text-align: center;">
            <div>
                <div style="font-size: 0.65rem; color: var(--text-muted);">Net Spread</div>
                <div style="font-family: var(--font-mono); color: ${opp.net_spread_after_slippage > 0 ? 'var(--color-success)' : 'var(--color-danger)'};">
                    ${opp.net_spread_after_slippage?.toFixed(3)}%
                </div>
            </div>
            <div>
                <div style="font-size: 0.65rem; color: var(--text-muted);">Confidence</div>
                <div style="font-family: var(--font-mono);">${(opp.confidence * 100).toFixed(0)}%</div>
            </div>
            <div>
                <div style="font-size: 0.65rem; color: var(--text-muted);">Est. Profit</div>
                <div style="font-family: var(--font-mono); color: var(--color-success);">$${opp.expected_profit_usd?.toFixed(2)}</div>
            </div>
        </div>
    `;
}

function renderOrderbookSide(ob, title, type) {
    if (!ob) {
        return `<div class="orderbook-side"><h5>${title}</h5><p style="color: var(--text-muted); font-size: 0.75rem;">No data</p></div>`;
    }

    const levels = type === 'bid' ? ob.bids : ob.asks;
    const priceClass = type;

    return `
        <div class="orderbook-side">
            <h5>${ob.exchange} - ${title}</h5>
            <div class="orderbook-header">
                <span>Price</span>
                <span>Size</span>
            </div>
            <div class="orderbook-levels">
                ${(levels || []).slice(0, 5).map(level => `
                    <div class="ob-level ${type}">
                        <span class="ob-price ${priceClass}">${formatPrice(level.price)}</span>
                        <span class="ob-size">${formatSize(level.size)}</span>
                    </div>
                `).join('')}
            </div>
            <div style="margin-top: 8px; font-size: 0.7rem; color: var(--text-muted);">
                Imbalance: ${(ob.imbalance * 100).toFixed(1)}% | Depth: ${ob.bid_depth?.toFixed(2) || 0}
            </div>
        </div>
    `;
}

function updateLatencies(latencies) {
    Object.entries(latencies).forEach(([exchange, stats]) => {
        const el = document.getElementById(`latency-${exchange}`);
        if (el && stats.avg_ms) {
            el.textContent = `${stats.avg_ms.toFixed(0)}ms`;
            el.className = 'latency-value';
            if (stats.avg_ms > 200) el.classList.add('very-slow');
            else if (stats.avg_ms > 100) el.classList.add('slow');
        }
    });
}

function updateMetrics() {
    // Calculate average latency across all bots
    const latencies = bots
        .filter(b => b.latency?.avg_ms > 0)
        .map(b => b.latency.avg_ms);

    if (latencies.length > 0) {
        const avg = latencies.reduce((a, b) => a + b, 0) / latencies.length;
        avgLatency.textContent = `${avg.toFixed(0)}ms`;
    }
}

// ============================================
// Bot Actions
// ============================================

async function addBot(e) {
    e.preventDefault();

    const data = {
        symbol: document.getElementById('symbol').value,
        exchange_a: document.getElementById('exchange-a').value,
        exchange_b: document.getElementById('exchange-b').value,
        min_spread: parseFloat(document.getElementById('min-spread').value),
        max_size: parseFloat(document.getElementById('max-size').value),
        use_websocket: document.getElementById('use-websocket').checked,
        dry_run: document.getElementById('dry-run').checked,
        poll_interval: 50,
    };

    try {
        const resp = await fetch('/api/bots', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data),
        });
        const result = await resp.json();

        if (!result.success) {
            showNotification('Error: ' + result.error, 'error');
        } else {
            showNotification(`Bot ${result.bot_id} created!`, 'success');
        }
    } catch (err) {
        showNotification('Error: ' + err.message, 'error');
    }
}

async function stopBot(botId) {
    try {
        await fetch(`/api/bots/${botId}/stop`, { method: 'POST' });
    } catch (err) {
        console.error('Failed to stop bot:', err);
    }
}

async function startBot(botId) {
    try {
        await fetch(`/api/bots/${botId}/start`, { method: 'POST' });
    } catch (err) {
        console.error('Failed to start bot:', err);
    }
}

async function removeBot(botId) {
    if (!confirm('Remove this bot?')) return;

    try {
        await fetch(`/api/bots/${botId}`, { method: 'DELETE' });
        if (selectedBotId === botId) {
            selectedBotId = null;
            botDetails.innerHTML = '<div class="empty-state"><p>Select a bot to view details</p></div>';
        }
    } catch (err) {
        console.error('Failed to remove bot:', err);
    }
}

// ============================================
// Utilities
// ============================================

function formatRuntime(seconds) {
    if (!seconds || seconds < 0) return '0s';
    // If value is unreasonably large (> 1 week), likely a timestamp bug
    if (seconds > 604800) return '--';
    if (seconds < 60) return `${Math.floor(seconds)}s`;
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
    if (seconds < 86400) return `${Math.floor(seconds / 3600)}h`;
    const days = Math.floor(seconds / 86400);
    const hours = Math.floor((seconds % 86400) / 3600);
    return `${days}d ${hours}h`;
}

function formatLatency(ms) {
    // Handle infinity, null, undefined, and unreasonably large values
    if (!ms || !isFinite(ms) || ms > 60000) return '--';
    return `${ms.toFixed(0)}ms`;
}

function formatPrice(price) {
    if (!price) return '--';
    if (price >= 1000) return price.toFixed(2);
    if (price >= 1) return price.toFixed(4);
    return price.toFixed(6);
}

function formatSize(size) {
    if (!size) return '--';
    if (size >= 1000) return `${(size / 1000).toFixed(1)}K`;
    return size.toFixed(4);
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function showNotification(message, type = 'info') {
    // Simple alert for now, could be upgraded to toast
    alert(message);
}

// ============================================
// Initialization
// ============================================

document.addEventListener('DOMContentLoaded', () => {
    // Connect WebSocket
    connectWebSocket();

    // Form handler
    document.getElementById('add-bot-form').addEventListener('submit', addBot);

    // Close details button
    document.getElementById('close-details')?.addEventListener('click', () => {
        selectedBotId = null;
        document.querySelectorAll('.bot-card').forEach(c => c.classList.remove('selected'));
        botDetails.innerHTML = '<div class="empty-state"><p>Select a bot to view details</p></div>';
    });

    // Filter pills
    document.querySelectorAll('.filter-pills .pill').forEach(pill => {
        pill.addEventListener('click', () => {
            document.querySelectorAll('.filter-pills .pill').forEach(p => p.classList.remove('active'));
            pill.classList.add('active');

            const filter = pill.dataset.filter;
            document.querySelectorAll('.bot-card').forEach(card => {
                const bot = bots.find(b => b.id === card.dataset.id);
                if (!bot) return;

                if (filter === 'all') {
                    card.style.display = '';
                } else if (filter === 'running') {
                    card.style.display = bot.running ? '' : 'none';
                } else if (filter === 'stopped') {
                    card.style.display = bot.running ? 'none' : '';
                }
            });
        });
    });

    // Ping WebSocket every 30s to keep alive
    setInterval(() => {
        if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ type: 'ping' }));
        }
    }, 30000);
});
