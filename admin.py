#!/usr/bin/env python3
"""
Travint.ai — Admin Backend

Simple Flask web interface for managing the system.

Usage:
    python admin.py
    
Then open http://localhost:5000 in your browser
"""

import os
import subprocess
import json
from datetime import datetime, timezone
from flask import Flask, render_template_string, request, jsonify, redirect, session
from dotenv import load_dotenv
from supabase import create_client
import secrets

load_dotenv()

app = Flask(__name__)
app.secret_key = secrets.token_hex(32)  # Generate secure secret key

# ADMIN PASSWORD - Change this!
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "travelguard2026")  # Default password
supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_SERVICE_KEY"))

# HTML Template
LOGIN_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Travint.ai Admin - Login</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            background: #0a0e14;
            color: #e6edf3;
            display: flex;
            align-items: center;
            justify-content: center;
            min-height: 100vh;
        }
        
        .login-container {
            background: #131920;
            border: 1px solid #232b36;
            border-radius: 12px;
            padding: 40px;
            width: 100%;
            max-width: 400px;
        }
        
        h1 {
            font-size: 24px;
            margin-bottom: 8px;
            font-weight: 700;
        }
        
        .subtitle {
            color: #8b949e;
            margin-bottom: 32px;
            font-size: 14px;
        }
        
        label {
            display: block;
            margin-bottom: 8px;
            font-size: 14px;
            font-weight: 500;
        }
        
        input[type="password"] {
            width: 100%;
            padding: 12px;
            background: #0d1117;
            border: 1px solid #232b36;
            border-radius: 8px;
            color: #e6edf3;
            font-size: 14px;
            margin-bottom: 20px;
        }
        
        input[type="password"]:focus {
            outline: none;
            border-color: #58a6ff;
        }
        
        .btn {
            width: 100%;
            padding: 12px;
            background: #58a6ff;
            color: #0a0e14;
            border: none;
            border-radius: 8px;
            font-weight: 600;
            font-size: 14px;
            cursor: pointer;
            transition: all 0.2s;
        }
        
        .btn:hover {
            background: #79c0ff;
        }
        
        .error {
            background: #f85149;
            color: white;
            padding: 12px;
            border-radius: 8px;
            margin-bottom: 20px;
            font-size: 14px;
        }
    </style>
</head>
<body>
    <div class="login-container">
        <h1>🛡️ Travint.ai Admin</h1>
        <p class="subtitle">Enter password to continue</p>
        
        {% if error %}
        <div class="error">{{ error }}</div>
        {% endif %}
        
        <form method="POST">
            <label for="password">Password</label>
            <input type="password" id="password" name="password" required autofocus>
            <button type="submit" class="btn">Login</button>
        </form>
    </div>
