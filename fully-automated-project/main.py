# ==============================================================================
# Exora Quant AI - v3.8 MODIFIED with Supply/Demand Logic & High-Res Backtest
# ==============================================================================
# This version INTEGRATES the advanced Supply and Demand trading logic.
# The backtester has been UPGRADED to a high-resolution engine for accuracy.
# It analyzes the last 500 candles to identify zones and trades on them.
# The backtester implements TP1, TP2, and TP3 for partial profit-taking.
# The live trading bot uses the same S/D logic with a single TP target.
# All other original v3.8 functions, including the UI and Bybit API, remain intact.
# ==============================================================================

import time
import requests
import math
import statistics
import threading
import json
import logging
import hmac
import hashlib
import os
from urllib.parse import urlencode
from flask import Flask, jsonify, render_template_string, request
from datetime import datetime
# --- FIX: Import modules for robust requests ---
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


# --- Configuration ---
ALLOWED_INTERVALS = ["15", "30", "60", "120", "240", "360", "720", "D", "W", "M"]
BYBIT_API_URL = "https://api.bybit.com/v5/market"
BINGX_API_URL = "https://open-api.bingx.com"
SETTINGS_FILE = "settings.json"
TRADELIST_FILE = "tradelist.json"
TRADE_COOLDOWN_SECONDS = 300 # 5 minutes
MAINTENANCE_MARGIN_RATE = 0.005 # Standard rate for major pairs like BTC/ETH

# --- FIX: Create a robust requests session with retries ---
session = requests.Session()
retry_strategy = Retry(
    total=5,  # Total number of retries
    backoff_factor=1,  # Wait 1s, 2s, 4s, 8s, 16s between retries
    status_forcelist=[429, 500, 502, 503, 504],  # Retry on these HTTP status codes
    allowed_methods=["HEAD", "GET", "OPTIONS"] # Only retry on safe methods
)
adapter = HTTPAdapter(max_retries=retry_strategy)
session.mount("https://", adapter)
session.mount("http://", adapter)

# --- Helper functions for JSON persistence ---
def load_from_json(filename, default_data):
    if os.path.exists(filename):
        with open(filename, 'r') as f:
            try: return json.load(f)
            except json.JSONDecodeError: return default_data
    return default_data

def save_to_json(filename, data):
    with open(filename, 'w') as f:
        json.dump(data, f, indent=4)

# --- Global State Management ---
SETTINGS = load_from_json(SETTINGS_FILE, {
    "bingx_api_key": "YOUR_BINGX_API_KEY",
    "bingx_secret_key": "YOUR_BINGX_SECRET_KEY",
    "mode": "demo",
    "risk_percentage": 1.0, # MODIFIED: from risk_usdt
    "leverage": 10,
    "trigger_percentage": 4.0 # Note: This is no longer used by the new S/D logic but kept for UI compatibility
})
TRADE_LIST = load_from_json(TRADELIST_FILE, [])
BOT_STATUS = {}
ACTIVE_POSITIONS = {}

# --- Thread-safe Locks ---
settings_lock = threading.Lock()
trade_list_lock = threading.Lock()
status_lock = threading.Lock()
positions_lock = threading.Lock()

# --- Flask App Initialization ---
app = Flask(__name__)
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

