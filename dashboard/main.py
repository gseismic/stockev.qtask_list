import os
import redis
import json
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="qtask_list Dashboard")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

# 获取所有队列
def get_all_queues():
    r = redis.from_url(REDIS_URL, decode_responses=True)
    queues = set()
    keys = r.keys("*")
    for key in keys:
        if not isinstance(key, str):
            continue
        if ":processing" in key or ":retry" in key or ":dlq" in key or ":delay" in key:
            continue
        if ":hist:" in key or ":task:" in key:
            continue
        if r.type(key) == "list":
            queues.add(key)
    return sorted(queues)

# 获取队列统计
def get_queue_stats(queue_name):
    r = redis.from_url(REDIS_URL, decode_responses=True)
    return {
        "queue": r.llen(queue_name),
        "processing": r.llen(f"{queue_name}:processing"),
        "retry": r.llen(f"{queue_name}:retry"),
        "dlq": r.llen(f"{queue_name}:dlq"),
        "delay": r.zcard(f"{queue_name}:delay"),
    }

# 获取历史
def get_history(queue_name, limit=20):
    r = redis.from_url(REDIS_URL, decode_responses=True)
    idx_key = f"qtask:hist:{queue_name}"
    task_ids = r.zrevrange(idx_key, 0, limit - 1)
    
    result = []
    for tid in task_ids:
        key = f"qtask:task:{tid}"
        raw = r.get(key)
        if raw:
            result.append(json.loads(raw))
    return result

@app.get("/api/queues")
def api_queues():
    queues = get_all_queues()
    result = []
    for q in queues:
        stats = get_queue_stats(q)
        result.append({
            "name": q,
            **stats
        })
    return result

@app.get("/api/queue/{name}")
def api_queue(name: str):
    stats = get_queue_stats(name)
    history = get_history(name)
    return {
        "name": name,
        "stats": stats,
        "history": history
    }

@app.get("/api/health")
def api_health():
    try:
        r = redis.from_url(REDIS_URL, decode_responses=True)
        r.ping()
        return {"status": "ok", "redis": REDIS_URL}
    except Exception as e:
        return {"status": "error", "error": str(e)}