</body>
</html>
"""

# HTML Template
ADMIN_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Travint.ai Admin</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            background: #0a0e14;
            color: #e6edf3;
            padding: 40px;
        }
        
        .container { max-width: 1200px; margin: 0 auto; }
        
        h1 {
            font-size: 32px;
            margin-bottom: 10px;
            font-weight: 700;
        }
        
        .subtitle {
            color: #8b949e;
            margin-bottom: 40px;
            font-size: 14px;
        }
        
        .grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
            gap: 20px;
            margin-bottom: 40px;
        }
        
        .card {
            background: #131920;
            border: 1px solid #232b36;
            border-radius: 12px;
            padding: 24px;
        }
        
        .card h2 {
            font-size: 18px;
            margin-bottom: 16px;
            font-weight: 600;
        }
        
        .stat {
            display: flex;
            justify-content: space-between;
            padding: 12px 0;
            border-bottom: 1px solid #232b36;
        }
        
        .stat:last-child { border-bottom: none; }
        
        .stat-label {
            color: #8b949e;
            font-size: 14px;
        }
        
        .stat-value {
            font-weight: 600;
            font-size: 14px;
        }
        
        .btn {
            display: inline-block;
            padding: 12px 24px;
            background: #58a6ff;
            color: #0a0e14;
            border: none;
            border-radius: 8px;
            font-weight: 600;
            font-size: 14px;
            cursor: pointer;
            text-decoration: none;
            margin-right: 12px;
            margin-bottom: 12px;
            transition: all 0.2s;
        }
        
        .btn:hover {
            background: #79c0ff;
            transform: translateY(-1px);
        }
        
        .btn-secondary {
            background: #1a2129;
            color: #e6edf3;
            border: 1px solid #232b36;
        }
        
        .btn-secondary:hover {
            background: #232b36;
        }
        
        .btn-danger {
            background: #f85149;
            color: white;
        }
        
        .btn-danger:hover {
            background: #ff6b6b;
        }
        
        .btn:disabled {
            opacity: 0.5;
            cursor: not-allowed;
        }
        
        .status {
            display: inline-block;
            padding: 4px 12px;
            border-radius: 12px;
            font-size: 12px;
            font-weight: 600;
        }
        
        .status-running { background: #3fb950; color: #000; }
        .status-idle { background: #6e7681; color: #fff; }
        .status-error { background: #f85149; color: #fff; }
        
        .log {
            background: #0d1117;
            border: 1px solid #232b36;
            border-radius: 8px;
            padding: 16px;
            font-family: 'Courier New', monospace;
            font-size: 12px;
            max-height: 400px;
            overflow-y: auto;
            white-space: pre-wrap;
            margin-top: 16px;
        }
        
        .headlines {
            max-height: 300px;
            overflow-y: auto;
        }

        .headline-item {
            padding: 8px 0;
            border-bottom: 1px solid #232b36;
            font-size: 13px;
        }

        .baseline-item {
            padding: 16px;
            margin-bottom: 12px;
            background: #0d1117;
            border: 1px solid #232b36;
            border-radius: 8px;
        }

        .baseline-item:last-child { margin-bottom: 0; }

        .baseline-header {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 12px;
            margin-bottom: 10px;
            flex-wrap: wrap;
        }

        .baseline-meta {
            display: flex;
            align-items: center;
            gap: 12px;
            flex-wrap: wrap;
        }

        .baseline-country {
            font-weight: 700;
            font-size: 16px;
        }

        .baseline-layer {
            font-size: 11px;
            font-family: monospace;
            padding: 3px 8px;
            background: #1a2129;
            border-radius: 4px;
            color: #8b949e;
            text-transform: uppercase;
        }

        .baseline-total {
            font-family: monospace;
            font-weight: 700;
            font-size: 12px;
            padding: 4px 10px;
            border-radius: 4px;
        }

        .baseline-total.green  { background: #2d5a3d; color: #9fefb0; }
        .baseline-total.yellow { background: #b8a02e; color: #fff8d0; }
        .baseline-total.orange { background: #c45a1f; color: #ffe4c4; }
        .baseline-total.red    { background: #b83232; color: #ffcaca; }
        .baseline-total.purple { background: #6b3a8f; color: #e9d5ff; }

        .baseline-narrative {
            font-size: 12px;
            color: #8b949e;
            line-height: 1.6;
            margin-bottom: 12px;
            max-height: 80px;
            overflow: hidden;
            display: -webkit-box;
            -webkit-line-clamp: 3;
            -webkit-box-orient: vertical;
        }

        .baseline-actions {
            display: flex;
            gap: 8px;
        }

        .btn-approve {
            padding: 8px 20px;
            background: #238636;
            color: #fff;
            border: none;
            border-radius: 6px;
            font-size: 13px;
            font-weight: 600;
            cursor: pointer;
        }

        .btn-approve:hover { background: #2ea043; }

        .btn-reject {
            padding: 8px 20px;
            background: transparent;
            color: #f85149;
            border: 1px solid #f85149;
            border-radius: 6px;
            font-size: 13px;
            font-weight: 600;
            cursor: pointer;
        }

        .btn-reject:hover { background: #3d1a1a; }

        .pending-count {
            display: inline-block;
            background: #f97316;
            color: #fff;
            border-radius: 10px;
            font-size: 11px;
            font-weight: 700;
            padding: 2px 7px;
            margin-left: 6px;
        }

        .empty-state {
            color: #8b949e;
            font-size: 13px;
            padding: 20px 0;
            text-align: center;
        }
        
        .notifications {
            max-height: 400px;
            overflow-y: auto;
        }
        
        .notification-item {
            padding: 12px;
            margin-bottom: 8px;
            background: #0d1117;
            border: 1px solid #232b36;
            border-radius: 8px;
            font-size: 13px;
        }
        
        .notification-item.unread {
            border-left: 3px solid #58a6ff;
            background: #161b22;
        }
        
        .notification-item.severity-critical {
            border-left-color: #f85149;
        }
        
        .notification-item.severity-warning {
            border-left-color: #f97316;
        }
        
        .notif-header {
            display: flex;
            justify-content: space-between;
            margin-bottom: 6px;
        }
        
        .notif-country {
            font-weight: 600;
            color: #58a6ff;
        }
        
        .notif-time {
            font-size: 11px;
            color: #6e7681;
        }
        
        .notif-message {
            color: #e6edf3;
            line-height: 1.5;
            margin-bottom: 8px;
        }
        
        .btn-mark-read {
            padding: 4px 12px;
            background: transparent;
            border: 1px solid #232b36;
            border-radius: 6px;
            color: #8b949e;
            font-size: 11px;
            cursor: pointer;
        }
        
        .btn-mark-read:hover {
            border-color: #58a6ff;
            color: #58a6ff;
        }
        
        #notification-config {
            display: none;
        }
        
        #notification-config.active {
            display: block;
        }
        
        input[type="email"], textarea {
            width: 100%;
            padding: 10px;
            background: #0d1117;
            border: 1px solid #232b36;
            border-radius: 6px;
            color: #e6edf3;
            font-size: 14px;
            margin-bottom: 12px;
        }
        
        label {
            display: block;
            margin-bottom: 8px;
            font-size: 14px;
            font-weight: 500;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>🛡️ Travint.ai Admin</h1>
        <p class="subtitle">
            System Management & Controls
            <a href="/logout" style="float: right; color: #8b949e; text-decoration: none; font-size: 12px;">Logout →</a>
        </p>
        
        <div class="grid">
            <div class="card">
                <h2>System Status</h2>
                <div class="stat">
                    <span class="stat-label">Status</span>
                    <span class="status status-{{ status }}">{{ status_text }}</span>
                </div>
                <div class="stat">
                    <span class="stat-label">Last Analysis</span>
                    <span class="stat-value">{{ last_analysis }}</span>
                </div>
                <div class="stat">
                    <span class="stat-label">Last Ingestion</span>
                    <span class="stat-value">{{ last_ingestion }}</span>
                </div>
                <div class="stat">
                    <span class="stat-label">Countries Analyzed</span>
                    <span class="stat-value">{{ country_count }}</span>
                </div>
                <div class="stat">
                    <span class="stat-label">Total Assessments</span>
                    <span class="stat-value">{{ total_scores }}</span>
                </div>
                <div class="stat">
                    <span class="stat-label">Unread Notifications</span>
                    <span class="stat-value" style="color: #f85149;">{{ unread_count }}</span>
                </div>
            </div>
            
            <div class="card">
                <h2>🔔 Recent Notifications</h2>
                <div class="notifications">
                    {% if notifications %}
                        {% for notif in notifications[:8] %}
                        <div class="notification-item {{ 'unread' if not notif.read else '' }} severity-{{ notif.severity }}">
                            <div class="notif-header">
                                <span class="notif-country">{{ notif.country_name }}</span>
                                <span class="notif-time">{{ notif.time_ago }}</span>
                            </div>
                            <div class="notif-message">{{ notif.message }}</div>
                            {% if not notif.read %}
                            <button class="btn-mark-read" onclick="markRead('{{ notif.id }}')">Mark Read</button>
                            {% endif %}
                        </div>
                        {% endfor %}
                        <button class="btn btn-secondary" onclick="markAllRead()" style="margin-top: 12px; width: 100%;">
                            Mark All Read
                        </button>
                    {% else %}
                        <p style="color: #8b949e; font-size: 13px; padding: 20px 0;">No notifications yet. Run an analysis to generate notifications.</p>
                    {% endif %}
                </div>
            </div>
        </div>
        
        <!-- ── Pending Baselines Review Queue ─────────────────────────────── -->
        <div class="card" style="margin-bottom: 20px;">
            <h2>
                Pending Baseline Review
                {% if pending_baselines %}
                <span class="pending-count">{{ pending_baselines|length }}</span>
                {% endif %}
            </h2>
            {% if pending_baselines %}
                {% for b in pending_baselines %}
                <div class="baseline-item" id="baseline-{{ b.id }}">
                    <div class="baseline-header">
                        <div class="baseline-meta">
                            <span class="baseline-country">{{ b.country_name }}</span>
                            <span class="baseline-layer">{{ b.identity_layer }}</span>
                            <span class="baseline-total {{ b.total_score.lower() }}">{{ b.total_score }}</span>
                            <span style="font-size: 11px; color: #6e7681;">v{{ b.version_number }} · {{ b.time_ago }}</span>
                        </div>
                        <div class="baseline-actions">
                            <button class="btn-approve" onclick="reviewBaseline('{{ b.id }}', 'approve')">✓ Approve</button>
                            <button class="btn-reject" onclick="reviewBaseline('{{ b.id }}', 'reject')">✗ Reject</button>
                        </div>
                    </div>
                    {% if b.narrative %}
                    <div class="baseline-narrative">{{ b.narrative }}</div>
                    {% endif %}
                    <div style="display: flex; gap: 8px; flex-wrap: wrap;">
                        {% for cat, score in b.scores.items() %}
                        <span style="font-family: monospace; font-size: 11px; padding: 2px 6px; background: #1a2129; border-radius: 3px; color: #8b949e;">
                            {{ cat.replace('_', ' ') }}: <strong style="color: #e6edf3;">{{ score }}</strong>
                        </span>
                        {% endfor %}
                    </div>
                </div>
                {% endfor %}
            {% else %}
                <div class="empty-state">No pending baselines. All assessments are up to date.</div>
            {% endif %}
        </div>

        <div class="grid">
            <div class="card">
                <h2>Recent Headlines</h2>
                <div class="headlines">
                    {% for headline in headlines[:10] %}
                    <div class="headline-item">{{ headline }}</div>
                    {% endfor %}
                </div>
            </div>
        </div>
        
        <div class="card">
            <h2>Actions</h2>
            <button class="btn" onclick="runTask('ingest')">📰 Ingest News</button>
            <button class="btn" onclick="runTask('analyze')">🔄 Smart Update (New Headlines Only)</button>
            <button class="btn btn-danger" onclick="runTask('force-analyze')">🔁 Force Re-analyze All</button>
            <button class="btn btn-secondary" onclick="toggleNotifications()">🔔 Notification Settings</button>
            
            <div id="notification-config" style="margin-top: 24px;">
                <h3 style="margin-bottom: 16px;">Notification Configuration</h3>
                <p style="color: #8b949e; margin-bottom: 16px; font-size: 13px;">
                    Coming soon: Configure email alerts for threat level changes
                </p>
                <label>Email Recipients (comma-separated)</label>
                <input type="email" placeholder="your@email.com, team@email.com" />
                
                <label>Alert Threshold</label>
                <select style="width: 100%; padding: 10px; background: #0d1117; border: 1px solid #232b36; border-radius: 6px; color: #e6edf3;">
                    <option>Any change</option>
                    <option>1+ level change</option>
                    <option selected>2+ level change</option>
                </select>
                
                <button class="btn" style="margin-top: 16px;" disabled>Save Settings (Coming Soon)</button>
            </div>
            
            <div id="task-log" class="log" style="display: none;">
                <strong>Task Output:</strong><br><br>
                <div id="log-content"></div>
            </div>
        </div>
    </div>
    
    <script>
        function toggleNotifications() {
            document.getElementById('notification-config').classList.toggle('active');
        }
        
        async function markRead(notifId) {
            try {
                await fetch(`/notification/read/${notifId}`, { method: 'POST' });
                location.reload();
            } catch (error) {
                console.error('Error:', error);
            }
        }
        
        async function markAllRead() {
            try {
                await fetch('/notification/read-all', { method: 'POST' });
                location.reload();
            } catch (error) {
                console.error('Error:', error);
            }
        }
        
        async function reviewBaseline(id, action) {
            const el = document.getElementById(`baseline-${id}`);
            if (!el) return;
            const confirmMsg = action === 'approve'
                ? 'Approve this baseline? It will be marked as owner-reviewed.'
                : 'Reject this baseline? It will be marked as rejected.';
            if (!confirm(confirmMsg)) return;
            el.style.opacity = '0.5';
            el.style.pointerEvents = 'none';
            try {
                const resp = await fetch(`/baseline/${action}/${id}`, { method: 'POST' });
                const data = await resp.json();
                if (data.status === 'ok') {
                    el.style.transition = 'opacity 0.4s';
                    el.style.opacity = '0';
                    setTimeout(() => { el.remove(); }, 400);
                } else {
                    alert(`Error: ${data.message}`);
                    el.style.opacity = '1';
                    el.style.pointerEvents = '';
                }
            } catch (e) {
                alert(`Error: ${e}`);
                el.style.opacity = '1';
                el.style.pointerEvents = '';
            }
        }

        async function runTask(task) {
            const logDiv = document.getElementById('task-log');
            const logContent = document.getElementById('log-content');
            
            logDiv.style.display = 'block';
            logContent.textContent = `Starting ${task}...\\n`;
            
            try {
                const response = await fetch(`/run/${task}`, { method: 'POST' });
                const data = await response.json();
                
                if (data.status === 'running') {
                    logContent.textContent += `Task started. Check terminal for output.\\n`;
                    logContent.textContent += `This may take several minutes...\\n`;
                    
                    // Poll for completion (simplified - in production use websockets)
                    setTimeout(() => {
                        location.reload();
                    }, 5000);
                } else {
                    logContent.textContent += `Error: ${data.message}\\n`;
                }
            } catch (error) {
                logContent.textContent += `Error: ${error}\\n`;
            }
        }
    </script>
</body>
</html>
"""

