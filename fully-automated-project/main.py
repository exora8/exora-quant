# ==============================================================================
# Exora Quant AI - v3.8 with Bot-Side TP/SL Management
# ==============================================================================
# This version is MODIFIED to fetch all price and candle data from the
# Bybit PERPETUAL (linear) market instead of the SPOT market. This aligns
# the data source with the bot's purpose of trading perpetual contracts,
# increasing stability and accuracy.
# THIS VERSION adds a configurable "Trigger Percentage" to the web UI.
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
    "risk_usdt": 10,
    "leverage": 10,
    "trigger_percentage": 4.0  # NEW: Configurable trade entry threshold
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

# --- HTML & JavaScript Template (MODIFIED) ---
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Exora Quant AI v3.8</title>
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
    <div class="panels-container"><div id="settings-panel" class="panel"><h3>Settings</h3><div class="setting-item"><label for="api-key">API Key:</label><input type="text" id="api-key"></div><div class="setting-item"><label for="secret-key">Secret Key:</label><input type="password" id="secret-key"></div><div class="setting-item"><label for="mode">Mode:</label><select id="mode"><option value="demo">Demo</option><option value="live">Live</option></select></div><div class="setting-item"><label for="risk-usdt">Risk (USDT):</label><input type="number" id="risk-usdt" value="10"></div><div class="setting-item"><label for="leverage">Leverage:</label><input type="number" id="leverage" value="10"></div><div class="setting-item"><label for="trigger-percentage">Trigger %:</label><input type="number" id="trigger-percentage" value="4.0" step="0.1" min="0"></div><button id="save-settings-btn">Save Settings</button></div><div id="tradelist-panel" class="panel"><h3>Live Trade List</h3><table id="trade-list-table"><thead><tr><th>Symbol</th><th>Timeframe</th><th>Status</th><th>PnL</th><th>Manual Control</th></tr></thead><tbody></tbody></table></div><div id="backtest-panel" class="panel"><h3>Backtest <button id="toggle-backtest-size-btn" title="Maximize">□</button></h3><div id="backtest-controls"><input type="text" id="backtest-symbol" value="BTCUSDT"><select id="backtest-interval"><option value="60">1 hour</option><option value="240">4 hours</option><option value="D">Daily</option></select><input type="date" id="backtest-start"><input type="date" id="backtest-end"><button id="run-backtest-btn">Run</button><div id="backtest-status" style="color: #ffc107;"></div></div><div id="backtest-results"><div id="equitychartdiv"></div><div id="backtest-stats"></div><div id="backtest-trades-table-container" style="height: 80px; overflow-y: auto;"><table id="backtest-trades-table" class="trade-list-table"><thead><tr><th>Exit Time</th><th>Side</th><th>PnL</th><th>Return %</th><th>Reason</th></tr></thead><tbody></tbody></table></div></div></div></div>