@app.get("/", response_class=HTMLResponse)
def index():
    return """<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>qtask_list Dashboard</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;600;700&family=Outfit:wght@300;400;500;600;700&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg-primary: #0a0a0f;
            --bg-secondary: #12121a;
            --bg-tertiary: #1a1a24;
            --bg-card: #16161f;
            --text-primary: #f0f0f5;
            --text-secondary: #8888a0;
            --text-muted: #555566;
            --accent-cyan: #00d4ff;
            --accent-cyan-dim: #00d4ff33;
            --accent-green: #00ff88;
            --accent-green-dim: #00ff8833;
            --accent-red: #ff4466;
            --accent-red-dim: #ff446633;
            --accent-yellow: #ffaa00;
            --accent-yellow-dim: #ffaa0033;
            --accent-purple: #aa66ff;
            --accent-purple-dim: #aa66ff33;
            --border-color: #2a2a3a;
            --shadow-glow: 0 0 40px rgba(0, 212, 255, 0.1);
        }
        
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        
        body {
            font-family: 'Outfit', sans-serif;
            background: var(--bg-primary);
            color: var(--text-primary);
            min-height: 100vh;
            background-image: 
                radial-gradient(ellipse at 20% 0%, rgba(0, 212, 255, 0.08) 0%, transparent 50%),
                radial-gradient(ellipse at 80% 100%, rgba(170, 102, 255, 0.06) 0%, transparent 50%);
        }
        
        .container {
            max-width: 1400px;
            margin: 0 auto;
            padding: 2rem;
        }
        
        header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 2.5rem;
            padding-bottom: 1.5rem;
            border-bottom: 1px solid var(--border-color);
        }
        
        .logo {
            display: flex;
            align-items: center;
            gap: 1rem;
        }
        
        .logo-icon {
            width: 48px;
            height: 48px;
            background: linear-gradient(135deg, var(--accent-cyan), var(--accent-purple));
            border-radius: 12px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 1.5rem;
            font-weight: 700;
            font-family: 'JetBrains Mono', monospace;
            color: var(--bg-primary);
        }
        
        h1 {
            font-size: 1.75rem;
            font-weight: 600;
            letter-spacing: -0.02em;
        }
        
        .subtitle {
            color: var(--text-secondary);
            font-size: 0.875rem;
            margin-top: 0.25rem;
        }
        
        .status-badge {
            display: flex;
            align-items: center;
            gap: 0.5rem;
            padding: 0.5rem 1rem;
            background: var(--bg-tertiary);
            border-radius: 100px;
            font-size: 0.875rem;
            font-family: 'JetBrains Mono', monospace;
        }
        
        .status-dot {
            width: 8px;
            height: 8px;
            border-radius: 50%;
            background: var(--accent-green);
            animation: pulse 2s infinite;
        }
        
        .status-dot.error {
            background: var(--accent-red);
            animation: none;
        }
        
        @keyframes pulse {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.5; }
        }
        
        .stats-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
            gap: 1.5rem;
            margin-bottom: 2.5rem;
        }
        
        .queue-card {
            background: var(--bg-card);
            border: 1px solid var(--border-color);
            border-radius: 16px;
            padding: 1.5rem;
            transition: all 0.3s ease;
            cursor: pointer;
            position: relative;
            overflow: hidden;
        }
        
        .queue-card::before {
            content: '';
            position: absolute;
            top: 0;
            left: 0;
            right: 0;
            height: 3px;
            background: linear-gradient(90deg, var(--accent-cyan), var(--accent-purple));
            opacity: 0;
            transition: opacity 0.3s ease;
        }
        
        .queue-card:hover {
            border-color: var(--accent-cyan);
            transform: translateY(-2px);
            box-shadow: var(--shadow-glow);
        }
        
        .queue-card:hover::before {
            opacity: 1;
        }
        
        .queue-name {
            font-family: 'JetBrains Mono', monospace;
            font-size: 1rem;
            font-weight: 600;
            margin-bottom: 1rem;
            color: var(--accent-cyan);
        }
        
        .queue-stats {
            display: grid;
            grid-template-columns: repeat(5, 1fr);
            gap: 0.75rem;
        }
        
        .stat {
            text-align: center;
        }
        
        .stat-value {
            font-family: 'JetBrains Mono', monospace;
            font-size: 1.25rem;
            font-weight: 600;
        }
        
        .stat-label {
            font-size: 0.7rem;
            color: var(--text-secondary);
            text-transform: uppercase;
            letter-spacing: 0.05em;
            margin-top: 0.25rem;
        }
        
        .stat-ready .stat-value { color: var(--accent-green); }
        .stat-processing .stat-value { color: var(--accent-cyan); }
        .stat-retry .stat-value { color: var(--accent-yellow); }
        .stat-dlq .stat-value { color: var(--accent-red); }
        .stat-delay .stat-value { color: var(--accent-purple); }
        
        .empty-state {
            text-align: center;
            padding: 4rem 2rem;
            color: var(--text-secondary);
        }
        
        .empty-icon {
            font-size: 4rem;
            margin-bottom: 1rem;
            opacity: 0.3;
        }
        
        .history-section {
            background: var(--bg-card);
            border: 1px solid var(--border-color);
            border-radius: 16px;
            padding: 1.5rem;
            margin-top: 2rem;
        }
        
        .history-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 1.5rem;
        }
        
        .history-title {
            font-size: 1.125rem;
            font-weight: 600;
        }
        
        .history-table {
            width: 100%;
            border-collapse: collapse;
        }
        
        .history-table th,
        .history-table td {
            padding: 0.75rem 1rem;
            text-align: left;
            border-bottom: 1px solid var(--border-color);
        }
        
        .history-table th {
            font-size: 0.75rem;
            color: var(--text-secondary);
            text-transform: uppercase;
            letter-spacing: 0.05em;
            font-weight: 500;
        }
        
        .history-table td {
            font-family: 'JetBrains Mono', monospace;
            font-size: 0.875rem;
        }
        
        .history-table tbody tr:hover {
            background: var(--bg-tertiary);
        }
        
        .status-tag {
            display: inline-block;
            padding: 0.25rem 0.75rem;
            border-radius: 100px;
            font-size: 0.75rem;
            font-weight: 500;
        }
        
        .status-pending { background: var(--accent-yellow-dim); color: var(--accent-yellow); }
        .status-completed { background: var(--accent-green-dim); color: var(--accent-green); }
        .status-failed { background: var(--accent-red-dim); color: var(--accent-red); }
        .status-retry { background: var(--accent-cyan-dim); color: var(--accent-cyan); }
        
        .refresh-indicator {
            position: fixed;
            bottom: 2rem;
            right: 2rem;
            display: flex;
            align-items: center;
            gap: 0.5rem;
            padding: 0.75rem 1rem;
            background: var(--bg-card);
            border: 1px solid var(--border-color);
            border-radius: 100px;
            font-size: 0.875rem;
            color: var(--text-secondary);
        }
        
        .spinner {
            width: 16px;
            height: 16px;
            border: 2px solid var(--border-color);
            border-top-color: var(--accent-cyan);
            border-radius: 50%;
            animation: spin 1s linear infinite;
        }
        
        @keyframes spin {
            to { transform: rotate(360deg); }
        }
        
        .total-stats {
            display: flex;
            gap: 2rem;
            padding: 1rem 1.5rem;
            background: var(--bg-tertiary);
            border-radius: 12px;
            margin-bottom: 1.5rem;
        }
        
        .total-stat {
            display: flex;
            align-items: baseline;
            gap: 0.5rem;
        }
        
        .total-stat-value {
            font-family: 'JetBrains Mono', monospace;
            font-size: 1.5rem;
            font-weight: 700;
            color: var(--accent-cyan);
        }
        
        .total-stat-label {
            color: var(--text-secondary);
            font-size: 0.875rem;
        }
        
        @media (max-width: 768px) {
            .container { padding: 1rem; }
            .queue-stats { grid-template-columns: repeat(3, 1fr); }
            .header { flex-direction: column; gap: 1rem; align-items: flex-start; }
        }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <div class="logo">
                <div class="logo-icon">Q</div>
                <div>
                    <h1>qtask_list</h1>
                    <div class="subtitle">Distributed Task Queue Dashboard</div>
                </div>
            </div>
            <div class="status-badge">
                <div class="status-dot" id="healthDot"></div>
                <span id="healthText">Connecting...</span>
            </div>
        </header>
        
        <div class="total-stats" id="totalStats"></div>
        
        <div class="stats-grid" id="queueGrid">
            <div class="empty-state">
                <div class="empty-icon">📭</div>
                <p>No queues found</p>
            </div>
        </div>
    </div>
    
    <div class="refresh-indicator">
        <div class="spinner" id="refreshSpinner"></div>
        <span>Auto-refresh every 2s</span>
    </div>
    
    <script>
        let lastUpdate = null;
        
        async function fetchQueues() {
            try {
                const [healthRes, queuesRes] = await Promise.all([
                    fetch('/api/health'),
                    fetch('/api/queues')
                ]);
                
                const health = await healthRes.json();
                const queues = await queuesRes.json();
                
                // Update health
                const healthDot = document.getElementById('healthDot');
                const healthText = document.getElementById('healthText');
                if (health.status === 'ok') {
                    healthDot.classList.remove('error');
                    healthText.textContent = 'Connected';
                } else {
                    healthDot.classList.add('error');
                    healthText.textContent = 'Error';
                }
                
                // Update queues
                renderQueues(queues);
                lastUpdate = new Date();
            } catch (e) {
                console.error('Fetch error:', e);
            }
        }
        
        function renderQueues(queues) {
            const grid = document.getElementById('queueGrid');
            const totalEl = document.getElementById('totalStats');
            
            if (!queues || queues.length === 0) {
                grid.innerHTML = `
                    <div class="empty-state">
                        <div class="empty-icon">📭</div>
                        <p>No queues found</p>
                    </div>
                `;
                totalEl.innerHTML = '';
                return;
            }
            
            // Calculate totals
            let totals = { queue: 0, processing: 0, retry: 0, dlq: 0, delay: 0 };
            queues.forEach(q => {
                totals.queue += q.queue;
                totals.processing += q.processing;
                totals.retry += q.retry;
                totals.dlq += q.dlq;
                totals.delay += q.delay;
            });
            
            totalEl.innerHTML = `
                <div class="total-stat">
                    <span class="total-stat-value">${queues.length}</span>
                    <span class="total-stat-label">Queues</span>
                </div>
                <div class="total-stat">
                    <span class="total-stat-value">${totals.queue}</span>
                    <span class="total-stat-label">Ready</span>
                </div>
                <div class="total-stat">
                    <span class="total-stat-value">${totals.processing}</span>
                    <span class="total-stat-label">Processing</span>
                </div>
                <div class="total-stat">
                    <span class="total-stat-value">${totals.dlq}</span>
                    <span class="total-stat-label">DLQ</span>
                </div>
            `;
            
            grid.innerHTML = queues.map(q => `
                <div class="queue-card" onclick="showHistory('${q.name}')">
                    <div class="queue-name">${q.name}</div>
                    <div class="queue-stats">
                        <div class="stat stat-ready">
                            <div class="stat-value">${q.queue}</div>
                            <div class="stat-label">Ready</div>
                        </div>
                        <div class="stat stat-processing">
                            <div class="stat-value">${q.processing}</div>
                            <div class="stat-label">Processing</div>
                        </div>
                        <div class="stat stat-retry">
                            <div class="stat-value">${q.retry}</div>
                            <div class="stat-label">Retry</div>
                        </div>
                        <div class="stat stat-dlq">
                            <div class="stat-value">${q.dlq}</div>
                            <div class="stat-label">DLQ</div>
                        </div>
                        <div class="stat stat-delay">
                            <div class="stat-value">${q.delay}</div>
                            <div class="stat-label">Delay</div>
                        </div>
                    </div>
                </div>
            `).join('');
        }
        
        async function showHistory(queueName) {
            try {
                const res = await fetch(`/api/queue/${encodeURIComponent(queueName)}`);
                const data = await res.json();
                
                const grid = document.getElementById('queueGrid');
                const historyHtml = data.history.length > 0 ? `
                    <div class="history-section">
                        <div class="history-header">
                            <div class="history-title">${data.name} - History</div>
                        </div>
                        <table class="history-table">
                            <thead>
                                <tr>
                                    <th>Task ID</th>
                                    <th>Action</th>
                                    <th>Status</th>
                                    <th>Created</th>
                                </tr>
                            </thead>
                            <tbody>
                                ${data.history.map(h => `
                                    <tr>
                                        <td>${h.task_id ? h.task_id.substring(0, 8) + '...' : '-'}</td>
                                        <td>${h.action || '-'}</td>
                                        <td><span class="status-tag status-${h.status || 'pending'}">${h.status || 'pending'}</span></td>
                                        <td>${h.created_at ? new Date(h.created_at * 1000).toLocaleTimeString() : '-'}</td>
                                    </tr>
                                `).join('')}
                            </tbody>
                        </table>
                    </div>
                ` : '<div class="empty-state"><p>No history</p></div>';
                
                grid.innerHTML = `
                    <div class="queue-card" style="cursor: default; margin-bottom: 1.5rem;">
                        <div class="queue-name">${data.name}</div>
                        <div class="queue-stats">
                            <div class="stat stat-ready">
                                <div class="stat-value">${data.stats.queue}</div>
                                <div class="stat-label">Ready</div>
                            </div>
                            <div class="stat stat-processing">
                                <div class="stat-value">${data.stats.processing}</div>
                                <div class="stat-label">Processing</div>
                            </div>
                            <div class="stat stat-retry">
                                <div class="stat-value">${data.stats.retry}</div>
                                <div class="stat-label">Retry</div>
                            </div>
                            <div class="stat stat-dlq">
                                <div class="stat-value">${data.stats.dlq}</div>
                                <div class="stat-label">DLQ</div>
                            </div>
                            <div class="stat stat-delay">
                                <div class="stat-value">${data.stats.delay}</div>
                                <div class="stat-label">Delay</div>
                            </div>
                        </div>
                    </div>
                    ${historyHtml}
                    <button onclick="fetchQueues()" style="margin-top: 1rem; padding: 0.75rem 1.5rem; background: var(--bg-tertiary); border: 1px solid var(--border-color); border-radius: 8px; color: var(--text-primary); cursor: pointer;">← Back to Queues</button>
                `;
            } catch (e) {
                console.error('History error:', e);
            }
        }
        
        // Initial fetch
        fetchQueues();
        
        // Auto-refresh every 2 seconds
        setInterval(fetchQueues, 2000);
    </script>
</body>
</html>"""


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