@app.route('/login', methods=['GET', 'POST'])
def login():
    """Login page"""
    if request.method == 'POST':
        password = request.form.get('password')
        if password == ADMIN_PASSWORD:
            session['authenticated'] = True
            return redirect('/')
        else:
            return render_template_string(LOGIN_TEMPLATE, error='Invalid password')
    
    return render_template_string(LOGIN_TEMPLATE)

@app.route('/logout')
def logout():
    """Logout"""
    session.pop('authenticated', None)
    return redirect('/login')

def require_auth():
    """Check if user is authenticated"""
    if not session.get('authenticated'):
        return redirect('/login')
    return None

@app.route('/')
def index():
    """Main admin dashboard"""
    
    # Check authentication
    auth_check = require_auth()
    if auth_check:
        return auth_check
    
    # Get system stats
    try:
        scores_result = supabase.table("scores").select("id, scored_at").execute()
        country_result = supabase.table("countries").select("id").execute()
        
        total_scores = len(scores_result.data)
        country_count = len(country_result.data)
        
        # Get last analysis time
        if scores_result.data:
            last_scored = max(s['scored_at'] for s in scores_result.data)
            last_analysis = datetime.fromisoformat(last_scored.replace('Z', '+00:00')).strftime('%Y-%m-%d %H:%M UTC')
        else:
            last_analysis = 'Never'
            
    except Exception as e:
        total_scores = 0
        country_count = 0
        last_analysis = f'Error: {e}'
    
    # Get headlines
    headlines = []
    last_ingestion = 'Never'
    try:
        with open('latest_headlines.json', 'r') as f:
            data = json.load(f)
            headlines = data.get('headlines', [])
            timestamp = data.get('timestamp')
            if timestamp:
                last_ingestion = datetime.fromisoformat(timestamp.replace('Z', '+00:00')).strftime('%Y-%m-%d %H:%M UTC')
    except FileNotFoundError:
        pass
    
    # Get pending baselines for review
    pending_baselines = []
    try:
        bv_result = (supabase.table("baseline_versions")
                     .select("*, countries(name)")
                     .eq("reviewed_by", "pending")
                     .order("created_at", desc=False)
                     .execute())
        for b in bv_result.data:
            scores = b.get("scores", {})
            if isinstance(scores, str):
                import json as _json
                try:
                    scores = _json.loads(scores)
                except Exception:
                    scores = {}
            pending_baselines.append({
                "id":            b["id"],
                "country_name":  b["countries"]["name"] if b.get("countries") else "Unknown",
                "identity_layer": b.get("identity_layer", "base"),
                "version_number": b.get("version_number", 1),
                "total_score":   b.get("total_score", "?"),
                "scores":        scores,
                "narrative":     (b.get("baseline_narrative") or "")[:300],
                "time_ago":      get_time_ago(b["created_at"]),
            })
    except Exception as e:
        print(f"Error fetching pending baselines: {e}")

    # Get notifications
    notifications = []
    unread_count = 0
    try:
        notif_result = supabase.table("notifications").select("*, countries(name)").order("created_at", desc=True).limit(20).execute()
        
        for n in notif_result.data:
            time_ago = get_time_ago(n['created_at'])
            notifications.append({
                'id': n['id'],
                'country_name': n['countries']['name'] if n.get('countries') else 'Unknown',
                'message': n['message'],
                'severity': n['severity'],
                'read': n['read'],
                'time_ago': time_ago
            })
            if not n['read']:
                unread_count += 1
                
    except Exception as e:
        print(f"Error fetching notifications: {e}")
    
    return render_template_string(
        ADMIN_TEMPLATE,
        status='idle',
        status_text='IDLE',
        last_analysis=last_analysis,
        last_ingestion=last_ingestion,
        country_count=country_count,
        total_scores=total_scores,
        headlines=headlines,
        notifications=notifications,
        unread_count=unread_count,
        pending_baselines=pending_baselines
    )