# --- HTML & JavaScript Template (from v3.8, with JSON 'Infinity' fix) ---
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Exora Quant AI v3.8 (S/D Logic)</title>
    <style>
        html, body { width: 100%; height: 100%; margin: 0; padding: 0; overflow: hidden; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background-color: #000; color: #eee; font-size: 14px; }
        #chartdiv { width: 100%; height: calc(100% - 250px); }
        .controls-wrapper { position: absolute; top: 15px; left: 15px; z-index: 100; display: flex; align-items: flex-start; gap: 10px; }
        #toggle-controls-btn { width: 40px; height: 40px; padding: 0; font-size: 20px; border-radius: 8px; border: 1px solid #444; background-color: rgba(25, 25, 25, 0.85); color: #eee; cursor: pointer; backdrop-filter: blur(5px); display: flex; align-items: center; justify-content: center; }
        .controls-overlay { background-color: rgba(25, 25, 25, 0.85); backdrop-filter: blur(5px); padding: 12px; border-radius: 8px; border: 1px solid #333; display: flex; flex-wrap: wrap; align-items: center; gap: 12px; box-shadow: 0 4px 15px rgba(0,0,0,0.5); transition: transform 0.3s ease-in-out, opacity 0.3s ease-in-out; }
        .controls-overlay.hidden { transform: translateX(calc(-100% - 20px)); opacity: 0; pointer-events: none; }
        .controls-overlay label { color: #ccc; }
        .controls-overlay select, .controls-overlay input, .controls-overlay button { padding: 8px 12px; border-radius: 5px; border: 1px solid #444; background-color: #2a2a2a; color: #eee; cursor: pointer; }
        .controls-overlay input[type='text'] { width: 100px; } .controls-overlay input[type='number'] { width: 60px; }
        .controls-overlay button { background-color: #007bff; border-color: #007bff; font-weight: bold; }
        .controls-overlay button.add-btn { background-color: #28a745; border-color: #28a745; }
        #status { margin-left: 15px; color: #ffeb3b; min-width: 250px; }
        .panels-container { position: absolute; bottom: 0; left: 0; right: 0; height: 250px; background: #111; border-top: 1px solid #333; display: flex; transition: height 0.3s ease-in-out; }
        .panel { padding: 15px; overflow-y: auto; box-sizing: border-box; }
        .panel h3 { margin-top: 0; border-bottom: 1px solid #444; padding-bottom: 8px; color: #00aaff; }
        #settings-panel { width: 300px; border-right: 1px solid #333; }
        #tradelist-panel { flex-grow: 1; border-right: 1px solid #333; }
        .setting-item { display: grid; grid-template-columns: 90px 1fr; gap: 10px; align-items: center; margin-bottom: 10px; }
        .setting-item input, .setting-item select { width: 100%; box-sizing: border-box; }
        #save-settings-btn { width: 100%; background-color: #ffc107; color: #000; margin-top: 10px; }
        #trade-list-table { width: 100%; border-collapse: collapse; }
        #trade-list-table th, #trade-list-table td { padding: 8px; text-align: left; border-bottom: 1px solid #222; font-size: 13px; }
        #trade-list-table th { color: #aaa; }
        .manual-trade-btn { padding: 4px 8px; font-size: 12px; margin-right: 4px; border-radius: 4px; }
        .long-btn { background-color: #28a745; border-color: #28a745; } .short-btn { background-color: #dc3545; border-color: #dc3545; } .close-btn { background-color: #ffc107; border-color: #ffc107; color: #000; } .remove-btn { background-color: #6c757d; border-color: #6c757d; padding: 4px 8px; font-size: 12px; }
        #backtest-panel { width: 450px; display: flex; flex-direction: column; } #backtest-controls { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; margin-bottom: 10px; } #backtest-controls input[type="date"] { width: 130px; } #run-backtest-btn { background-color: #17a2b8; border-color: #17a2b8; } #backtest-results { flex-grow: 1; overflow-y: auto; display: none; } #equitychartdiv { width: 100%; height: 100px; margin-bottom: 10px; transition: height 0.3s ease-in-out; } #backtest-stats { display: grid; grid-template-columns: repeat(3, 1fr); gap: 5px 15px; margin-bottom: 10px; font-size: 12px; } #backtest-stats div > span { font-weight: bold; color: #00aaff; } #backtest-trades-table { width: 100%; font-size: 11px; } #toggle-backtest-size-btn { float: right; background: #333; border: 1px solid #555; color: #ccc; cursor: pointer; padding: 1px 7px; font-size: 16px; border-radius: 4px; line-height: 1; }
        .panels-container.is-maximized { height: calc(100% - 40px); } .panels-container.is-maximized #chartdiv { height: 40px; } .panels-container.is-maximized #settings-panel, .panels-container.is-maximized #tradelist-panel { display: none; } .panels-container.is-maximized #backtest-panel { width: 100%; } .panels-container.is-maximized #backtest-results { height: calc(100% - 60px); } .panels-container.is-maximized #equitychartdiv { height: 300px; } .panels-container.is-maximized #backtest-trades-table-container { flex-grow: 1; }
    </style>
    <script src="https://cdn.amcharts.com/lib/5/index.js"></script><script src="https://cdn.amcharts.com/lib/5/xy.js"></script><script src="https://cdn.amcharts.com/lib/5/themes/Animated.js"></script><script src="https://cdn.amcharts.com/lib/5/themes/Dark.js"></script>
</head>
<body>
    <div id="chartdiv"></div><div class="controls-wrapper"><button id="toggle-controls-btn" title="Toggle Controls">☰</button><div class="controls-overlay"><label for="symbol">Symbol:</label><input type="text" id="symbol" value="BTCUSDT"><label for="interval">Timeframe:</label><select id="interval"><option value="60">1 hour</option><option value="240">4 hours</option><option value="D">Daily</option></select><label for="num_predictions">Predictions:</label><input type="number" id="num_predictions" value="20" min="1" max="50"><button id="fetchButton">Fetch</button><button id="add-to-list-btn" class="add-btn">Add to Trade List</button><div id="status"></div></div></div>
    <div class="panels-container"><div id="settings-panel" class="panel"><h3>Settings</h3><div id="balance-display" style="padding: 5px 0 10px; font-size: 1.1em; color: #ffeb3b; border-bottom: 1px solid #444; margin-bottom: 10px;">Balance: Loading...</div><div class="setting-item"><label for="api-key">API Key:</label><input type="text" id="api-key"></div><div class="setting-item"><label for="secret-key">Secret Key:</label><input type="password" id="secret-key"></div><div class="setting-item"><label for="mode">Mode:</label><select id="mode"><option value="demo">Demo</option><option value="live">Live</option></select></div><div class="setting-item"><label for="risk-percentage">Risk (%):</label><input type="number" id="risk-percentage" value="1.0" step="0.1" min="0.1"></div><div class="setting-item"><label for="leverage">Leverage:</label><input type="number" id="leverage" value="10"></div><div class="setting-item"><label for="trigger-percentage">Trigger %:</label><input type="number" id="trigger-percentage" value="4.0" step="0.1" min="0"></div><button id="save-settings-btn">Save Settings</button></div><div id="tradelist-panel" class="panel"><h3>Live Trade List</h3><table id="trade-list-table"><thead><tr><th>Symbol</th><th>Timeframe</th><th>Status</th><th>PnL</th><th>Manual Control</th></tr></thead><tbody></tbody></table></div><div id="backtest-panel" class="panel"><h3>Backtest <button id="toggle-backtest-size-btn" title="Maximize">□</button></h3><div id="backtest-controls"><input type="text" id="backtest-symbol" value="BTCUSDT"><select id="backtest-interval"><option value="60">1 hour</option><option value="240">4 hours</option><option value="D">Daily</option></select><input type="date" id="backtest-start"><input type="date" id="backtest-end"><button id="run-backtest-btn">Run</button><div id="backtest-status" style="color: #ffc107;"></div></div><div id="backtest-results"><div id="equitychartdiv"></div><div id="backtest-stats"></div><div id="backtest-trades-table-container" style="height: 80px; overflow-y: auto;"><table id="backtest-trades-table" class="trade-list-table"><thead><tr><th>Exit Time</th><th>Side</th><th>PnL</th><th>Return %</th><th>Reason</th></tr></thead><tbody></tbody></table></div></div></div></div>
<script>
document.addEventListener('DOMContentLoaded', function () {
    let root, chart, equityRoot;
    async function manualTrade(side, symbol, id) { if (!confirm(`Are you sure you want to place a manual ${side.toUpperCase()} order for ${symbol}?`)) return; try { const response = await fetch('/api/manual_trade', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ side, symbol, id }) }); const result = await response.json(); alert(result.message || result.error); } catch (error) { alert(`Error placing manual trade: ${error}`); } }
    async function manualClose(symbol, id) { if (!confirm(`Are you sure you want to close the position for ${symbol}?`)) return; try { const response = await fetch('/api/manual_close', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ symbol, id }) }); const result = await response.json(); alert(result.message || result.error); } catch (error) { alert(`Error closing position: ${error}`); } }
    async function refreshTradeList() { const response = await fetch('/api/trade_list'); const { trade_list, bot_status } = await response.json(); const tableBody = document.querySelector('#trade-list-table tbody'); tableBody.innerHTML = ''; trade_list.forEach(item => { const status = bot_status[item.id] || { message: "Initializing...", color: "#fff" }; let pnlCell = '<td>-</td>'; if (status.pnl !== undefined) { const pnl = status.pnl; const pnl_pct = status.pnl_pct; const pnlColor = pnl > 0 ? '#28a745' : (pnl < 0 ? '#dc3545' : '#fff'); pnlCell = `<td style="color: ${pnlColor}; font-weight: bold;">${pnl.toFixed(2)} <span style="font-size:0.8em; opacity: 0.8;">(${pnl_pct.toFixed(2)}%)</span></td>`; } const row = `<tr><td>${item.symbol}</td><td>${item.interval_text}</td><td style="color:${status.color}">${status.message}</td>${pnlCell}<td><button class="manual-trade-btn long-btn" data-id="${item.id}" data-symbol="${item.symbol}">Long</button><button class="manual-trade-btn short-btn" data-id="${item.id}" data-symbol="${item.symbol}">Short</button><button class="manual-trade-btn close-btn" data-id="${item.id}" data-symbol="${item.symbol}">Close</button><button class="remove-btn" data-id="${item.id}">X</button></td></tr>`; tableBody.insertAdjacentHTML('beforeend', row); }); document.querySelectorAll('.remove-btn').forEach(btn => { btn.addEventListener('click', () => removeTradeItem(btn.dataset.id)); }); document.querySelectorAll('.long-btn').forEach(btn => { btn.addEventListener('click', () => manualTrade('long', btn.dataset.symbol, btn.dataset.id)); }); document.querySelectorAll('.short-btn').forEach(btn => { btn.addEventListener('click', () => manualTrade('short', btn.dataset.symbol, btn.dataset.id)); }); document.querySelectorAll('.close-btn').forEach(btn => { btn.addEventListener('click', () => manualClose(btn.dataset.symbol, btn.dataset.id)); }); };
    let xAxis, yAxis; function createMainChart() { if (root) root.dispose(); root = am5.Root.new("chartdiv"); root.setThemes([am5themes_Animated.new(root), am5themes_Dark.new(root)]); chart = root.container.children.push(am5xy.XYChart.new(root, { panX: true, wheelX: "panX", pinchZoomX: true })); chart.set("cursor", am5xy.XYCursor.new(root, { behavior: "panX" })).lineY.set("visible", false); xAxis = chart.xAxes.push(am5xy.DateAxis.new(root, { baseInterval: { timeUnit: "minute", count: 60 }, renderer: am5xy.AxisRendererX.new(root, { minGridDistance: 70 }) })); yAxis = chart.yAxes.push(am5xy.ValueAxis.new(root, { renderer: am5xy.AxisRendererY.new(root, {}) })); let series = chart.series.push(am5xy.CandlestickSeries.new(root, { name: "Historical", xAxis: xAxis, yAxis: yAxis, valueXField: "t", openValueYField: "o", highValueYField: "h", lowValueYField: "l", valueYField: "c" })); let predictedSeries = chart.series.push(am5xy.CandlestickSeries.new(root, { name: "Predicted", xAxis: xAxis, yAxis: yAxis, valueXField: "t", openValueYField: "o", highValueYField: "h", lowValueYField: "l", valueYField: "c" })); predictedSeries.columns.template.setAll({ fill: am5.color(0xaaaaaa), stroke: am5.color(0xaaaaaa) }); chart.set("scrollbarX", am5.Scrollbar.new(root, { orientation: "horizontal" })); };
    async function fetchChartData() { createMainChart(); const symbol = document.getElementById('symbol').value.toUpperCase().trim(); const interval = document.getElementById('interval').value; const numPredictions = document.getElementById('num_predictions').value; if (!symbol) { document.getElementById('status').innerText = 'Error: Symbol cannot be empty.'; return; } document.getElementById('status').innerText = 'Fetching chart data...'; try { const response = await fetch(`/api/candles?symbol=${symbol}&interval=${interval}&predictions=${numPredictions}`); if (!response.ok) throw new Error((await response.json()).error); const data = await response.json(); const intervalConfig = !isNaN(interval) ? { timeUnit: "minute", count: parseInt(interval) } : { timeUnit: { 'D': 'day', 'W': 'week', 'M': 'month' }[interval] || 'day', count: 1 }; xAxis.set("baseInterval", intervalConfig); chart.series.getIndex(0).data.setAll(data.candles); chart.series.getIndex(1).data.setAll(data.predicted); document.getElementById('status').innerText = 'Chart updated.'; } catch (error) { document.getElementById('status').innerText = `Error: ${error.message}`; } finally { setTimeout(() => { document.getElementById('status').innerText = ''; }, 3000); }};
    async function saveSettings() { const settings = { bingx_api_key: document.getElementById('api-key').value, bingx_secret_key: document.getElementById('secret-key').value, mode: document.getElementById('mode').value, risk_percentage: parseFloat(document.getElementById('risk-percentage').value), leverage: parseInt(document.getElementById('leverage').value), trigger_percentage: parseFloat(document.getElementById('trigger-percentage').value) }; await fetch('/api/settings', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(settings) }); alert('Settings saved!'); };
    async function loadSettings() { const response = await fetch('/api/settings'); const settings = await response.json(); document.getElementById('api-key').value = settings.bingx_api_key; document.getElementById('secret-key').value = settings.bingx_secret_key; document.getElementById('mode').value = settings.mode; document.getElementById('risk-percentage').value = settings.risk_percentage; document.getElementById('leverage').value = settings.leverage; document.getElementById('trigger-percentage').value = settings.trigger_percentage; };
    async function addTradeItem() { const item = { symbol: document.getElementById('symbol').value.toUpperCase().trim(), interval: document.getElementById('interval').value, interval_text: document.getElementById('interval').options[document.getElementById('interval').selectedIndex].text, predictions: parseInt(document.getElementById('num_predictions').value) }; await fetch('/api/trade_list/add', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(item) }); refreshTradeList(); };
    async function removeTradeItem(id) { await fetch('/api/trade_list/remove', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ id: id }) }); refreshTradeList(); };
    async function refreshBalance() { try { const response = await fetch('/api/balance'); if (!response.ok) { document.getElementById('balance-display').textContent = 'Balance: Error'; return; } const data = await response.json(); if (data.total_balance !== undefined) { document.getElementById('balance-display').textContent = `Balance: ${data.total_balance.toFixed(2)} USDT`; } else { document.getElementById('balance-display').textContent = `Balance: ${data.error || 'N/A'}`; } } catch (error) { document.getElementById('balance-display').textContent = 'Balance: Network Error'; } }
    let backtestRunning = false;
    async function runBacktest() { if (backtestRunning) return; backtestRunning = true; const statusEl = document.getElementById('backtest-status'); const resultsEl = document.getElementById('backtest-results'); statusEl.textContent = 'Fetching historical data...'; resultsEl.style.display = 'none'; const payload = { symbol: document.getElementById('backtest-symbol').value.toUpperCase(), interval: document.getElementById('backtest-interval').value, start_date: document.getElementById('backtest-start').value, end_date: document.getElementById('backtest-end').value, }; try { statusEl.textContent = 'Running simulation...'; const response = await fetch('/api/backtest', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload) }); if (!response.ok) throw new Error((await response.json()).error); const results = await response.json(); displayBacktestResults(results); statusEl.textContent = 'Backtest complete.'; } catch (error) { statusEl.textContent = `Error: ${error.message}`; } finally { backtestRunning = false; } }
    function displayBacktestResults(results) { 
        document.getElementById('backtest-results').style.display = 'block'; 
        const stats = results.metrics; 
        const statsEl = document.getElementById('backtest-stats'); 
        const profitFactorDisplay = typeof stats.profit_factor === 'number' ? stats.profit_factor.toFixed(2) : stats.profit_factor;
        statsEl.innerHTML = `<div>Net Profit: <span style="color:${stats.net_profit > 0 ? '#28a745' : '#dc3545'}">${stats.net_profit.toFixed(2)} USDT</span></div><div>Win Rate: <span>${stats.win_rate.toFixed(2)}%</span></div><div>Profit Factor: <span>${profitFactorDisplay}</span></div><div>Total Trades: <span>${stats.total_trades}</span></div><div>Avg Trade PnL: <span>${stats.avg_trade_pnl.toFixed(2)}</span></div><div>Max Drawdown: <span style="color:#dc3545">${stats.max_drawdown.toFixed(2)}%</span></div>`; 
        const tradesTableBody = document.querySelector('#backtest-trades-table tbody'); 
        tradesTableBody.innerHTML = ''; 
        results.trades.forEach(trade => { 
            const pnlColor = trade.pnl > 0 ? '#28a745' : '#dc3545'; 
            const row = `<tr><td>${new Date(trade.exit_time).toLocaleString()}</td><td>${trade.direction}</td><td style="color:${pnlColor}">${trade.pnl.toFixed(2)}</td><td style="color:${pnlColor}">${trade.return_pct.toFixed(2)}%</td><td>${trade.exit_reason || 'N/A'}</td></tr>`; 
            tradesTableBody.insertAdjacentHTML('afterbegin', row); 
        }); 
        createEquityChart(results.equity_curve); 
    }
    function createEquityChart(data) { if (equityRoot) equityRoot.dispose(); equityRoot = am5.Root.new("equitychartdiv"); equityRoot.setThemes([am5themes_Dark.new(equityRoot)]); let chart = equityRoot.container.children.push(am5xy.XYChart.new(equityRoot, { panX: true, wheelX: "zoomX", pinchZoomX: true, paddingLeft: 0, paddingRight: 0 })); let xAxis = chart.xAxes.push(am5xy.DateAxis.new(equityRoot, { baseInterval: { timeUnit: "day", count: 1 }, renderer: am5xy.AxisRendererX.new(equityRoot, { minGridDistance: 50 }), })); let yAxis = chart.yAxes.push(am5xy.ValueAxis.new(equityRoot, { renderer: am5xy.AxisRendererY.new(equityRoot, {}) })); let series = chart.series.push(am5xy.LineSeries.new(equityRoot, { name: "Equity", xAxis: xAxis, yAxis: yAxis, valueYField: "equity", valueXField: "time", stroke: am5.color(0x00aaff), fill: am5.color(0x00aaff), })); series.fills.template.setAll({ fillOpacity: 0.1, visible: true }); series.data.setAll(data); }
    function initialize() { loadSettings(); refreshTradeList(); refreshBalance(); setInterval(refreshTradeList, 1000); setInterval(refreshBalance, 10000); const today = new Date(); const yesterday = new Date(today); yesterday.setDate(yesterday.getDate() - 1); const threeMonthsAgo = new Date(today); threeMonthsAgo.setMonth(threeMonthsAgo.getMonth() - 3); document.getElementById('backtest-end').valueAsDate = yesterday; document.getElementById('backtest-start').valueAsDate = threeMonthsAgo; document.getElementById('toggle-controls-btn').addEventListener('click', () => document.querySelector('.controls-overlay').classList.toggle('hidden')); document.getElementById('fetchButton').addEventListener('click', fetchChartData); document.getElementById('add-to-list-btn').addEventListener('click', addTradeItem); document.getElementById('save-settings-btn').addEventListener('click', saveSettings); document.getElementById('run-backtest-btn').addEventListener('click', runBacktest); document.getElementById('toggle-backtest-size-btn').addEventListener('click', (e) => { const btn = e.target; const container = document.querySelector('.panels-container'); const chartContainer = document.getElementById('chartdiv'); container.classList.toggle('is-maximized'); if (container.classList.contains('is-maximized')) { btn.textContent = '−'; btn.title = "Minimize"; chartContainer.style.height = '40px'; } else { btn.textContent = '□'; btn.title = "Maximize"; chartContainer.style.height = 'calc(100% - 250px)'; } setTimeout(() => { if (equityRoot) { equityRoot.resize(); } if (root) { root.resize(); } }, 350); }); }
    initialize();
});
</script>
</body>
</html>
"""

# --- NEW: Liquidation Calculation Helper ---
def calculate_liquidation_price(entry_price, leverage, direction, maintenance_margin_rate=MAINTENANCE_MARGIN_RATE):
    """Calculates the approximate liquidation price for a given entry."""
    if leverage <= 1: return None
    
    initial_margin_rate = 1 / leverage
    
    if direction == 'long':
        price_change_percentage = initial_margin_rate - maintenance_margin_rate
        return entry_price * (1 - price_change_percentage)
    elif direction == 'short':
        price_change_percentage = initial_margin_rate - maintenance_margin_rate
        return entry_price * (1 + price_change_percentage)
    return None

# --- Data Fetching (from v3.8) ---
def get_bybit_data(symbol, interval, start_ts=None, end_ts=None, limit=1000):
    params = {"category": "linear", "symbol": symbol, "interval": interval, "limit": limit}
    if start_ts: params['start'] = int(start_ts)
    if end_ts: params['end'] = int(end_ts)
    try:
        response = session.get(f"{BYBIT_API_URL}/kline", params=params, timeout=(5, 10))
        response.raise_for_status()
        data = response.json()
        if data.get("retCode") != 0: raise ValueError(data.get("retMsg"))
        # Bybit returns newest first, so we reverse it to have oldest first
        return list(reversed(data["result"]["list"]))
    except (requests.exceptions.RequestException, ValueError) as e:
        app.logger.warning(f"Bybit kline API error for {symbol}: {e}")
        raise ConnectionError(f"Failed to fetch Bybit kline data for {symbol} after retries.")

def get_bybit_ticker_data(symbols):
    if not isinstance(symbols, list): symbols = [symbols]
    if not symbols: return {}
    params = {"category": "linear", "symbol": ",".join(symbols)}
    try:
        response = session.get(f"{BYBIT_API_URL}/tickers", params=params, timeout=(5, 10))
        response.raise_for_status()
        data = response.json()
        if data.get("retCode") == 0:
            return {item['symbol']: float(item['lastPrice']) for item in data['result']['list']}
        return {}
    except requests.exceptions.RequestException as e:
        app.logger.error(f"Bybit ticker API error after retries: {e}")
        return {}

# --- NEW: Supply and Demand Zone Identification Logic ---
def find_supply_demand_zones(candles, lookback=500):
    if len(candles) < 20:
        return {'supply': None, 'demand': None}

    data = [{'t': int(c[0]), 'o': float(c[1]), 'h': float(c[2]), 'l': float(c[3]), 'c': float(c[4])} for c in candles[-lookback:]]
    
    body_sizes = [abs(c['o'] - c['c']) for c in data if abs(c['o'] - c['c']) > 0]
    if not body_sizes: return {'supply': None, 'demand': None}
    avg_body_size = statistics.mean(body_sizes)
    
    supply_zone, demand_zone = None, None
    current_price = data[-1]['c']

    for i in range(len(data) - 2, 2, -1):
        if not supply_zone:
            is_drop_candle = (data[i]['o'] > data[i]['c']) and (data[i]['o'] - data[i]['c']) > avg_body_size * 1.5
            if is_drop_candle:
                base_high, base_low, base_candles_count = -1, float('inf'), 0
                for j in range(i - 1, max(0, i - 4), -1):
                    if abs(data[j]['o'] - data[j]['c']) < avg_body_size * 0.8 and data[j-1]['c'] > data[j-1]['o']:
                        base_high = max(base_high, data[j]['h']); base_low = min(base_low, data[j]['l']); base_candles_count += 1
                    else: break
                if base_candles_count > 0 and base_low > current_price: supply_zone = {'high': base_high, 'low': base_low}

        if not demand_zone:
            is_rally_candle = (data[i]['c'] > data[i]['o']) and (data[i]['c'] - data[i]['o']) > avg_body_size * 1.5
            if is_rally_candle:
                base_high, base_low, base_candles_count = -1, float('inf'), 0
                for j in range(i - 1, max(0, i - 4), -1):
                    if abs(data[j]['o'] - data[j]['c']) < avg_body_size * 0.8 and data[j-1]['o'] > data[j-1]['c']:
                        base_high = max(base_high, data[j]['h']); base_low = min(base_low, data[j]['l']); base_candles_count += 1
                    else: break
                if base_candles_count > 0 and base_high < current_price: demand_zone = {'high': base_high, 'low': base_low}
        
        if supply_zone and demand_zone: break
            
    return {'supply': supply_zone, 'demand': demand_zone}

def predict_next_candles(candles_data, num_predictions=20): # Kept for API compatibility, but no longer used in core logic
    return []

# --- BingX Client & Bot Workers ---
class BingXClient:
    def __init__(self, api_key, secret_key, demo_mode=True): self.api_key, self.secret_key, self.demo_mode = api_key, secret_key, demo_mode
    def _sign(self, params_str): return hmac.new(self.secret_key.encode('utf-8'), params_str.encode('utf-8'), hashlib.sha256).hexdigest()
    def _request(self, method, path, params=None):
        if params is None: params = {}
        params['timestamp'] = int(time.time() * 1000)
        sorted_params = sorted(params.items())
        query_string = urlencode(sorted_params)
        signature = self._sign(query_string)
        url = f"{BINGX_API_URL}{path}?{query_string}&signature={signature}"; headers = {'X-BX-APIKEY': self.api_key}
        try:
            response = session.request(method.upper(), url, headers=headers, timeout=(5, 10))
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            app.logger.error(f"BingX API request failed: {e.response.text if e.response else e}")
            return None
    
    def get_balance(self):
        if self.demo_mode:
            app.logger.info("[DEMO] Fetching balance.")
            return {"code": 0, "data": {"balance": {"balance": "10000.00", "currency": "USDT"}}}
        return self._request('GET', "/openApi/swap/v2/user/balance", {"currency": "USDT"})

    def place_order(self, symbol, side, position_side, quantity, leverage):
        if self.demo_mode:
            app.logger.info(f"[DEMO] Place {side} {position_side} order: {quantity} {symbol} @ {leverage}x")
            return {"code": 0, "msg": "Demo order placed", "data": {"orderId": int(time.time())}}
        bingx_symbol = f"{symbol.replace('USDT', '')}-USDT"
        self.set_leverage(bingx_symbol, position_side.upper(), leverage)
        params = {"symbol": bingx_symbol, "side": side.upper(), "positionSide": position_side.upper(), "type": "MARKET", "quantity": f"{float(quantity):.5f}"}
        return self._request('POST', "/openApi/swap/v2/trade/order", params)
    
    def set_leverage(self, symbol, side, leverage): return self._request('POST', "/openApi/swap/v2/trade/leverage", {"symbol": symbol, "side": side, "leverage": leverage})

# --- REWRITTEN trade_bot_worker WITH S/D LOGIC ---
def trade_bot_worker():
    app.logger.info("Trading bot worker thread started.")
    analysis_interval = 60
    last_analysis_time = 0

    while True:
        time.sleep(2) # Slow down loop slightly
        try:
            with trade_list_lock: trade_list_copy = list(TRADE_LIST)
            if not trade_list_copy:
                time.sleep(5)
                continue

            with settings_lock:
                client = BingXClient(SETTINGS['bingx_api_key'], SETTINGS['bingx_secret_key'], SETTINGS['mode'] == 'demo')
                risk_percentage = SETTINGS.get('risk_percentage', 1.0)
                leverage = SETTINGS['leverage']
            
            with positions_lock: active_positions_copy = dict(ACTIVE_POSITIONS)
            
            # High-frequency TP/SL monitoring is handled by pnl_updater_worker now
            
            # Perform main analysis only once per minute
            if time.time() - last_analysis_time < analysis_interval:
                continue
            
            last_analysis_time = time.time()
            balance_response = client.get_balance()
            total_balance = 0
            if balance_response and balance_response.get('code') == 0:
                total_balance = float(balance_response['data']['balance']['balance'])
            else:
                app.logger.error("Could not fetch account balance. Skipping trade cycle."); time.sleep(10); continue
            
            risk_usdt = total_balance * (risk_percentage / 100)

            for item in trade_list_copy:
                try:
                    item_id, symbol, interval = item['id'], item['symbol'], item['interval']
                    with positions_lock: position_data = ACTIVE_POSITIONS.get(item_id)
                    with status_lock: last_close_time = BOT_STATUS.get(item_id, {}).get('last_close_time', 0)
                    
                    raw_candles = get_bybit_data(symbol, interval, limit=500)
                    if len(raw_candles) < 50: continue
                    current_price = float(raw_candles[-1][4])

                    zones = find_supply_demand_zones(raw_candles)
                    supply_zone = zones['supply']
                    demand_zone = zones['demand']
                    
                    if position_data:
                        direction = position_data['direction']
                        is_long = direction == 'long'
                        signal_reversed = (is_long and supply_zone and current_price >= supply_zone['low']) or \
                                          (not is_long and demand_zone and current_price <= demand_zone['high'])
                        if signal_reversed:
                            app.logger.info(f"[REVERSAL] Contrary zone detected for {symbol}. Closing {direction} position.")
                            position_side, order_side = direction.upper(), "SELL" if is_long else "BUY"
                            res = client.place_order(symbol, order_side, position_side, position_data['quantity'], leverage)
                            if res and res.get('code') == 0:
                                with positions_lock, status_lock:
                                    if item_id in ACTIVE_POSITIONS: del ACTIVE_POSITIONS[item_id]
                                    BOT_STATUS[item_id] = {"message": "Waiting...", "color": "#fff", "last_close_time": time.time()}

                    elif time.time() - last_close_time > TRADE_COOLDOWN_SECONDS:
                        direction, entry_price, sl_price, tp_price = None, None, None, None
                        
                        if demand_zone and current_price <= demand_zone['high'] and current_price >= demand_zone['low']:
                            direction = 'long'
                            entry_price = current_price
                            sl_price = demand_zone['low'] * 0.999
                            tp_price = entry_price + (abs(entry_price - sl_price) * 2.5)

                        elif supply_zone and current_price <= supply_zone['high'] and current_price >= supply_zone['low']:
                            direction = 'short'
                            entry_price = current_price
                            sl_price = supply_zone['high'] * 1.001
                            tp_price = entry_price - (abs(entry_price - sl_price) * 2.5)
                            
                        if direction and abs(entry_price - sl_price) > 0:
                            liquidation_price = calculate_liquidation_price(entry_price, leverage, direction)
                            if liquidation_price is not None:
                                if direction == "long" and sl_price <= liquidation_price: sl_price = liquidation_price * 1.001
                                elif direction == "short" and sl_price >= liquidation_price: sl_price = liquidation_price * 0.999

                            quantity = risk_usdt / abs(entry_price - sl_price)
                            position_side, order_side = direction.upper(), "BUY" if direction == 'long' else "SELL"
                            res = client.place_order(symbol, order_side, position_side, quantity, leverage)
                            if res and res.get('code') == 0:
                                with positions_lock:
                                    ACTIVE_POSITIONS[item_id] = {'symbol': symbol, 'quantity': quantity, 'direction': direction, 'entry_price': entry_price, 'tp_price': tp_price, 'sl_price': sl_price}
                                    app.logger.info(f"Opened {direction} position for {symbol} based on S/D zone.")
                except Exception as e:
                    app.logger.error(f"Error in analysis for {item.get('symbol', 'N/A')}: {e}", exc_info=False)
                time.sleep(1)
        except Exception as e:
            app.logger.error(f"FATAL ERROR in main trade_bot_worker loop: {e}", exc_info=True)
            time.sleep(10)

def pnl_updater_worker():
    app.logger.info("PnL updater and TP/SL monitor thread started.")
    while True:
        time.sleep(2)
        try:
            with positions_lock:
                if not ACTIVE_POSITIONS: continue
                active_positions_copy = dict(ACTIVE_POSITIONS)
                symbols_to_fetch = list(set(pos['symbol'] for pos in active_positions_copy.values()))
            
            if not symbols_to_fetch: continue
            ticker_prices = get_bybit_ticker_data(symbols_to_fetch)
            if not ticker_prices: continue

            with settings_lock: 
                leverage = SETTINGS.get('leverage', 10)
                client = BingXClient(SETTINGS['bingx_api_key'], SETTINGS['bingx_secret_key'], SETTINGS['mode'] == 'demo')

            for item_id, position in active_positions_copy.items():
                symbol = position['symbol']
                if symbol in ticker_prices:
                    current_price = ticker_prices[symbol]
                    entry_price, quantity, direction = position['entry_price'], position['quantity'], position['direction']
                    
                    # --- PnL Calculation ---
                    pnl = (current_price - entry_price) * quantity if direction == 'long' else (entry_price - current_price) * quantity
                    initial_margin = (entry_price * quantity) / leverage
                    pnl_pct = (pnl / initial_margin) * 100 if initial_margin > 0 else 0
                    with status_lock:
                        BOT_STATUS[item_id] = { "message": f"In {direction.upper()}", "color": "#28a745" if pnl >= 0 else "#dc3545", "pnl": pnl, "pnl_pct": pnl_pct }
                    
                    # --- TP/SL Execution ---
                    tp, sl = position.get('tp_price'), position.get('sl_price')
                    close_position, close_reason = False, ""
                    if direction == 'long':
                        if sl and current_price <= sl: close_position, close_reason = True, "SL"
                        elif tp and current_price >= tp: close_position, close_reason = True, "TP"
                    elif direction == 'short':
                        if sl and current_price >= sl: close_position, close_reason = True, "SL"
                        elif tp and current_price <= tp: close_position, close_reason = True, "TP"
                    
                    if close_position:
                        app.logger.info(f"[MONITOR] {close_reason} hit for {symbol}. Closing {direction} position.")
                        position_side = direction.upper(); order_side = "SELL" if direction == 'long' else "BUY"
                        res = client.place_order(symbol, order_side, position_side, quantity, leverage)
                        if res and res.get('code') == 0:
                            app.logger.info(f"Successfully closed {symbol} position via monitor.")
                            with positions_lock, status_lock:
                                if item_id in ACTIVE_POSITIONS: del ACTIVE_POSITIONS[item_id]
                                BOT_STATUS[item_id] = {"message": "Waiting...", "color": "#fff", "last_close_time": time.time()}
                        else:
                            app.logger.error(f"Failed to close {symbol} via monitor: {res.get('msg') if res else 'Unknown error'}")

        except Exception as e: 
            app.logger.error(f"Error in PnL/Monitor worker: {e}", exc_info=False)


# --- UPGRADED Backtesting Engine (High-Resolution with S/D Logic) ---
def get_sub_interval(interval):
    if not interval.isnumeric(): return "60"
    interval_min = int(interval)
    if interval_min >= 240: return "30"
    if interval_min >= 60: return "5"
    if interval_min >= 15: return "1"
    return "1"

def run_backtest_simulation(symbol, interval, start_ts, end_ts):
    with settings_lock:
        leverage = SETTINGS.get('leverage', 10)
        risk_percentage = SETTINGS.get('risk_percentage', 1.0)
        
    # 1. Fetch Data
    app.logger.info(f"Backtest: Fetching primary {interval} candles from Bybit...")
    all_candles_raw = []
    current_start_ts = start_ts
    while current_start_ts < end_ts:
        chunk = get_bybit_data(symbol, interval, start_ts=current_start_ts)
        if not chunk: break
        all_candles_raw.extend(c for c in chunk if int(c[0]) < end_ts)
        last_ts = int(chunk[-1][0])
        if len(chunk) < 1000 or last_ts >= end_ts: break
        current_start_ts = last_ts + 1
    
    if len(all_candles_raw) < 50: raise ValueError("Not enough historical data for the main interval.")
    
    sub_interval = get_sub_interval(interval)
    app.logger.info(f"Backtest: Fetching sub-interval {sub_interval} candles for simulation...")
    all_sub_candles_raw = []
    current_start_ts = start_ts
    while current_start_ts < end_ts:
        chunk = get_bybit_data(symbol, sub_interval, start_ts=current_start_ts)
        if not chunk: break
        all_sub_candles_raw.extend(c for c in chunk if int(c[0]) < end_ts)
        last_ts = int(chunk[-1][0])
        if len(chunk) < 1000 or last_ts >= end_ts: break
        current_start_ts = last_ts + 1

    if not all_sub_candles_raw: raise ValueError("Not enough historical data for the sub-interval.")

    # 2. Pre-computation and Initialization
    main_candle_open_timestamps = {int(c[0]) for c in all_candles_raw}
    ts_to_main_candle_idx = {int(c[0]): i for i, c in enumerate(all_candles_raw)}
    sub_candles = [{'t': int(c[0]), 'o': float(c[1]), 'h': float(c[2]), 'l': float(c[3]), 'c': float(c[4])} for c in all_sub_candles_raw]
    
    trades, equity_curve = [], [{'time': start_ts, 'equity': 10000.0}]
    equity = 10000.0
    open_position = None

    app.logger.info(f"Backtest: Starting simulation on {len(sub_candles)} sub-candles...")

    # 3. Simulation Loop
    for sub_candle in sub_candles:
        current_ts = sub_candle['t']

        if open_position:
            if (open_position['direction'] == 'long' and sub_candle['l'] <= open_position['sl']) or \
               (open_position['direction'] == 'short' and sub_candle['h'] >= open_position['sl']):
                pnl = (open_position['sl'] - open_position['entry_price']) * open_position['quantity'] if open_position['direction'] == 'long' else (open_position['entry_price'] - open_position['sl']) * open_position['quantity']
                equity += pnl
                trades.append({'exit_time': current_ts, 'direction': open_position['direction'].upper(), 'pnl': pnl, 'return_pct': (pnl / open_position['initial_margin']) * 100, 'exit_reason': 'SL'})
                open_position = None
            
            if open_position:
                tp_prices = [open_position['tp1'], open_position['tp2'], open_position['tp3']]
                tp_qtys = [open_position['tp1_qty'], open_position['tp2_qty'], open_position['tp3_qty']]
                tp_flags = ['tp1_hit', 'tp2_hit', 'tp3_hit']
                for i in range(3):
                    if not open_position.get(tp_flags[i]):
                        if (open_position['direction'] == 'long' and sub_candle['h'] >= tp_prices[i]) or \
                           (open_position['direction'] == 'short' and sub_candle['l'] <= tp_prices[i]):
                            qty_to_close = tp_qtys[i]
                            pnl = (tp_prices[i] - open_position['entry_price']) * qty_to_close if open_position['direction'] == 'long' else (open_position['entry_price'] - tp_prices[i]) * qty_to_close
                            equity += pnl
                            trades.append({'exit_time': current_ts, 'direction': open_position['direction'].upper(), 'pnl': pnl, 'return_pct': (pnl / open_position['initial_margin']) * 100, 'exit_reason': f'TP{i+1}'})
                            open_position['quantity'] -= qty_to_close
                            open_position[tp_flags[i]] = True
                if open_position.get('tp3_hit'): open_position = None

        if current_ts in main_candle_open_timestamps:
            current_main_idx = ts_to_main_candle_idx.get(current_ts)
            if current_main_idx and current_main_idx >= 50:
                history_slice = all_candles_raw[max(0, current_main_idx - 500) : current_main_idx + 1]
                zones = find_supply_demand_zones(history_slice)
                
                if open_position and ((open_position['direction'] == 'long' and zones['supply']) or (open_position['direction'] == 'short' and zones['demand'])):
                    pnl = (sub_candle['o'] - open_position['entry_price']) * open_position['quantity'] if open_position['direction'] == 'long' else (open_position['entry_price'] - sub_candle['o']) * open_position['quantity']
                    equity += pnl
                    trades.append({'exit_time': current_ts, 'direction': open_position['direction'].upper(), 'pnl': pnl, 'return_pct': (pnl / open_position['initial_margin']) * 100, 'exit_reason': 'Reversal'})
                    open_position = None
                
                if not open_position:
                    entry_price, direction, zone = sub_candle['o'], None, None
                    if zones['demand'] and entry_price <= zones['demand']['high'] and entry_price >= zones['demand']['low']: direction, zone = 'long', zones['demand']
                    elif zones['supply'] and entry_price <= zones['supply']['high'] and entry_price >= zones['supply']['low']: direction, zone = 'short', zones['supply']
                    
                    if direction and zone:
                        sl = zone['low'] * 0.999 if direction == 'long' else zone['high'] * 1.001
                        risk_per_unit = abs(entry_price - sl)
                        if risk_per_unit > 0:
                            tp1, tp2, tp3 = entry_price + risk_per_unit, entry_price + risk_per_unit * 2, entry_price + risk_per_unit * 3
                            if direction == 'short': tp1, tp2, tp3 = entry_price - risk_per_unit, entry_price - risk_per_unit * 2, entry_price - risk_per_unit * 3
                            total_quantity = (equity * (risk_percentage / 100)) / risk_per_unit
                            if total_quantity > 0:
                                open_position = {
                                    'entry_price': entry_price, 'quantity': total_quantity, 'direction': direction, 'sl': sl,
                                    'tp1': tp1, 'tp2': tp2, 'tp3': tp3, 'tp1_qty': total_quantity * 0.33, 'tp2_qty': total_quantity * 0.33,
                                    'tp3_qty': total_quantity - (total_quantity * 0.33 * 2), 'initial_margin': (entry_price * total_quantity) / leverage
                                }
            equity_curve.append({'time': current_ts, 'equity': equity})
            
    final_equity = equity
    if open_position: final_equity += (sub_candles[-1]['c'] - open_position['entry_price']) * open_position['quantity'] if open_position['direction'] == 'long' else (open_position['entry_price'] - sub_candles[-1]['c']) * open_position['quantity']
    
    # 4. Calculate Final Metrics
    net_profit = final_equity - 10000.0
    total_pnl_events = len(trades)
    win_rate = (len([t for t in trades if t['pnl'] > 0]) / total_pnl_events * 100) if total_pnl_events > 0 else 0
    total_profit = sum(t['pnl'] for t in trades if t['pnl'] > 0)
    total_loss = abs(sum(t['pnl'] for t in trades if t['pnl'] <= 0))
    profit_factor = total_profit / total_loss if total_loss > 0 else "Infinity"
    max_dd, peak = 0, 10000.0
    for item in equity_curve:
        if item['equity'] > peak: peak = item['equity']
        dd = (peak - item['equity']) / peak if peak != 0 else 0
        max_dd = max(max_dd, dd)
    
    return {"metrics": {"net_profit": net_profit, "total_trades": total_pnl_events, "win_rate": win_rate, "profit_factor": profit_factor, "max_drawdown": max_dd * 100, "avg_trade_pnl": (net_profit / total_pnl_events) if total_pnl_events > 0 else 0}, "trades": trades, "equity_curve": equity_curve}


# --- Flask Routes ---
@app.route('/')
def index(): return render_template_string(HTML_TEMPLATE)

@app.route('/api/candles')
def api_candles():
    symbol, interval = request.args.get('symbol', 'BTCUSDT').upper(), request.args.get('interval', '60')
    if interval not in ALLOWED_INTERVALS: return jsonify({"error": "Invalid interval"}), 400
    try:
        raw_candles = get_bybit_data(symbol, interval, limit=500)
        historical = [{"t": int(c[0]), "o": float(c[1]), "h": float(c[2]), "l": float(c[3]), "c": float(c[4])} for c in raw_candles]
        return jsonify({"candles": historical, "predicted": []}) # Prediction removed
    except Exception as e: return jsonify({"error": str(e)}), 500

@app.route('/api/settings', methods=['GET', 'POST'])
def handle_settings():
    with settings_lock:
        if request.method == 'POST': SETTINGS.update(request.json); save_to_json(SETTINGS_FILE, SETTINGS); return jsonify({"status": "success"})
        return jsonify(SETTINGS)

@app.route('/api/trade_list', methods=['GET'])
def get_trade_list():
    with trade_list_lock, status_lock: return jsonify({"trade_list": TRADE_LIST, "bot_status": BOT_STATUS})

@app.route('/api/trade_list/add', methods=['POST'])
def add_to_trade_list():
    item = request.json; item['id'] = str(int(time.time() * 1000))
    with trade_list_lock:
        if not any(i['symbol'] == item['symbol'] and i['interval'] == item['interval'] for i in TRADE_LIST):
            TRADE_LIST.append(item)
            with status_lock: BOT_STATUS[item['id']] = {"message": "Waiting...", "color": "#fff"}
            save_to_json(TRADELIST_FILE, TRADE_LIST)
    return jsonify({"status": "success"})

@app.route('/api/trade_list/remove', methods=['POST'])
def remove_from_trade_list():
    item_id = request.json.get('id')
    with trade_list_lock: global TRADE_LIST; TRADE_LIST = [i for i in TRADE_LIST if i['id'] != item_id]; save_to_json(TRADELIST_FILE, TRADE_LIST)
    with status_lock:
        if item_id in BOT_STATUS: del BOT_STATUS[item_id]
    with positions_lock:
        if item_id in ACTIVE_POSITIONS: del ACTIVE_POSITIONS[item_id]
    return jsonify({"status": "success"})

@app.route('/api/balance')
def get_balance():
    try:
        with settings_lock: client = BingXClient(SETTINGS['bingx_api_key'], SETTINGS['bingx_secret_key'], SETTINGS['mode'] == 'demo')
        balance_data = client.get_balance()
        if balance_data and balance_data.get('code') == 0:
            total_balance = float(balance_data['data']['balance']['balance'])
            return jsonify({"total_balance": total_balance})
        else:
            return jsonify({"error": "Failed to fetch balance", "details": balance_data.get('msg') if balance_data else "No response"}), 500
    except Exception as e:
        app.logger.error(f"Balance fetch error: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500

@app.route('/api/manual_trade', methods=['POST'])
def manual_trade():
    try:
        data = request.json; symbol, side, item_id = data['symbol'], data['side'], data['id']
        with settings_lock:
            client = BingXClient(SETTINGS['bingx_api_key'], SETTINGS['secret_key'], SETTINGS['mode'] == 'demo')
            risk_perc, lev = SETTINGS.get('risk_percentage', 1.0), SETTINGS['leverage']

        balance_res = client.get_balance()
        if not (balance_res and balance_res.get('code') == 0):
            return jsonify({"error": "Could not fetch balance for risk calculation"}), 400
        balance = float(balance_res['data']['balance']['balance'])
        risk_usdt = balance * (risk_perc / 100)

        price_data = get_bybit_ticker_data([symbol])
        if not price_data or symbol not in price_data: return jsonify({"error": "Could not fetch current price"}), 400
        current_price = price_data[symbol]
        
        stop_loss_price = current_price * 0.98 if side == 'long' else current_price * 1.02
        price_diff_per_unit = abs(current_price - stop_loss_price)
        if price_diff_per_unit == 0: return jsonify({"error": "Price difference is zero, cannot calculate quantity"}), 400
        quantity = risk_usdt / price_diff_per_unit

        position_side, order_side = "LONG" if side == 'long' else "SHORT", "BUY" if side == 'long' else "SELL"
        res = client.place_order(symbol, order_side, position_side, quantity, lev)
        if res and res.get('code') == 0:
            with positions_lock, status_lock:
                ACTIVE_POSITIONS[item_id] = {'symbol': symbol, 'quantity': quantity, 'direction': side, 'entry_price': current_price}
                BOT_STATUS.pop(item_id, None)
            return jsonify({"message": f"Manual {side} order placed for {symbol}."})
        return jsonify({"error": f"Failed: {res.get('msg') if res else 'Unknown error'}"}), 400
    except Exception as e: app.logger.error(f"Manual trade error: {e}", exc_info=True); return jsonify({"error": str(e)}), 500

@app.route('/api/manual_close', methods=['POST'])
def manual_close():
    try:
        data = request.json; symbol, item_id = data['symbol'], data['id']
        with positions_lock:
            if item_id not in ACTIVE_POSITIONS: return jsonify({"message": "No active position found by the bot to close."}), 404
            pos = ACTIVE_POSITIONS[item_id]
        with settings_lock: client = BingXClient(SETTINGS['bingx_api_key'], SETTINGS['secret_key'], SETTINGS['mode'] == 'demo'); lev = SETTINGS['leverage']
        position_side, order_side = pos['direction'].upper(), "SELL" if pos['direction'] == 'long' else "BUY"
        res = client.place_order(symbol, order_side, position_side, pos['quantity'], lev)
        if res and res.get('code') == 0:
            with positions_lock, status_lock:
                if item_id in ACTIVE_POSITIONS: del ACTIVE_POSITIONS[item_id]
                BOT_STATUS[item_id] = {"message": "Waiting...", "color": "#fff", "last_close_time": time.time()}
            return jsonify({"message": f"Close order for {symbol} placed."})
        return jsonify({"error": f"Failed to close: {res.get('msg') if res else 'Unknown error'}"}), 400
    except Exception as e: app.logger.error(f"Manual close error: {e}", exc_info=True); return jsonify({"error": str(e)}), 500

@app.route('/api/backtest', methods=['POST'])
def handle_backtest():
    data = request.json
    try:
        start_ts = int(datetime.strptime(data['start_date'], '%Y-%m-%d').timestamp() * 1000); end_ts = int(datetime.strptime(data['end_date'], '%Y-%m-%d').timestamp() * 1000)
        results = run_backtest_simulation(data['symbol'], data['interval'], start_ts, end_ts); 
        return jsonify(results)
    except Exception as e: 
        app.logger.error(f"Backtest error: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 400

# --- Main Execution ---
if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    threading.Thread(target=trade_bot_worker, daemon=True).start()
    threading.Thread(target=pnl_updater_worker, daemon=True).start()
    app.run(host='0.0.0.0', port=5000, debug=False)
