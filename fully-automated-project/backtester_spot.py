# ==============================================================================
# Exora Quant AI - v3.9.2 (Binance & Backtest UI with Spot Mode & Reversal)
# ==============================================================================
# This version is MODIFIED to fetch all price and candle data from the
# Binance PERPETUAL (fapi) market instead of Bybit. This aligns the data
# source with a different major exchange.
# THIS VERSION MODIFIES the web UI to exclusively display the backtesting
# panel, hiding live trading, charting, and settings controls.
# THIS VERSION adds a configurable "Trigger Percentage" to the web UI.
# THIS VERSION IS MODIFIED to use percentage-based risk and display total balance.
# THIS VERSION ADDS a liquidation detection feature to adjust SL and prevent liquidation.
# THIS VERSION MODIFIES the backtest engine to use high-resolution data for realistic volatility simulation.
# THIS VERSION ADDS user-configurable parameters (Leverage, Equity, Risk, Trigger) to the backtest UI.
# THIS VERSION RE-ENGINEERS the backtest to loop on sub-interval data for high-precision entries and exits.
# THIS VERSION ADDS a DCA/regular investment feature to the backtesting engine.
# THIS VERSION ADDS A "SPOT MODE" to the backtesting engine for unleveraged, buy-only strategies.
# THIS VERSION FIXES a JSON serialization error for 'Infinity' in the backtest profit factor calculation.
# THIS VERSION ADDS a reversal detection feature to close open positions on new contrary signals.
# ==============================================================================
# BACKTESTER LOGIC for 'spot' mode has been updated to match Exora Quant Spot Edition v1.2 logic.
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
BINANCE_API_URL = "https://fapi.binance.com"
BINGX_API_URL = "https://open-api.bingx.com"
SETTINGS_FILE = "settings.json"
TRADELIST_FILE = "tradelist.json"
TRADE_COOLDOWN_SECONDS = 300 # 5 minutes
MAINTENANCE_MARGIN_RATE = 0.005 # Standard rate for major pairs like BTC/ETH

# --- Mapping for Binance Intervals ---
BINANCE_INTERVAL_MAP = {
    "15": "15m", "30": "30m", "60": "1h", "120": "2h",
    "240": "4h", "360": "6h", "720": "12h",
    "D": "1d", "W": "1w", "M": "1M"
}

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
    "trigger_percentage": 4.0
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