def get_time_ago(timestamp_str):
    """Convert timestamp to human-readable 'time ago' format"""
    try:
        dt = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
        now = datetime.now(timezone.utc)
        diff = now - dt
        
        seconds = diff.total_seconds()
        if seconds < 60:
            return 'Just now'
        elif seconds < 3600:
            mins = int(seconds / 60)
            return f'{mins}m ago'
        elif seconds < 86400:
            hours = int(seconds / 3600)
            return f'{hours}h ago'
        else:
            days = int(seconds / 86400)
            return f'{days}d ago'
    except:
        return 'Unknown'

@app.route('/run/<task>', methods=['POST'])
def run_task(task):
    """Execute analysis tasks"""
    
    # Check authentication
    if not session.get('authenticated'):
        return jsonify({'status': 'error', 'message': 'Unauthorized'}), 401
    
    try:
        if task == 'ingest':
            subprocess.Popen(['python', 'ingest.py'])
            return jsonify({'status': 'running', 'message': 'Ingestion started'})
            
        elif task == 'analyze':
            subprocess.Popen(['python', 'analyze.py'])
            return jsonify({'status': 'running', 'message': 'Smart analysis started'})
            
        elif task == 'force-analyze':
            # Delete cache file to force re-analysis
            if os.path.exists('latest_headlines.json'):
                os.remove('latest_headlines.json')
            subprocess.Popen(['python', 'ingest.py'])
            subprocess.Popen(['python', 'analyze.py'])
            return jsonify({'status': 'running', 'message': 'Force re-analysis started'})
            
        else:
            return jsonify({'status': 'error', 'message': 'Unknown task'}), 400
            
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/baseline/approve/<baseline_id>', methods=['POST'])
def approve_baseline(baseline_id):
    """Mark a baseline as owner-approved"""
    if not session.get('authenticated'):
        return jsonify({'status': 'error', 'message': 'Unauthorized'}), 401
    try:
        supabase.table("baseline_versions").update({
            'reviewed_by': 'owner_approved',
            'reviewed_at': datetime.now(timezone.utc).isoformat()
        }).eq('id', baseline_id).execute()
        return jsonify({'status': 'ok'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/baseline/reject/<baseline_id>', methods=['POST'])
def reject_baseline(baseline_id):
    """Mark a baseline as rejected"""
    if not session.get('authenticated'):
        return jsonify({'status': 'error', 'message': 'Unauthorized'}), 401
    try:
        supabase.table("baseline_versions").update({
            'reviewed_by': 'rejected',
            'reviewed_at': datetime.now(timezone.utc).isoformat()
        }).eq('id', baseline_id).execute()
        return jsonify({'status': 'ok'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/notification/read/<notif_id>', methods=['POST'])
def mark_notification_read(notif_id):
    """Mark a notification as read"""
    try:
        supabase.table("notifications").update({'read': True}).eq('id', notif_id).execute()
        return jsonify({'status': 'ok'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/notification/read-all', methods=['POST'])
def mark_all_read():
    """Mark all notifications as read"""
    try:
        supabase.table("notifications").update({'read': True}).eq('read', False).execute()
        return jsonify({'status': 'ok'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

if __name__ == '__main__':
    print("="*50)
    print("  Travint.ai Admin Backend")
    print("="*50)
    print("\nStarting server on http://localhost:5000")
    print("Press Ctrl+C to stop\n")
    
    app.run(debug=True, port=5000)