<script>
document.addEventListener('DOMContentLoaded', function () {
    let root, chart, equityRoot;
    async function manualTrade(side, symbol, id) { if (!confirm(`Are you sure you want to place a manual ${side.toUpperCase()} order for ${symbol}?`)) return; try { const response = await fetch('/api/manual_trade', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ side, symbol, id }) }); const result = await response.json(); alert(result.message || result.error); } catch (error) { alert(`Error placing manual trade: ${error}`); } }
    async function manualClose(symbol, id) { if (!confirm(`Are you sure you want to close the position for ${symbol}?`)) return; try { const response = await fetch('/api/manual_close', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ symbol, id }) }); const result = await response.json(); alert(result.message || result.error); } catch (error) { alert(`Error closing position: ${error}`); } }
    async function refreshTradeList() { const response = await fetch('/api/trade_list'); const { trade_list, bot_status } = await response.json(); const tableBody = document.querySelector('#trade-list-table tbody'); tableBody.innerHTML = ''; trade_list.forEach(item => { const status = bot_status[item.id] || { message: "Initializing...", color: "#fff" }; let pnlCell = '<td>-</td>'; if (status.pnl !== undefined) { const pnl = status.pnl; const pnl_pct = status.pnl_pct; const pnlColor = pnl > 0 ? '#28a745' : (pnl < 0 ? '#dc3545' : '#fff'); pnlCell = `<td style="color: ${pnlColor}; font-weight: bold;">${pnl.toFixed(2)} <span style="font-size:0.8em; opacity: 0.8;">(${pnl_pct.toFixed(2)}%)</span></td>`; } const row = `<tr><td>${item.symbol}</td><td>${item.interval_text}</td><td style="color:${status.color}">${status.message}</td>${pnlCell}<td><button class="manual-trade-btn long-btn" data-id="${item.id}" data-symbol="${item.symbol}">Long</button><button class="manual-trade-btn short-btn" data-id="${item.id}" data-symbol="${item.symbol}">Short</button><button class="manual-trade-btn close-btn" data-id="${item.id}" data-symbol="${item.symbol}">Close</button><button class="remove-btn" data-id="${item.id}">X</button></td></tr>`; tableBody.insertAdjacentHTML('beforeend', row); }); document.querySelectorAll('.remove-btn').forEach(btn => { btn.addEventListener('click', () => removeTradeItem(btn.dataset.id)); }); document.querySelectorAll('.long-btn').forEach(btn => { btn.addEventListener('click', () => manualTrade('long', btn.dataset.symbol, btn.dataset.id)); }); document.querySelectorAll('.short-btn').forEach(btn => { btn.addEventListener('click', () => manualTrade('short', btn.dataset.symbol, btn.dataset.id)); }); document.querySelectorAll('.close-btn').forEach(btn => { btn.addEventListener('click', () => manualClose(btn.dataset.symbol, btn.dataset.id)); }); };
    let xAxis, yAxis; function createMainChart() { if (root) root.dispose(); root = am5.Root.new("chartdiv"); root.setThemes([am5themes_Animated.new(root), am5themes_Dark.new(root)]); chart = root.container.children.push(am5xy.XYChart.new(root, { panX: true, wheelX: "panX", pinchZoomX: true })); chart.set("cursor", am5xy.XYCursor.new(root, { behavior: "panX" })).lineY.set("visible", false); xAxis = chart.xAxes.push(am5xy.DateAxis.new(root, { baseInterval: { timeUnit: "minute", count: 60 }, renderer: am5xy.AxisRendererX.new(root, { minGridDistance: 70 }) })); yAxis = chart.yAxes.push(am5xy.ValueAxis.new(root, { renderer: am5xy.AxisRendererY.new(root, {}) })); let series = chart.series.push(am5xy.CandlestickSeries.new(root, { name: "Historical", xAxis: xAxis, yAxis: yAxis, valueXField: "t", openValueYField: "o", highValueYField: "h", lowValueYField: "l", valueYField: "c" })); let predictedSeries = chart.series.push(am5xy.CandlestickSeries.new(root, { name: "Predicted", xAxis: xAxis, yAxis: yAxis, valueXField: "t", openValueYField: "o", highValueYField: "h", lowValueYField: "l", valueYField: "c" })); predictedSeries.columns.template.setAll({ fill: am5.color(0xaaaaaa), stroke: am5.color(0xaaaaaa) }); chart.set("scrollbarX", am5.Scrollbar.new(root, { orientation: "horizontal" })); };
    async function fetchChartData() { createMainChart(); const symbol = document.getElementById('symbol').value.toUpperCase().trim(); const interval = document.getElementById('interval').value; const numPredictions = document.getElementById('num_predictions').value; if (!symbol) { document.getElementById('status').innerText = 'Error: Symbol cannot be empty.'; return; } document.getElementById('status').innerText = 'Fetching chart data...'; try { const response = await fetch(`/api/candles?symbol=${symbol}&interval=${interval}&predictions=${numPredictions}`); if (!response.ok) throw new Error((await response.json()).error); const data = await response.json(); const intervalConfig = !isNaN(interval) ? { timeUnit: "minute", count: parseInt(interval) } : { timeUnit: { 'D': 'day', 'W': 'week', 'M': 'month' }[interval] || 'day', count: 1 }; xAxis.set("baseInterval", intervalConfig); chart.series.getIndex(0).data.setAll(data.candles); chart.series.getIndex(1).data.setAll(data.predicted); document.getElementById('status').innerText = 'Chart updated.'; } catch (error) { document.getElementById('status').innerText = `Error: ${error.message}`; } finally { setTimeout(() => { document.getElementById('status').innerText = ''; }, 3000); }};
    async function saveSettings() { const settings = { bingx_api_key: document.getElementById('api-key').value, bingx_secret_key: document.getElementById('secret-key').value, mode: document.getElementById('mode').value, risk_usdt: parseFloat(document.getElementById('risk-usdt').value), leverage: parseInt(document.getElementById('leverage').value), trigger_percentage: parseFloat(document.getElementById('trigger-percentage').value) }; await fetch('/api/settings', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(settings) }); alert('Settings saved!'); };
    async function loadSettings() { const response = await fetch('/api/settings'); const settings = await response.json(); document.getElementById('api-key').value = settings.bingx_api_key; document.getElementById('secret-key').value = settings.bingx_secret_key; document.getElementById('mode').value = settings.mode; document.getElementById('risk-usdt').value = settings.risk_usdt; document.getElementById('leverage').value = settings.leverage; document.getElementById('trigger-percentage').value = settings.trigger_percentage; };
    async function addTradeItem() { const item = { symbol: document.getElementById('symbol').value.toUpperCase().trim(), interval: document.getElementById('interval').value, interval_text: document.getElementById('interval').options[document.getElementById('interval').selectedIndex].text, predictions: parseInt(document.getElementById('num_predictions').value) }; await fetch('/api/trade_list/add', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(item) }); refreshTradeList(); };
    async function removeTradeItem(id) { await fetch('/api/trade_list/remove', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ id: id }) }); refreshTradeList(); };
    let backtestRunning = false;
    async function runBacktest() { if (backtestRunning) return; backtestRunning = true; const statusEl = document.getElementById('backtest-status'); const resultsEl = document.getElementById('backtest-results'); statusEl.textContent = 'Fetching historical data...'; resultsEl.style.display = 'none'; const payload = { symbol: document.getElementById('backtest-symbol').value.toUpperCase(), interval: document.getElementById('backtest-interval').value, start_date: document.getElementById('backtest-start').value, end_date: document.getElementById('backtest-end').value, }; try { statusEl.textContent = 'Running simulation...'; const response = await fetch('/api/backtest', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload) }); if (!response.ok) throw new Error((await response.json()).error); const results = await response.json(); displayBacktestResults(results); statusEl.textContent = 'Backtest complete.'; } catch (error) { statusEl.textContent = `Error: ${error.message}`; } finally { backtestRunning = false; } }
    function displayBacktestResults(results) { document.getElementById('backtest-results').style.display = 'block'; const stats = results.metrics; const statsEl = document.getElementById('backtest-stats'); statsEl.innerHTML = `<div>Net Profit: <span style="color:${stats.net_profit > 0 ? '#28a745' : '#dc3545'}">${stats.net_profit.toFixed(2)} USDT</span></div><div>Win Rate: <span>${stats.win_rate.toFixed(2)}%</span></div><div>Profit Factor: <span>${stats.profit_factor.toFixed(2)}</span></div><div>Total Trades: <span>${stats.total_trades}</span></div><div>Avg Trade PnL: <span>${stats.avg_trade_pnl.toFixed(2)}</span></div><div>Max Drawdown: <span style="color:#dc3545">${stats.max_drawdown.toFixed(2)}%</span></div>`; const tradesTableBody = document.querySelector('#backtest-trades-table tbody'); tradesTableBody.innerHTML = ''; results.trades.forEach(trade => { const pnlColor = trade.pnl > 0 ? '#28a745' : '#dc3545'; const row = `<tr><td>${new Date(trade.exit_time).toLocaleString()}</td><td>${trade.direction}</td><td style="color:${pnlColor}">${trade.pnl.toFixed(2)}</td><td style="color:${pnlColor}">${trade.return_pct.toFixed(2)}%</td><td>${trade.exit_reason || 'N/A'}</td></tr>`; tradesTableBody.insertAdjacentHTML('afterbegin', row); }); createEquityChart(results.equity_curve); }
    function createEquityChart(data) { if (equityRoot) equityRoot.dispose(); equityRoot = am5.Root.new("equitychartdiv"); equityRoot.setThemes([am5themes_Dark.new(equityRoot)]); let chart = equityRoot.container.children.push(am5xy.XYChart.new(equityRoot, { panX: true, wheelX: "zoomX", pinchZoomX: true, paddingLeft: 0, paddingRight: 0 })); let xAxis = chart.xAxes.push(am5xy.DateAxis.new(equityRoot, { baseInterval: { timeUnit: "day", count: 1 }, renderer: am5xy.AxisRendererX.new(equityRoot, { minGridDistance: 50 }), })); let yAxis = chart.yAxes.push(am5xy.ValueAxis.new(equityRoot, { renderer: am5xy.AxisRendererY.new(equityRoot, {}) })); let series = chart.series.push(am5xy.LineSeries.new(equityRoot, { name: "Equity", xAxis: xAxis, yAxis: yAxis, valueYField: "equity", valueXField: "time", stroke: am5.color(0x00aaff), fill: am5.color(0x00aaff), })); series.fills.template.setAll({ fillOpacity: 0.1, visible: true }); series.data.setAll(data); }
    function initialize() { loadSettings(); refreshTradeList(); setInterval(refreshTradeList, 1000); const today = new Date(); const yesterday = new Date(today); yesterday.setDate(yesterday.getDate() - 1); const threeMonthsAgo = new Date(today); threeMonthsAgo.setMonth(threeMonthsAgo.getMonth() - 3); document.getElementById('backtest-end').valueAsDate = yesterday; document.getElementById('backtest-start').valueAsDate = threeMonthsAgo; document.getElementById('toggle-controls-btn').addEventListener('click', () => document.querySelector('.controls-overlay').classList.toggle('hidden')); document.getElementById('fetchButton').addEventListener('click', fetchChartData); document.getElementById('add-to-list-btn').addEventListener('click', addTradeItem); document.getElementById('save-settings-btn').addEventListener('click', saveSettings); document.getElementById('run-backtest-btn').addEventListener('click', runBacktest); document.getElementById('toggle-backtest-size-btn').addEventListener('click', (e) => { const btn = e.target; const container = document.querySelector('.panels-container'); const chartContainer = document.getElementById('chartdiv'); container.classList.toggle('is-maximized'); if (container.classList.contains('is-maximized')) { btn.textContent = '−'; btn.title = "Minimize"; chartContainer.style.height = '40px'; } else { btn.textContent = '□'; btn.title = "Maximize"; chartContainer.style.height = 'calc(100% - 250px)'; } setTimeout(() => { if (equityRoot) { equityRoot.resize(); } if (root) { root.resize(); } }, 350); }); }
    initialize();
});
</script>
</body>
</html>
"""

# --- Data Fetching & Prediction (FIXED) ---
def get_bybit_data(symbol, interval, start_ts=None, end_ts=None, limit=1000):
    # CHANGED: "category" is now "linear" for perpetual contracts
    params = {"category": "linear", "symbol": symbol, "interval": interval, "limit": limit}
    if start_ts: params['start'] = int(start_ts)
    if end_ts: params['end'] = int(end_ts)
    try:
        # Use the global session with retries and add a timeout
        response = session.get(f"{BYBIT_API_URL}/kline", params=params, timeout=(5, 10)) # 5s connect, 10s read
        response.raise_for_status()
        data = response.json()
        if data.get("retCode") != 0: raise ValueError(data.get("retMsg"))
        return list(reversed(data["result"]["list"]))
    except (requests.exceptions.RequestException, ValueError) as e:
        # This error will now only be raised after all retries have failed
        # Log the error but re-raise a more generic ConnectionError to be handled by the worker
        app.logger.warning(f"Bybit kline API error for {symbol}: {e}")
        raise ConnectionError(f"Failed to fetch Bybit kline data for {symbol} after retries.")


def get_bybit_ticker_data(symbols):
    if not isinstance(symbols, list): symbols = [symbols]
    if not symbols: return {}
    # CHANGED: "category" is now "linear" for perpetual contracts
    params = {"category": "linear", "symbol": ",".join(symbols)}
    try:
        # Use the global session with retries and add a timeout
        response = session.get(f"{BYBIT_API_URL}/tickers", params=params, timeout=(5, 10))
        response.raise_for_status()
        data = response.json()
        if data.get("retCode") == 0:
            return {item['symbol']: float(item['lastPrice']) for item in data['result']['list']}
        return {}
    except requests.exceptions.RequestException as e:
        app.logger.error(f"Bybit ticker API error after retries: {e}")
        return {}

def find_similar_patterns_pure_python(data_series, window_size=20, top_n=5):
    if len(data_series) < 2 * window_size: return None
    def dot_product(v1, v2): return sum(x * y for x, y in zip(v1, v2))
    def norm(v): return math.sqrt(sum(x * x for x in v))
    current_pattern = data_series[-window_size:]; current_norm = norm(current_pattern)
    if current_norm == 0: return None
    similarities = []
    for i in range(len(data_series) - window_size):
        historical_pattern = data_series[i : i + window_size]; historical_norm = norm(historical_pattern)
        if historical_norm > 0: similarities.append({"sim": dot_product(historical_pattern, current_pattern) / (historical_norm * current_norm), "outcome_index": i + window_size})
    if not similarities: return None
    similarities.sort(key=lambda x: x["sim"], reverse=True)
    return statistics.mean(data_series[p["outcome_index"]] for p in similarities[:top_n])

def predict_next_candles(candles_data, num_predictions=20):
    if len(candles_data) < 50: return []
    data = [[float(c[i]) for i in range(6)] for c in candles_data]
    upper_wicks = [d[2] - max(d[1], d[4]) for d in data]; lower_wicks = [min(d[1], d[4]) - d[3] for d in data]
    avg_upper_wick = statistics.mean(upper_wicks) if upper_wicks else 0; avg_lower_wick = statistics.mean(lower_wicks) if lower_wicks else 0
    predictions, current_candles = [], data[:]
    for i in range(num_predictions):
        closes = [c[4] for c in current_candles]; log_returns = [math.log(closes[j]/closes[j-1]) for j in range(1,len(closes)) if closes[j-1]>0]
        if not log_returns: break
        predicted_log_return = find_similar_patterns_pure_python(log_returns)
        if predicted_log_return is None: break
        last_close = current_candles[-1][4]; predicted_close = last_close * math.exp(predicted_log_return)
        pred_o, pred_h, pred_l = last_close, max(last_close, predicted_close) + avg_upper_wick, min(last_close, predicted_close) - avg_lower_wick
        interval_ms = int(current_candles[-1][0]) - int(current_candles[-2][0]); new_ts = int(current_candles[-1][0]) + interval_ms
        current_candles.append([new_ts, pred_o, pred_h, pred_l, predicted_close, 0])
        predictions.append({"t": new_ts, "o": pred_o, "h": pred_h, "l": pred_l, "c": predicted_close})
    return predictions

# --- BingX Client & Bot Workers (FIXED) ---
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
            # Use the global session with a timeout. Post requests will not be retried by default.
            response = session.request(method.upper(), url, headers=headers, timeout=(5, 10))
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            app.logger.error(f"BingX API request failed: {e.response.text if e.response else e}")
            return None
    # --- MODIFIED: Removed tp_price and sl_price from the function signature as they are no longer used here ---
    def place_order(self, symbol, side, position_side, quantity, leverage):
        if self.demo_mode:
            app.logger.info(f"[DEMO] Place {side} {position_side} order: {quantity} {symbol} @ {leverage}x")
            return {"code": 0, "msg": "Demo order placed", "data": {"orderId": int(time.time())}}
        bingx_symbol = f"{symbol.replace('USDT', '')}-USDT"
        self.set_leverage(bingx_symbol, position_side.upper(), leverage)
        params = {"symbol": bingx_symbol, "side": side.upper(), "positionSide": position_side.upper(), "type": "MARKET", "quantity": f"{float(quantity):.5f}"}
        # The takeProfit and stopLoss parameters are now removed from the API call
        return self._request('POST', "/openApi/swap/v2/trade/order", params)
    def set_leverage(self, symbol, side, leverage): return self._request('POST', "/openApi/swap/v2/trade/leverage", {"symbol": symbol, "side": side, "leverage": leverage})

# --- MODIFIED trade_bot_worker WITH BOT-SIDE TP/SL LOGIC ---
def trade_bot_worker():
    app.logger.info("Trading bot worker thread started.")
    ticker_check_interval = 5  # Seconds to wait before checking prices for TP/SL
    analysis_interval = 60     # Seconds to wait for a full new candle analysis
    last_analysis_time = 0

    while True:
        time.sleep(1) # Main loop delay to prevent tight-looping on errors
        try:
            with trade_list_lock: trade_list_copy = list(TRADE_LIST)
            if not trade_list_copy:
                time.sleep(5) # If no coins, sleep longer
                continue

            with settings_lock:
                client = BingXClient(SETTINGS['bingx_api_key'], SETTINGS['bingx_secret_key'], SETTINGS['mode'] == 'demo')
                risk = SETTINGS['risk_usdt']
                leverage = SETTINGS['leverage']
                trigger_percentage = SETTINGS.get('trigger_percentage', 4.0)

            # --- High-frequency TP/SL and PnL monitoring ---
            with positions_lock:
                active_positions_copy = dict(ACTIVE_POSITIONS)
                symbols_to_fetch = list(set(pos['symbol'] for pos in active_positions_copy.values()))
            
            if symbols_to_fetch:
                current_prices = get_bybit_ticker_data(symbols_to_fetch)
                if current_prices:
                    for item_id, position in active_positions_copy.items():
                        symbol = position['symbol']
                        if symbol in current_prices:
                            current_price = current_prices[symbol]
                            direction = position['direction']
                            tp = position.get('tp_price')
                            sl = position.get('sl_price')
                            
                            # --- BOT-SIDE TP/SL CHECK ---
                            close_position = False
                            close_reason = ""
                            if direction == 'long':
                                if sl and current_price <= sl: close_position, close_reason = True, "SL"
                                elif tp and current_price >= tp: close_position, close_reason = True, "TP"
                            elif direction == 'short':
                                if sl and current_price >= sl: close_position, close_reason = True, "SL"
                                elif tp and current_price <= tp: close_position, close_reason = True, "TP"
                            
                            if close_position:
                                app.logger.info(f"[AUTO-CLOSE] {close_reason} hit for {symbol}. Closing {direction} position.")
                                position_side = direction.upper()
                                order_side = "SELL" if direction == 'long' else "BUY"
                                res = client.place_order(symbol, order_side, position_side, position['quantity'], leverage)
                                if res and res.get('code') == 0:
                                    app.logger.info(f"Successfully closed {symbol} position due to {close_reason}.")
                                    with positions_lock, status_lock:
                                        if item_id in ACTIVE_POSITIONS: del ACTIVE_POSITIONS[item_id]
                                        BOT_STATUS[item_id] = {"message": "Waiting...", "color": "#fff", "last_close_time": time.time()}
                                else:
                                    app.logger.error(f"Failed to close {symbol} on {close_reason}: {res.get('msg') if res else 'Unknown error'}")
                                continue

            # --- Lower-frequency new signal analysis ---
            if time.time() - last_analysis_time < analysis_interval:
                time.sleep(ticker_check_interval) # Use the high-frequency interval here
                continue
            
            app.logger.info("Starting new signal analysis cycle...")
            last_analysis_time = time.time()

            for item in trade_list_copy:
                try:
                    item_id, symbol, interval = item['id'], item['symbol'], item['interval']
                    
                    with positions_lock:
                        position_data = ACTIVE_POSITIONS.get(item_id)
                    with status_lock:
                        last_close_time = BOT_STATUS.get(item_id, {}).get('last_close_time', 0)
                    
                    raw_candles = get_bybit_data(symbol, interval, limit=50)
                    if len(raw_candles) < 50: continue
                    
                    current_price = float(raw_candles[-1][4])

                    if position_data: # --- Position Management (Reversal Signal) ---
                        predicted_candles = predict_next_candles(raw_candles)
                        if not predicted_candles: continue
                        final_predicted_price = predicted_candles[-1]['c']
                        price_change_pct = ((final_predicted_price - current_price) / current_price) * 100

                        direction = position_data['direction']
                        is_long = direction == 'long'
                        signal_reversed = (is_long and price_change_pct < -0.5) or (not is_long and price_change_pct > 0.5)
                        
                        if signal_reversed:
                            app.logger.info(f"[AUTO-CLOSE] Signal reversed for {symbol}. Closing {direction} position.")
                            position_side = direction.upper()
                            order_side = "SELL" if is_long else "BUY"
                            res = client.place_order(symbol, order_side, position_side, position_data['quantity'], leverage)
                            if res and res.get('code') == 0:
                                app.logger.info(f"Successfully auto-closed {symbol} position.")
                                with positions_lock, status_lock:
                                    if item_id in ACTIVE_POSITIONS: del ACTIVE_POSITIONS[item_id]
                                    BOT_STATUS[item_id] = {"message": "Waiting...", "color": "#fff", "last_close_time": time.time()}
                            else:
                                app.logger.error(f"Failed to auto-close {symbol}: {res.get('msg') if res else 'Unknown error'}")

                    else: # --- Position Entry Logic ---
                        if time.time() - last_close_time < TRADE_COOLDOWN_SECONDS:
                            continue
                        
                        predicted_candles = predict_next_candles(raw_candles)
                        if not predicted_candles: continue
                        final_predicted_price = predicted_candles[-1]['c']
                        price_change_pct = ((final_predicted_price - current_price) / current_price) * 100

                        if abs(price_change_pct) > trigger_percentage:
                            direction = "long" if price_change_pct > 0 else "short"
                            tp_price = current_price * (1 + (price_change_pct * 0.8 / 100))
                            sl_price = current_price * (1 - (price_change_pct * 0.4 / 100)) if direction == "long" else current_price * (1 + (abs(price_change_pct) * 0.4 / 100))
                            
                            if abs(current_price - sl_price) > 0:
                                quantity = risk / abs(current_price - sl_price)
                                position_side = direction.upper()
                                order_side = "BUY" if direction == 'long' else "SELL"
                                
                                app.logger.info(f"[AUTO-TRADE] Entry signal for {symbol}. Placing {direction} order.")
                                res = client.place_order(symbol, order_side, position_side, quantity, leverage)
                                
                                if res and res.get('code') == 0:
                                    app.logger.info(f"Successfully opened {direction} position for {symbol}.")
                                    with positions_lock:
                                        ACTIVE_POSITIONS[item_id] = {
                                            'symbol': symbol, 'quantity': quantity, 'direction': direction, 
                                            'entry_price': current_price, 'tp_price': tp_price, 'sl_price': sl_price
                                        }
                                else:
                                    app.logger.error(f"Failed to open position for {symbol}: {res.get('msg') if res else 'Unknown error'}")

                except Exception as e:
                    app.logger.error(f"Error in trade_bot_worker analysis for item {item.get('symbol', 'N/A')}: {e}", exc_info=False)
                
                # ******** THE CRITICAL FIX IS HERE ********
                # Add a delay between analyzing each coin to prevent API rate limiting.
                time.sleep(1)

        except Exception as e:
            app.logger.error(f"FATAL ERROR in main trade_bot_worker loop: {e}", exc_info=True)
            time.sleep(10) # Wait 10 seconds before restarting the loop on a major error


def pnl_updater_worker():
    app.logger.info("PnL updater thread started.")
    while True:
        time.sleep(2) # Increased from 1s to 2s to be safer with API rate limits
        try:
            with positions_lock:
                if not ACTIVE_POSITIONS: continue
                active_positions_copy = dict(ACTIVE_POSITIONS)
                symbols_to_fetch = list(set(pos['symbol'] for pos in active_positions_copy.values()))
            
            if not symbols_to_fetch: continue
            
            ticker_prices = get_bybit_ticker_data(symbols_to_fetch)
            if not ticker_prices: continue

            with settings_lock: leverage = SETTINGS.get('leverage', 10)
            
            for item_id, position in active_positions_copy.items():
                symbol = position['symbol']
                if symbol in ticker_prices:
                    current_price = ticker_prices[symbol]
                    entry_price = position['entry_price']
                    quantity = position['quantity']
                    direction = position['direction']
                    pnl = (current_price - entry_price) * quantity if direction == 'long' else (entry_price - current_price) * quantity
                    initial_margin = (entry_price * quantity) / leverage
                    pnl_pct = (pnl / initial_margin) * 100 if initial_margin > 0 else 0
                    with status_lock:
                        BOT_STATUS[item_id] = { "message": f"In {direction.upper()}", "color": "#28a745" if pnl >= 0 else "#dc3545", "pnl": pnl, "pnl_pct": pnl_pct }
        except Exception as e: 
            app.logger.error(f"Error in PnL updater worker: {e}", exc_info=False)


# --- Backtesting Engine (MODIFIED) ---
def run_backtest_simulation(symbol, interval, start_ts, end_ts):
    all_candles_raw, current_start_ts = [], start_ts
    while current_start_ts <= end_ts:
        chunk = get_bybit_data(symbol, interval, start_ts=current_start_ts);
        if not chunk: break
        all_candles_raw.extend(chunk); last_ts = int(chunk[-1][0])
        if len(chunk) < 1000 or last_ts >= end_ts: break
        current_start_ts = last_ts + 1
    if len(all_candles_raw) < 50: raise ValueError("Not enough historical data.")
    with settings_lock:
        risk_usdt = SETTINGS['risk_usdt']
        leverage = SETTINGS['leverage']
        trigger_percentage = SETTINGS.get('trigger_percentage', 4.0)

    equity, equity_curve, trades, open_position = 10000.0, [{'time': start_ts, 'equity': 10000.0}], [], None
    for i in range(50, len(all_candles_raw)):
        candle = {'t': int(all_candles_raw[i][0]), 'c': float(all_candles_raw[i][4]), 'h': float(all_candles_raw[i][2]), 'l': float(all_candles_raw[i][3])}
        if open_position:
            exit_price, exit_reason = None, None
            if open_position['direction'] == 'long':
                if candle['l'] <= open_position['sl']: exit_price, exit_reason = open_position['sl'], 'SL'
                elif candle['h'] >= open_position['tp']: exit_price, exit_reason = open_position['tp'], 'TP'
            else:
                if candle['h'] >= open_position['sl']: exit_price, exit_reason = open_position['sl'], 'SL'
                elif candle['l'] <= open_position['tp']: exit_price, exit_reason = open_position['tp'], 'TP'
            if not exit_price:
                predicted = predict_next_candles(all_candles_raw[i-50:i], 20)
                if predicted:
                    last_price = float(all_candles_raw[i-1][4]); change_pct = ((predicted[-1]['c'] - last_price) / last_price) * 100
                    if (open_position['direction']=='long' and change_pct<-0.5) or (open_position['direction']=='short' and change_pct>0.5): exit_price, exit_reason = candle['c'], 'Reversal'
            if exit_price:
                pnl = (exit_price - open_position['entry_price']) * open_position['quantity'] if open_position['direction'] == 'long' else (open_position['entry_price'] - exit_price) * open_position['quantity']
                equity += pnl; trades.append({'exit_time': candle['t'], 'direction': open_position['direction'], 'pnl': pnl, 'return_pct': (pnl / ((open_position['entry_price'] * open_position['quantity']) / leverage)) * 100, 'exit_reason': exit_reason}); open_position = None
        if not open_position:
            predicted = predict_next_candles(all_candles_raw[i-50:i], 20)
            if predicted:
                price = float(all_candles_raw[i-1][4]); change = ((predicted[-1]['c'] - price) / price) * 100
                if abs(change) > trigger_percentage:
                    direction = "long" if change > 0 else "short"
                    tp = price * (1 + (change*0.8/100)); sl = price * (1-(change*0.4/100)) if direction == "long" else price * (1+(abs(change)*0.4/100))
                    if not (any(p['l'] < sl for p in predicted) if direction == "long" else any(p['h'] > sl for p in predicted)) and abs(price-sl)>0:
                        open_position = {'entry_price': price, 'quantity': risk_usdt / abs(price - sl), 'direction': direction, 'tp': tp, 'sl': sl}
        equity_curve.append({'time': candle['t'], 'equity': equity})
    net_profit = equity - 10000; total_trades = len(trades); win_rate = (len([t for t in trades if t['pnl'] > 0]) / total_trades * 100) if total_trades > 0 else 0; total_profit = sum(t['pnl'] for t in trades if t['pnl']>0); total_loss = abs(sum(t['pnl'] for t in trades if t['pnl']<=0)); profit_factor = total_profit / total_loss if total_loss > 0 else float('inf'); max_dd, peak = 0, -1
    for item in equity_curve:
        if item['equity'] > peak: peak = item['equity']
        dd = (peak - item['equity']) / peak if peak != 0 else 0; max_dd = max(max_dd, dd)
    return {"metrics": {"net_profit": net_profit, "total_trades": total_trades, "win_rate": win_rate, "profit_factor": profit_factor, "max_drawdown": max_dd * 100, "avg_trade_pnl": (net_profit / total_trades) if total_trades > 0 else 0}, "trades": trades, "equity_curve": equity_curve}

# --- Flask Routes (Unchanged)---
@app.route('/')
def index(): return render_template_string(HTML_TEMPLATE)
@app.route('/api/candles')
def api_candles():
    symbol, interval, num_predictions = request.args.get('symbol', 'BTCUSDT').upper(), request.args.get('interval', '60'), max(1, min(request.args.get('predictions', 20, type=int), 50))
    if interval not in ALLOWED_INTERVALS: return jsonify({"error": "Invalid interval"}), 400
    try:
        raw_candles = get_bybit_data(symbol, interval)[-500:]
        historical = [{"t": int(c[0]), "o": float(c[1]), "h": float(c[2]), "l": float(c[3]), "c": float(c[4])} for c in raw_candles]
        predicted = predict_next_candles(raw_candles); return jsonify({"candles": historical, "predicted": predicted})
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
    item_id = request.json.get('id');
    with trade_list_lock: global TRADE_LIST; TRADE_LIST = [i for i in TRADE_LIST if i['id'] != item_id]; save_to_json(TRADELIST_FILE, TRADE_LIST)
    with status_lock:
        if item_id in BOT_STATUS: del BOT_STATUS[item_id]
    with positions_lock:
        if item_id in ACTIVE_POSITIONS: del ACTIVE_POSITIONS[item_id]
    return jsonify({"status": "success"})
@app.route('/api/manual_trade', methods=['POST'])
def manual_trade():
    try:
        data = request.json; symbol, side, item_id = data['symbol'], data['side'], data['id']
        with settings_lock: client = BingXClient(SETTINGS['bingx_api_key'], SETTINGS['secret_key'], SETTINGS['mode'] == 'demo'); risk, lev = SETTINGS['risk_usdt'], SETTINGS['leverage']
        price_data = get_bybit_ticker_data(symbol)
        if not price_data or symbol not in price_data: return jsonify({"error": "Could not fetch current price"}), 400
        current_price = price_data[symbol]; quantity = risk / (current_price * 0.02)
        position_side = "LONG" if side == 'long' else "SHORT"; order_side = "BUY" if side == 'long' else "SELL"
        app.logger.info(f"[MANUAL] Placing {side} order for {symbol}. Qty: {quantity:.4f}")
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
        position_side = pos['direction'].upper(); order_side = "SELL" if pos['direction'] == 'long' else "BUY"
        app.logger.info(f"[MANUAL-CLOSE] Closing {pos['direction']} for {symbol}. Qty: {pos['quantity']:.4f}")
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
        results = run_backtest_simulation(data['symbol'], data['interval'], start_ts, end_ts); return jsonify(results)
    except Exception as e: app.logger.error(f"Backtest error: {e}", exc_info=True); return jsonify({"error": str(e)}), 400

# --- Main Execution ---
if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    threading.Thread(target=trade_bot_worker, daemon=True).start()
    threading.Thread(target=pnl_updater_worker, daemon=True).start()
    app.run(host='0.0.0.0', port=5000, debug=False)