# --- HTML & JavaScript Template (MODIFIED FOR BACKTEST-ONLY VIEW & SPOT MODE) ---
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Exora Quant AI v3.9.2 (Binance & Backtest UI w/Reversal)</title>
    <style>
        html, body { width: 100%; height: 100%; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background-color: #000; color: #eee; font-size: 14px; }
        
        /* Hide all non-backtest elements */
        #chartdiv, .controls-wrapper, #settings-panel, #tradelist-panel {
            display: none;
        }

        /* Make backtest panel fill the screen */
        .panels-container {
            height: 100vh;
            width: 100%;
            display: flex;
            justify-content: center;
            align-items: flex-start;
            background: #111;
            padding-top: 20px;
            box-sizing: border-box;
        }
        .panel { padding: 15px; overflow-y: auto; box-sizing: border-box; }
        .panel h3 { margin-top: 0; border-bottom: 1px solid #444; padding-bottom: 8px; color: #00aaff; }
        #backtest-panel {
            width: 100%;
            max-width: 1000px;
            height: calc(100vh - 40px);
            display: flex;
            flex-direction: column;
            background-color: #1a1a1a;
            border-radius: 8px;
            border: 1px solid #333;
        }
        #backtest-controls { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-bottom: 12px; font-size: 13px; }
        .bt-control-group { display: flex; flex-direction: column; gap: 4px; }
        .bt-control-group > div { display: flex; align-items: center; gap: 5px; }
        .bt-control-group label { color: #bbb; min-width: 60px; text-align: right; }
        .bt-control-group input, .bt-control-group select { width: 100%; box-sizing: border-box; padding: 6px; font-size: 13px; border-radius: 4px; border: 1px solid #444; background-color: #2a2a2a; color: #eee; }
        input:disabled { background-color: #222; color: #777; }
        #run-backtest-btn-container { grid-column: 1 / -1; display: flex; gap: 10px; align-items: center; }
        #run-backtest-btn { background-color: #17a2b8; border-color: #17a2b8; padding: 8px 16px; flex-grow: 1; border: none; font-weight: bold; cursor: pointer; }
        #backtest-status { color: #ffc107; }
        #backtest-results { flex-grow: 1; overflow-y: auto; display: none; margin-top: 15px; }
        #equitychartdiv { width: 100%; height: 250px; margin-bottom: 10px; }
        #backtest-stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 5px 15px; margin-bottom: 10px; font-size: 12px; background: #222; padding: 10px; border-radius: 5px;}
        #backtest-stats div > span { font-weight: bold; color: #00aaff; }
        #backtest-trades-table-container { flex-grow: 1; height: 200px; overflow-y: auto; border: 1px solid #333; border-radius: 5px; }
        #backtest-trades-table { width: 100%; border-collapse: collapse; font-size: 11px; }
        #backtest-trades-table th, #backtest-trades-table td { padding: 6px 8px; text-align: left; border-bottom: 1px solid #2a2a2a; }
    </style>
    <script src="https://cdn.amcharts.com/lib/5/index.js"></script><script src="https://cdn.amcharts.com/lib/5/xy.js"></script><script src="https://cdn.amcharts.com/lib/5/themes/Animated.js"></script><script src="https://cdn.amcharts.com/lib/5/themes/Dark.js"></script>
</head>
<body>
    <div id="chartdiv"></div><div class="controls-wrapper"></div>
    <div class="panels-container">
        <div id="settings-panel" class="panel"></div>
        <div id="tradelist-panel" class="panel"></div>
        <div id="backtest-panel" class="panel">
            <h3>Backtest Engine</h3>
            <div id="backtest-controls">
                <div class="bt-control-group">
                    <div><label for="backtest-symbol">Symbol:</label><input type="text" id="backtest-symbol" value="BTCUSDT"></div>
                    <div><label for="backtest-start">Start:</label><input type="date" id="backtest-start"></div>
                    <div><label for="backtest-equity">Equity:</label><input type="number" id="backtest-equity" value="10000" step="100"></div>
                    <div><label for="backtest-risk">Risk %:</label><input type="number" id="backtest-risk" value="10.0" step="0.1" min="0.1"></div>
                    <div><label for="backtest-dca-freq">DCA Freq:</label><select id="backtest-dca-freq"><option value="none">None</option><option value="weekly">Weekly</option><option value="monthly">Monthly</option></select></div>
                </div>
                <div class="bt-control-group">
                    <div><label for="backtest-interval">TF:</label><select id="backtest-interval"><option value="60">1 hour</option><option value="240">4 hours</option><option value="D">Daily</option></select></div>
                    <div><label for="backtest-end">End:</label><input type="date" id="backtest-end"></div>
                    <div><label for="backtest-mode">Mode:</label><select id="backtest-mode"><option value="futures">Futures</option><option value="spot" selected>Spot</option></select></div>
                    <div><label for="backtest-leverage">Lev:</label><input type="number" id="backtest-leverage" value="10" min="1"></div>
                    <div><label for="backtest-trigger">Trigger %:</label><input type="number" id="backtest-trigger" value="4.0" step="0.1" min="0"></div>
                    <div><label for="backtest-dca-amount">DCA Amt:</label><input type="number" id="backtest-dca-amount" value="0" step="10"></div>
                </div>
                <div id="run-backtest-btn-container">
                    <button id="run-backtest-btn">Run Backtest</button>
                    <div id="backtest-status">Ready</div>
                </div>
            </div>
            <div id="backtest-results">
                <div id="equitychartdiv"></div>
                <div id="backtest-stats"></div>
                <div id="backtest-trades-table-container">
                    <table id="backtest-trades-table">
                        <thead><tr><th>Exit Time</th><th>Side</th><th>PnL</th><th>Return %</th><th>Reason</th></tr></thead>
                        <tbody></tbody>
                    </table>
                </div>
            </div>
        </div>
    </div>
<script>
document.addEventListener('DOMContentLoaded', function () {
    let equityRoot;
    let backtestRunning = false;
    
    async function runBacktest() {
        if (backtestRunning) return;
        backtestRunning = true;
        const statusEl = document.getElementById('backtest-status');
        const resultsEl = document.getElementById('backtest-results');
        const buttonEl = document.getElementById('run-backtest-btn');
        statusEl.textContent = 'Fetching historical data...';
        buttonEl.disabled = true;
        buttonEl.textContent = 'Running...';
        resultsEl.style.display = 'none';

        const payload = {
            symbol: document.getElementById('backtest-symbol').value.toUpperCase(),
            interval: document.getElementById('backtest-interval').value,
            start_date: document.getElementById('backtest-start').value,
            end_date: document.getElementById('backtest-end').value,
            equity: parseFloat(document.getElementById('backtest-equity').value),
            leverage: parseInt(document.getElementById('backtest-leverage').value),
            risk_percentage: parseFloat(document.getElementById('backtest-risk').value),
            trigger_percentage: parseFloat(document.getElementById('backtest-trigger').value),
            dca_frequency: document.getElementById('backtest-dca-freq').value,
            dca_amount: parseFloat(document.getElementById('backtest-dca-amount').value),
            mode: document.getElementById('backtest-mode').value
        };
        try {
            statusEl.textContent = 'Running simulation... (this may take a moment)';
            const response = await fetch('/api/backtest', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload) });
            if (!response.ok) {
                const errorData = await response.json();
                throw new Error(errorData.error || 'Unknown server error');
            }
            const results = await response.json();
            displayBacktestResults(results);
            statusEl.textContent = 'Backtest complete.';
        } catch (error) {
            statusEl.textContent = `Error: ${error.message}`;
            console.error(error);
        } finally {
            backtestRunning = false;
            buttonEl.disabled = false;
            buttonEl.textContent = 'Run Backtest';
        }
    }
    
    function displayBacktestResults(results) {
        document.getElementById('backtest-results').style.display = 'block';
        const stats = results.metrics;
        const statsEl = document.getElementById('backtest-stats');
        statsEl.innerHTML = `<div>Net Profit: <span style="color:${stats.net_profit > 0 ? '#28a745' : '#dc3545'}">${stats.net_profit.toFixed(2)} USDT</span></div><div>Win Rate: <span>${stats.win_rate.toFixed(2)}%</span></div><div>Profit Factor: <span>${stats.profit_factor.toFixed(2)}</span></div><div>Total Trades: <span>${stats.total_trades}</span></div><div>Avg Trade PnL: <span>${stats.avg_trade_pnl.toFixed(2)}</span></div><div>Max Drawdown: <span style="color:#dc3545">${stats.max_drawdown.toFixed(2)}%</span></div>`;
        const tradesTableBody = document.querySelector('#backtest-trades-table tbody');
        tradesTableBody.innerHTML = '';
        results.trades.forEach(trade => {
            const pnlColor = trade.pnl > 0 ? '#28a745' : '#dc3545';
            const row = `<tr><td>${new Date(trade.exit_time).toLocaleString()}</td><td>${trade.direction}</td><td style="color:${pnlColor}">${trade.pnl.toFixed(2)}</td><td style="color:${pnlColor}">${trade.return_pct.toFixed(2)}%</td><td>${trade.exit_reason || 'N/A'}</td></tr>`;
            tradesTableBody.insertAdjacentHTML('afterbegin', row);
        });
        createEquityChart(results.equity_curve);
    }

    function createEquityChart(data) {
        if (equityRoot) equityRoot.dispose();
        equityRoot = am5.Root.new("equitychartdiv");
        equityRoot.setThemes([am5themes_Dark.new(equityRoot)]);
        let chart = equityRoot.container.children.push(am5xy.XYChart.new(equityRoot, { panX: true, wheelX: "zoomX", pinchZoomX: true, paddingLeft: 0, paddingRight: 0 }));
        let xAxis = chart.xAxes.push(am5xy.DateAxis.new(equityRoot, { baseInterval: { timeUnit: "day", count: 1 }, renderer: am5xy.AxisRendererX.new(equityRoot, { minGridDistance: 50 }), }));
        let yAxis = chart.yAxes.push(am5xy.ValueAxis.new(equityRoot, { renderer: am5xy.AxisRendererY.new(equityRoot, {}) }));
        let series = chart.series.push(am5xy.LineSeries.new(equityRoot, { name: "Equity", xAxis: xAxis, yAxis: yAxis, valueYField: "equity", valueXField: "time", stroke: am5.color(0x00aaff), fill: am5.color(0x00aaff), }));
        series.fills.template.setAll({ fillOpacity: 0.1, visible: true });
        series.data.setAll(data);
    }
    
    function initialize() {
        const today = new Date();
        const yesterday = new Date(today);
        yesterday.setDate(yesterday.getDate() - 1);
        const threeMonthsAgo = new Date(today);
        threeMonthsAgo.setMonth(threeMonthsAgo.getMonth() - 3);
        document.getElementById('backtest-end').valueAsDate = yesterday;
        document.getElementById('backtest-start').valueAsDate = threeMonthsAgo;
        document.getElementById('run-backtest-btn').addEventListener('click', runBacktest);
        
        const modeSelect = document.getElementById('backtest-mode');
        const leverageInput = document.getElementById('backtest-leverage');
        const riskInput = document.getElementById('backtest-risk');
        
        function toggleMode() {
            if (modeSelect.value === 'spot') {
                leverageInput.disabled = true;
                riskInput.previousElementSibling.textContent = 'Inv %:'; // Change label to "Investment %"
            } else {
                leverageInput.disabled = false;
                riskInput.previousElementSibling.textContent = 'Risk %:';
            }
        }
        modeSelect.addEventListener('change', toggleMode);
        toggleMode(); // Call on init
    }
    
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

# --- Data Fetching & Prediction (MODIFIED FOR BINANCE) ---
def get_binance_data(symbol, interval, start_ts=None, end_ts=None, limit=1000):
    """Fetches kline/candlestick data from Binance Futures."""
    binance_interval = BINANCE_INTERVAL_MAP.get(interval, "1h")
    params = {"symbol": symbol, "interval": binance_interval, "limit": min(limit, 1500)}
    if start_ts: params['startTime'] = int(start_ts)
    if end_ts: params['endTime'] = int(end_ts)
    try:
        response = session.get(f"{BINANCE_API_URL}/fapi/v1/klines", params=params, timeout=(5, 10))
        response.raise_for_status()
        # Binance returns a list of lists: [open_time, open, high, low, close, volume, ...]
        return response.json()
    except (requests.exceptions.RequestException, ValueError) as e:
        app.logger.warning(f"Binance kline API error for {symbol}: {e}")
        raise ConnectionError(f"Failed to fetch Binance kline data for {symbol} after retries.")

def get_binance_ticker_data(symbols):
    """Fetches the latest price for a list of symbols from Binance Futures."""
    if not isinstance(symbols, list): symbols = [symbols]
    if not symbols: return {}
    prices = {}
    for symbol in symbols:
        params = {"symbol": symbol}
        try:
            response = session.get(f"{BINANCE_API_URL}/fapi/v1/ticker/price", params=params, timeout=(5, 10))
            response.raise_for_status()
            data = response.json()
            if 'price' in data:
                prices[symbol] = float(data['price'])
        except requests.exceptions.RequestException as e:
            app.logger.error(f"Binance ticker API error for {symbol} after retries: {e}")
    return prices

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

# --- Live Trading Bot Worker ---
def trade_bot_worker():
    app.logger.info("Trading bot worker thread started.")
    ticker_check_interval = 5
    analysis_interval = 60
    last_analysis_time = 0

    while True:
        time.sleep(1)
        try:
            with trade_list_lock: trade_list_copy = list(TRADE_LIST)
            if not trade_list_copy:
                time.sleep(5)
                continue

            with settings_lock:
                client = BingXClient(SETTINGS['bingx_api_key'], SETTINGS['bingx_secret_key'], SETTINGS['mode'] == 'demo')
                risk_percentage = SETTINGS.get('risk_percentage', 1.0)
                leverage = SETTINGS['leverage']
                trigger_percentage = SETTINGS.get('trigger_percentage', 4.0)
            
            with positions_lock:
                active_positions_copy = dict(ACTIVE_POSITIONS)
                symbols_to_fetch = list(set(pos['symbol'] for pos in active_positions_copy.values()))
            
            if symbols_to_fetch:
                current_prices = get_binance_ticker_data(symbols_to_fetch)
                if current_prices:
                    for item_id, position in active_positions_copy.items():
                        symbol = position['symbol']
                        if symbol in current_prices:
                            current_price = current_prices[symbol]
                            direction, tp, sl = position['direction'], position.get('tp_price'), position.get('sl_price')
                            
                            close_position, close_reason = False, ""
                            if direction == 'long':
                                if sl and current_price <= sl: close_position, close_reason = True, "SL"
                                elif tp and current_price >= tp: close_position, close_reason = True, "TP"
                            elif direction == 'short':
                                if sl and current_price >= sl: close_position, close_reason = True, "SL"
                                elif tp and current_price <= tp: close_position, close_reason = True, "TP"
                            
                            if close_position:
                                app.logger.info(f"[AUTO-CLOSE] {close_reason} hit for {symbol}. Closing {direction} position.")
                                position_side = direction.upper(); order_side = "SELL" if direction == 'long' else "BUY"
                                res = client.place_order(symbol, order_side, position_side, position['quantity'], leverage)
                                if res and res.get('code') == 0:
                                    app.logger.info(f"Successfully closed {symbol} position due to {close_reason}.")
                                    with positions_lock, status_lock:
                                        if item_id in ACTIVE_POSITIONS: del ACTIVE_POSITIONS[item_id]
                                        BOT_STATUS[item_id] = {"message": "Waiting...", "color": "#fff", "last_close_time": time.time()}
                                else:
                                    app.logger.error(f"Failed to close {symbol} on {close_reason}: {res.get('msg') if res else 'Unknown error'}")
                                continue

            if time.time() - last_analysis_time < analysis_interval:
                time.sleep(ticker_check_interval)
                continue
            
            last_analysis_time = time.time()
            balance_response = client.get_balance()
            total_balance = 0
            if balance_response and balance_response.get('code') == 0:
                total_balance = float(balance_response['data']['balance']['balance'])
            else:
                app.logger.error("Could not fetch account balance. Skipping trade cycle.")
                time.sleep(10)
                continue
            
            risk_usdt = total_balance * (risk_percentage / 100)

            for item in trade_list_copy:
                try:
                    item_id, symbol, interval = item['id'], item['symbol'], item['interval']
                    with positions_lock: position_data = ACTIVE_POSITIONS.get(item_id)
                    with status_lock: last_close_time = BOT_STATUS.get(item_id, {}).get('last_close_time', 0)
                    
                    raw_candles = get_binance_data(symbol, interval, limit=50)
                    if len(raw_candles) < 50: continue
                    current_price = float(raw_candles[-1][4])

                    predicted_candles = predict_next_candles(raw_candles)
                    if not predicted_candles: continue
                    final_predicted_price = predicted_candles[-1]['c']
                    price_change_pct = ((final_predicted_price - current_price) / current_price) * 100

                    if position_data:
                        direction = position_data['direction']
                        is_long = direction == 'long'
                        signal_reversed = (is_long and price_change_pct < -0.5) or (not is_long and price_change_pct > 0.5)
                        
                        if signal_reversed:
                            position_side, order_side = direction.upper(), "SELL" if is_long else "BUY"
                            res = client.place_order(symbol, order_side, position_side, position_data['quantity'], leverage)
                            if res and res.get('code') == 0:
                                with positions_lock, status_lock:
                                    if item_id in ACTIVE_POSITIONS: del ACTIVE_POSITIONS[item_id]
                                    BOT_STATUS[item_id] = {"message": "Waiting...", "color": "#fff", "last_close_time": time.time()}

                    elif time.time() - last_close_time > TRADE_COOLDOWN_SECONDS:
                        if abs(price_change_pct) > trigger_percentage:
                            direction = "long" if price_change_pct > 0 else "short"
                            tp_price = current_price * (1 + (price_change_pct * 0.8 / 100))
                            sl_price = current_price * (1 - (price_change_pct * 0.4 / 100)) if direction == "long" else current_price * (1 + (abs(price_change_pct) * 0.4 / 100))
                            
                            liquidation_price = calculate_liquidation_price(current_price, leverage, direction)
                            if liquidation_price is not None:
                                if direction == "long" and sl_price <= liquidation_price:
                                    sl_price = liquidation_price * 1.001
                                elif direction == "short" and sl_price >= liquidation_price:
                                    sl_price = liquidation_price * 0.999

                            if abs(current_price - sl_price) > 0:
                                quantity = risk_usdt / abs(current_price - sl_price)
                                position_side, order_side = direction.upper(), "BUY" if direction == 'long' else "SELL"
                                res = client.place_order(symbol, order_side, position_side, quantity, leverage)
                                if res and res.get('code') == 0:
                                    with positions_lock:
                                        ACTIVE_POSITIONS[item_id] = {'symbol': symbol, 'quantity': quantity, 'direction': direction, 'entry_price': current_price, 'tp_price': tp_price, 'sl_price': sl_price}
                except Exception as e:
                    app.logger.error(f"Error in analysis for {item.get('symbol', 'N/A')}: {e}", exc_info=False)
                time.sleep(1)
        except Exception as e:
            app.logger.error(f"FATAL ERROR in main trade_bot_worker loop: {e}", exc_info=True)
            time.sleep(10)

def pnl_updater_worker():
    app.logger.info("PnL updater thread started.")
    while True:
        time.sleep(2)
        try:
            with positions_lock:
                if not ACTIVE_POSITIONS: continue
                active_positions_copy = dict(ACTIVE_POSITIONS)
                symbols_to_fetch = list(set(pos['symbol'] for pos in active_positions_copy.values()))
            
            if not symbols_to_fetch: continue
            ticker_prices = get_binance_ticker_data(symbols_to_fetch)
            if not ticker_prices: continue

            with settings_lock: leverage = SETTINGS.get('leverage', 10)
            
            for item_id, position in active_positions_copy.items():
                symbol = position['symbol']
                if symbol in ticker_prices:
                    current_price = ticker_prices[symbol]
                    entry_price, quantity, direction = position['entry_price'], position['quantity'], position['direction']
                    pnl = (current_price - entry_price) * quantity if direction == 'long' else (entry_price - current_price) * quantity
                    initial_margin = (entry_price * quantity) / leverage
                    pnl_pct = (pnl / initial_margin) * 100 if initial_margin > 0 else 0
                    with status_lock:
                        BOT_STATUS[item_id] = { "message": f"In {direction.upper()}", "color": "#28a745" if pnl >= 0 else "#dc3545", "pnl": pnl, "pnl_pct": pnl_pct }
        except Exception as e: 
            app.logger.error(f"Error in PnL updater worker: {e}", exc_info=False)


# --- Backtesting Engine (MODIFIED to include Reversal Detection and Correct Spot Logic) ---
def get_sub_interval(interval):
    """Determines a smaller interval for detailed backtest simulation."""
    if not interval.isnumeric():
        return "60"
    interval_min = int(interval)
    if interval_min >= 240: return "30"
    if interval_min >= 60: return "5"
    if interval_min >= 15: return "1"
    return "1"

def run_backtest_simulation(symbol, interval, start_ts, end_ts, leverage, starting_equity, risk_percentage, trigger_percentage, dca_frequency, dca_amount, mode):
    # 1. Fetch Data
    app.logger.info(f"Backtest: Fetching primary {interval} candles from Binance...")
    all_candles_raw = []
    current_start_ts = start_ts
    while current_start_ts < end_ts:
        chunk = get_binance_data(symbol, interval, start_ts=current_start_ts, end_ts=end_ts)
        if not chunk: break
        all_candles_raw.extend(c for c in chunk if int(c[0]) >= current_start_ts and int(c[0]) < end_ts)
        last_ts = int(chunk[-1][0])
        if len(chunk) < 1000 or last_ts >= end_ts: break
        current_start_ts = last_ts + 1
    
    if len(all_candles_raw) < 50: raise ValueError("Not enough historical data for the main interval.")
    
    sub_interval = get_sub_interval(interval)
    app.logger.info(f"Backtest: Fetching sub-interval {sub_interval} candles from Binance...")
    all_sub_candles_raw = []
    current_start_ts = start_ts
    while current_start_ts < end_ts:
        chunk = get_binance_data(symbol, sub_interval, start_ts=current_start_ts, end_ts=end_ts)
        if not chunk: break
        all_sub_candles_raw.extend(c for c in chunk if int(c[0]) >= current_start_ts and int(c[0]) < end_ts)
        last_ts = int(chunk[-1][0])
        if len(chunk) < 1000 or last_ts >= end_ts: break
        current_start_ts = last_ts + 1

    if not all_sub_candles_raw: raise ValueError("Not enough historical data for the sub-interval.")

    # 2. Pre-computation and Initialization
    all_candles = [{'t': int(c[0]), 'o': float(c[1]), 'h': float(c[2]), 'l': float(c[3]), 'c': float(c[4])} for c in all_candles_raw]
    sub_candles = [{'t': int(c[0]), 'o': float(c[1]), 'h': float(c[2]), 'l': float(c[3]), 'c': float(c[4])} for c in all_sub_candles_raw]
    
    main_candle_open_timestamps = {c['t'] for c in all_candles}
    ts_to_main_candle_idx = {c['t']: i for i, c in enumerate(all_candles)}
    
    trades, equity_curve = [], [{'time': start_ts, 'equity': starting_equity}]
    total_dca_added = 0.0
    last_dca_time = start_ts
    WEEK_MS = 7 * 24 * 60 * 60 * 1000

    app.logger.info(f"Backtest: Starting simulation on {len(sub_candles)} sub-candles in {mode.upper()} mode...")

    # 3. Simulation Loop (Mode-dependent)
    # ===================================
    # --- FUTURES MODE SIMULATION ---
    # ===================================
    if mode == 'futures':
        equity = starting_equity
        open_position = None
        for sub_candle in sub_candles:
            current_ts = sub_candle['t']
            
            if dca_frequency != 'none' and dca_amount > 0:
                perform_dca = False
                if dca_frequency == 'weekly' and current_ts - last_dca_time >= WEEK_MS: perform_dca = True
                elif dca_frequency == 'monthly':
                    if datetime.fromtimestamp(current_ts/1000).month != datetime.fromtimestamp(last_dca_time/1000).month: perform_dca = True
                if perform_dca: equity += dca_amount; total_dca_added += dca_amount; last_dca_time = current_ts

            if open_position:
                exit_price, exit_reason = None, None
                if open_position['direction'] == 'long':
                    if sub_candle['l'] <= open_position['sl']: exit_price, exit_reason = open_position['sl'], 'SL'
                    elif sub_candle['h'] >= open_position['tp']: exit_price, exit_reason = open_position['tp'], 'TP'
                else: # Short
                    if sub_candle['h'] >= open_position['sl']: exit_price, exit_reason = open_position['sl'], 'SL'
                    elif sub_candle['l'] <= open_position['tp']: exit_price, exit_reason = open_position['tp'], 'TP'
                
                if exit_price:
                    pnl = (exit_price - open_position['entry_price']) * open_position['quantity'] if open_position['direction'] == 'long' else (open_position['entry_price'] - exit_price) * open_position['quantity']
                    initial_margin = (open_position['entry_price'] * open_position['quantity']) / leverage
                    return_pct = (pnl / initial_margin) * 100 if initial_margin > 0 else 0
                    equity += pnl
                    trades.append({'exit_time': current_ts, 'direction': open_position['direction'], 'pnl': pnl, 'return_pct': return_pct, 'exit_reason': exit_reason})
                    open_position = None
            
            if current_ts in main_candle_open_timestamps:
                current_main_idx = ts_to_main_candle_idx.get(current_ts)
                if current_main_idx and current_main_idx >= 50:
                    history_slice = all_candles_raw[current_main_idx - 50 : current_main_idx]
                    predicted = predict_next_candles(history_slice, 20)
                    
                    if predicted:
                        signal_price = float(history_slice[-1][4])
                        change = ((predicted[-1]['c'] - signal_price) / signal_price) * 100

                        if open_position:
                            is_long = open_position['direction'] == 'long'
                            signal_reversed = (is_long and change < -0.5) or (not is_long and change > 0.5)
                            if signal_reversed:
                                exit_price = sub_candle['o']
                                pnl = (exit_price - open_position['entry_price']) * open_position['quantity'] if is_long else (open_position['entry_price'] - exit_price) * open_position['quantity']
                                initial_margin = (open_position['entry_price'] * open_position['quantity']) / leverage
                                return_pct = (pnl / initial_margin) * 100 if initial_margin > 0 else 0
                                equity += pnl
                                trades.append({'exit_time': current_ts, 'direction': open_position['direction'], 'pnl': pnl, 'return_pct': return_pct, 'exit_reason': 'Reversal'})
                                open_position = None
                        
                        elif not open_position and abs(change) > trigger_percentage:
                            entry_price = sub_candle['o']
                            direction = "long" if change > 0 else "short"
                            tp = entry_price * (1 + (change * 0.8 / 100))
                            sl = entry_price * (1 - (change * 0.4 / 100)) if direction == "long" else entry_price * (1 + (abs(change) * 0.4 / 100))
                            
                            liquidation_price = calculate_liquidation_price(entry_price, leverage, direction)
                            if liquidation_price is not None:
                                if direction == "long" and sl <= liquidation_price: sl = liquidation_price * 1.001
                                elif direction == "short" and sl >= liquidation_price: sl = liquidation_price * 0.999
                            
                            if abs(entry_price - sl) > 0 and equity > 0:
                                risk_amount = equity * (risk_percentage / 100)
                                quantity = risk_amount / abs(entry_price - sl)
                                open_position = {'entry_price': entry_price, 'quantity': quantity, 'direction': direction, 'tp': tp, 'sl': sl}

            if current_ts in main_candle_open_timestamps: equity_curve.append({'time': current_ts, 'equity': equity})
        final_equity = equity

    # ===================================
    # --- SPOT MODE SIMULATION (CORRECTED LOGIC) ---
    # ===================================
    elif mode == 'spot':
        cash = starting_equity
        open_trade = None 
        last_trade_exit_time = 0

        for sub_candle in sub_candles:
            current_ts = sub_candle['t']
            
            if dca_frequency != 'none' and dca_amount > 0:
                perform_dca = False
                if dca_frequency == 'weekly' and current_ts - last_dca_time >= WEEK_MS: perform_dca = True
                elif dca_frequency == 'monthly':
                    if datetime.fromtimestamp(current_ts/1000).month != datetime.fromtimestamp(last_dca_time/1000).month: perform_dca = True
                if perform_dca: cash += dca_amount; total_dca_added += dca_amount; last_dca_time = current_ts

            if open_trade:
                exit_price, exit_reason = None, None
                if sub_candle['l'] <= open_trade['sl']:
                    exit_price, exit_reason = open_trade['sl'], 'SL'
                elif sub_candle['h'] >= open_trade['tp']:
                    exit_price, exit_reason = open_trade['tp'], 'TP'

                if exit_price:
                    pnl = (exit_price - open_trade['entry_price']) * open_trade['quantity']
                    cash += exit_price * open_trade['quantity']
                    trades.append({
                        'exit_time': current_ts, 'direction': 'BUY', 'pnl': pnl,
                        'return_pct': (pnl / open_trade['initial_cost']) * 100 if open_trade['initial_cost'] > 0 else 0,
                        'exit_reason': exit_reason
                    })
                    open_trade = None
                    last_trade_exit_time = current_ts

            if current_ts in main_candle_open_timestamps:
                current_main_idx = ts_to_main_candle_idx.get(current_ts)
                if current_main_idx and current_main_idx >= 50:
                    history_slice = all_candles_raw[current_main_idx - 50 : current_main_idx]
                    predicted = predict_next_candles(history_slice, 20)
                    
                    if predicted:
                        signal_price = float(history_slice[-1][4])
                        change = ((predicted[-1]['c'] - signal_price) / signal_price) * 100
                        
                        if open_trade and change < -0.5:
                            exit_price = sub_candle['o']
                            pnl = (exit_price - open_trade['entry_price']) * open_trade['quantity']
                            cash += exit_price * open_trade['quantity']
                            trades.append({
                                'exit_time': current_ts, 'direction': 'BUY', 'pnl': pnl,
                                'return_pct': (pnl / open_trade['initial_cost']) * 100 if open_trade['initial_cost'] > 0 else 0,
                                'exit_reason': 'Reversal'
                            })
                            open_trade = None
                            last_trade_exit_time = current_ts
                        
                        elif not open_trade and (current_ts - last_trade_exit_time > TRADE_COOLDOWN_SECONDS * 1000):
                            if change > trigger_percentage:
                                entry_price = sub_candle['o']
                                usdt_to_risk = cash * (risk_percentage / 100)
                                if usdt_to_risk > cash: usdt_to_risk = cash
                                
                                if usdt_to_risk > 10:
                                    quantity = usdt_to_risk / entry_price
                                    cash -= usdt_to_risk
                                    
                                    tp = entry_price * (1 + (change * 0.8 / 100))
                                    sl = entry_price * (1 - (change * 0.4 / 100))
                                    
                                    open_trade = {
                                        'entry_price': entry_price, 'quantity': quantity,
                                        'initial_cost': usdt_to_risk, 'tp': tp, 'sl': sl
                                    }

            if current_ts in main_candle_open_timestamps:
                current_price = sub_candle['c']
                holdings_value = (open_trade['quantity'] * current_price) if open_trade else 0
                total_equity = cash + holdings_value
                equity_curve.append({'time': current_ts, 'equity': total_equity})

        final_price = sub_candles[-1]['c']
        holdings_value_at_end = (open_trade['quantity'] * final_price) if open_trade else 0
        final_equity = cash + holdings_value_at_end


    # 4. Calculate Final Metrics
    net_profit = final_equity - starting_equity - total_dca_added
    total_trades = len(trades)
    win_rate = (len([t for t in trades if t['pnl'] > 0]) / total_trades * 100) if total_trades > 0 else 0
    total_profit = sum(t['pnl'] for t in trades if t['pnl'] > 0)
    total_loss = abs(sum(t['pnl'] for t in trades if t['pnl'] <= 0))
    profit_factor = total_profit / total_loss if total_loss > 0 else 0
    max_dd, peak = 0, starting_equity
    for item in equity_curve:
        if item['equity'] > peak: peak = item['equity']
        dd = (peak - item['equity']) / peak if peak != 0 else 0
        max_dd = max(max_dd, dd)
    return {
        "metrics": {
            "net_profit": net_profit, 
            "total_trades": total_trades, 
            "win_rate": win_rate, 
            "profit_factor": profit_factor, 
            "max_drawdown": max_dd * 100, 
            "avg_trade_pnl": (net_profit / total_trades) if total_trades > 0 else 0
        }, 
        "trades": trades, 
        "equity_curve": equity_curve
    }


# --- Flask Routes ---
@app.route('/')
def index(): return render_template_string(HTML_TEMPLATE)

@app.route('/api/candles')
def api_candles():
    symbol, interval, num_predictions = request.args.get('symbol', 'BTCUSDT').upper(), request.args.get('interval', '60'), max(1, min(request.args.get('predictions', 20, type=int), 50))
    if interval not in ALLOWED_INTERVALS: return jsonify({"error": "Invalid interval"}), 400
    try:
        raw_candles = get_binance_data(symbol, interval)[-500:]
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

        price_data = get_binance_ticker_data(symbol)
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
        start_ts = int(datetime.strptime(data['start_date'], '%Y-%m-%d').timestamp() * 1000)
        end_ts = int(datetime.strptime(data['end_date'], '%Y-%m-%d').timestamp() * 1000)
        
        mode = data.get('mode', 'futures')
        leverage = data.get('leverage', 10)
        starting_equity = data.get('equity', 10000.0)
        risk_percentage = data.get('risk_percentage', 1.0)
        trigger_percentage = data.get('trigger_percentage', 4.0)
        dca_frequency = data.get('dca_frequency', 'none')
        dca_amount = data.get('dca_amount', 0.0)

        results = run_backtest_simulation(
            data['symbol'], data['interval'], start_ts, end_ts,
            leverage, starting_equity, risk_percentage, trigger_percentage,
            dca_frequency, dca_amount, mode
        )
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
