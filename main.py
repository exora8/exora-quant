# ==============================================================================
# Candlestick Chart with AI Prediction (Flask, amCharts5, numpy)
# ==============================================================================
# This single-file Python project runs a Flask web server to display a
# candlestick chart with AI-predicted future candles.
#
# Core Libraries:
# - Flask: Web server framework.
# - requests: To fetch data from the Bybit API.
# - numpy: For all numerical operations and machine learning models.
#
# Prohibited Libraries (as per requirements):
# - pandas: Not used for data manipulation.
# - scikit-learn: Not used for machine learning models.
#
# How to Run:
# 1. Install dependencies:
#    pip install Flask requests numpy
#
# 2. Run the Flask development server:
#    python app.py
#
# 3. Access the application in your browser:
#    http://127.0.0.1:5000
#
# For Production (Optional):
#   gunicorn --workers 4 --bind 0.0.0.0:5000 app:app
# ==============================================================================

import time
import requests
import numpy as np
from flask import Flask, jsonify, render_template_string, request

# --- Configuration ---
# Whitelist of allowed intervals to prevent abuse.
ALLOWED_INTERVALS = ["1", "3", "5", "15", "30", "60", "120", "240", "360", "720", "D", "W", "M"]
BYBIT_API_URL = "https://api.bybit.com/v5/market/kline"
CACHE_TTL_SECONDS = 15  # Cache API responses for 15 seconds

# --- Flask App Initialization ---
app = Flask(__name__)
# Simple in-memory cache: {cache_key: (timestamp, data)}
cache = {}


# --- HTML & JavaScript Template ---
# This single string contains the entire frontend code, including HTML, CSS,
# and JavaScript for fetching data and rendering the amCharts5 chart.
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>AI Candlestick Chart</title>
    <style>
        html, body {
            width: 100%; height: 100%; margin: 0; padding: 0;
            overflow: hidden; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            background-color: #000;
        }
        #chartdiv { width: 100%; height: 100%; }
        .controls-overlay {
            position: absolute; top: 15px; left: 15px; z-index: 100;
            background-color: rgba(25, 25, 25, 0.85); backdrop-filter: blur(5px);
            padding: 12px; border-radius: 8px; border: 1px solid #333;
            display: flex; align-items: center; gap: 12px; box-shadow: 0 4px 15px rgba(0,0,0,0.5);
        }
        .controls-overlay label { color: #ccc; font-size: 14px; }
        .controls-overlay select, .controls-overlay input, .controls-overlay button {
            padding: 8px 12px; border-radius: 5px; border: 1px solid #444;
            background-color: #2a2a2a; color: #eee; font-size: 14px; cursor: pointer;
            transition: background-color 0.2s, border-color 0.2s;
        }
        .controls-overlay input[type='text'] { width: 100px; cursor: text; }
        .controls-overlay input[type='number'] { width: 50px; }
        .controls-overlay select:hover, .controls-overlay input:hover, .controls-overlay button:hover {
            background-color: #333; border-color: #555;
        }
        .controls-overlay button { background-color: #007bff; border-color: #007bff; font-weight: bold; }
        .controls-overlay button:hover { background-color: #0056b3; border-color: #0056b3; }
        #status { margin-left: 15px; color: #ffeb3b; font-size: 14px; min-width: 250px; }

        /* --- NEW: Position Simulation Modal --- */
        #position-modal {
            display: none; position: absolute; top: 50%; left: 50%;
            transform: translate(-50%, -50%); z-index: 200;
            background-color: #1a1a1a; border: 1px solid #444;
            border-radius: 8px; padding: 20px; box-shadow: 0 5px 25px rgba(0,0,0,0.7);
            color: #eee;
        }
        #position-modal.visible { display: block; }
        .modal-content { display: grid; grid-template-columns: 120px 1fr; gap: 10px 15px; align-items: center; }
        .modal-content h3 { grid-column: 1 / -1; margin: 0 0 10px; text-align: center; }
        .modal-content input[type="number"], .modal-content .radio-group { width: 100%; box-sizing: border-box; }
        .modal-buttons { grid-column: 1 / -1; display: flex; justify-content: space-between; margin-top: 15px; }
    </style>
    <!-- amCharts 5 CDN -->
    <script src="https://cdn.amcharts.com/lib/5/index.js"></script>
    <script src="https://cdn.amcharts.com/lib/5/xy.js"></script>
    <script src="https://cdn.amcharts.com/lib/5/themes/Animated.js"></script>
    <script src="https://cdn.amcharts.com/lib/5/themes/Dark.js"></script>
</head>
<body>
    <div id="chartdiv"></div>

    <div class="controls-overlay">
        <label for="symbol">Symbol:</label>
        <input type="text" id="symbol" value="BTCUSDT" placeholder="e.g., BTCUSDT">
        <label for="interval">Timeframe:</label>
        <select id="interval">
            <option value="1">1 minute</option> <option value="5">5 minutes</option>
            <option value="15" selected>15 minutes</option> <option value="30">30 minutes</option>
            <option value="60">1 hour</option> <option value="240">4 hours</option>
            <option value="D">Daily</option> <option value="W">Weekly</option>
        </select>
        <label for="num_predictions">Predictions:</label>
        <input type="number" id="num_predictions" value="5" min="1" max="20">
        <button id="fetchButton">Fetch & Predict</button>
        <div id="status"></div>
    </div>

    <!-- --- NEW: Position Simulation Modal HTML --- -->
    <div id="position-modal">
        <div class="modal-content">
            <h3>Simulate Position</h3>
            <label>Direction:</label>
            <div class="radio-group">
                <input type="radio" id="pos-long" name="direction" value="long" checked><label for="pos-long"> Long</label>
                <input type="radio" id="pos-short" name="direction" value="short" style="margin-left: 10px;"><label for="pos-short"> Short</label>
            </div>
            <label for="entry-price">Entry Price:</label>
            <input type="number" id="entry-price" step="0.01">
            <label for="tp-percent">Take Profit (%):</label>
            <input type="number" id="tp-percent" value="3">
            <label for="sl-percent">Stop Loss (%):</label>
            <input type="number" id="sl-percent" value="1.5">
            <div class="modal-buttons">
                <button id="set-position-btn">Set Position</button>
                <button id="cancel-position-btn">Cancel</button>
            </div>
        </div>
    </div>

    <script>
        document.addEventListener('DOMContentLoaded', function () {
            const statusEl = document.getElementById('status');
            const fetchButton = document.getElementById('fetchButton');
            const symbolInput = document.getElementById('symbol');
            
            // --- NEW: Modal elements and state ---
            const positionModal = document.getElementById('position-modal');
            const entryPriceInput = document.getElementById('entry-price');
            let selectedCandleTimestamp = null;
            let positionRanges = [];

            let root, chart, xAxis, yAxis, series, predictedSeries;

            function createChart() {
                if (root) root.dispose();
                root = am5.Root.new("chartdiv");
                root.setThemes([am5themes_Animated.new(root), am5themes_Dark.new(root)]);

                chart = root.container.children.push(am5xy.XYChart.new(root, {
                    panX: true, panY: false, wheelX: "panX", wheelY: "zoomX", pinchZoomX: true
                }));
                
                const cursor = chart.set("cursor", am5xy.XYCursor.new(root, { behavior: "zoomX" }));
                cursor.lineY.set("visible", false);
                
                xAxis = chart.xAxes.push(am5xy.DateAxis.new(root, {
                    baseInterval: { timeUnit: "minute", count: 1 },
                    renderer: am5xy.AxisRendererX.new(root, { minGridDistance: 70 }),
                    tooltip: am5.Tooltip.new(root, {})
                }));

                yAxis = chart.yAxes.push(am5xy.ValueAxis.new(root, {
                    renderer: am5xy.AxisRendererY.new(root, {}),
                    tooltip: am5.Tooltip.new(root, {})
                }));
                
                series = chart.series.push(am5xy.CandlestickSeries.new(root, {
                    name: "Historical", xAxis: xAxis, yAxis: yAxis,
                    valueXField: "t", openValueYField: "o", highValueYField: "h", lowValueYField: "l", valueYField: "c",
                    tooltip: am5.Tooltip.new(root, { labelText: "Source: Real\\nOpen: {openValueY}\\nHigh: {highValueY}\\nLow: {lowValueY}\\nClose: {valueY}" })
                }));

                // --- NEW: Add click event to candles to open the simulation modal ---
                series.columns.template.events.on("click", function(ev) {
                    const dataItem = ev.target.dataItem;
                    if (dataItem) {
                        selectedCandleTimestamp = dataItem.get("valueX");
                        entryPriceInput.value = dataItem.get("valueY"); // Pre-fill with close price
                        positionModal.classList.add("visible");
                    }
                });
                
                predictedSeries = chart.series.push(am5xy.CandlestickSeries.new(root, {
                    name: "Predicted", xAxis: xAxis, yAxis: yAxis,
                    valueXField: "t", openValueYField: "o", highValueYField: "h", lowValueYField: "l", valueYField: "c",
                    tooltip: am5.Tooltip.new(root, { labelText: "Source: AI Prediction\\nOpen: {openValueY}\\nHigh: {highValueY}\\nLow: {lowValueY}\\nClose: {valueY}" })
                }));
                predictedSeries.columns.template.setAll({ fill: am5.color(0xaaaaaa), stroke: am5.color(0xaaaaaa) });

                chart.set("scrollbarX", am5.Scrollbar.new(root, { orientation: "horizontal" }));
                chart.appear(1000, 100);
            }
            
            // --- NEW: Function to draw the position lines on the chart ---
            function drawPositionOnChart(entryPrice, tpPrice, slPrice, direction, startTime) {
                // Clear any previous position lines
                positionRanges.forEach(range => range.dispose());
                positionRanges = [];

                // Create Entry line
                const entryRange = yAxis.createAxisRange(yAxis.makeDataItem({ value: entryPrice }));
                entryRange.get("grid").setAll({ stroke: am5.color(0x0099ff), strokeWidth: 2, strokeOpacity: 1, strokeDasharray: [3, 3] });
                entryRange.get("label").setAll({ text: "Entry", fill: am5.color(0x0099ff), location: 0, inside: true, align: "right", dx: 60 });
                positionRanges.push(entryRange);

                // Create TP line
                const tpRange = yAxis.createAxisRange(yAxis.makeDataItem({ value: tpPrice }));
                tpRange.get("grid").setAll({ stroke: am5.color(0x00c782), strokeWidth: 2, strokeOpacity: 1 });
                tpRange.get("label").setAll({ text: "TP", fill: am5.color(0x00c782), location: 0, inside: true, align: "right", dx: 30 });
                positionRanges.push(tpRange);

                // Create SL line
                const slRange = yAxis.createAxisRange(yAxis.makeDataItem({ value: slPrice }));
                slRange.get("grid").setAll({ stroke: am5.color(0xf34a4a), strokeWidth: 2, strokeOpacity: 1 });
                slRange.get("label").setAll({ text: "SL", fill: am5.color(0xf34a4a), location: 0, inside: true, align: "right" });
                positionRanges.push(slRange);

                // Create shaded background for the trade duration
                const backgroundRange = xAxis.createAxisRange(xAxis.makeDataItem({ value: startTime }));
                const fillColor = direction === 'long' ? am5.color(0x00c782) : am5.color(0xf34a4a);
                backgroundRange.get("axisFill").setAll({ fill: fillColor, fillOpacity: 0.1, visible: true });
                positionRanges.push(backgroundRange);
            }

            async function fetchDataAndPredict() {
                const symbol = symbolInput.value.toUpperCase().trim();
                const interval = document.getElementById('interval').value;
                const numPredictions = document.getElementById('num_predictions').value;

                if (!symbol) { statusEl.innerText = 'Error: Symbol cannot be empty.'; return; }
                
                statusEl.innerText = 'Fetching data from Bybit...';
                fetchButton.disabled = true;

                try {
                    const response = await fetch(`/api/candles?symbol=${symbol}&interval=${interval}&predictions=${numPredictions}`);
                    if (!response.ok) throw new Error((await response.json()).error || `HTTP error! status: ${response.status}`);
                    
                    statusEl.innerText = 'Data received. Predicting...';
                    const data = await response.json();
                    
                    const intervalConfig = !isNaN(interval) ? { timeUnit: "minute", count: parseInt(interval) } : { timeUnit: { 'D': 'day', 'W': 'week' }[interval] || 'day', count: 1 };
                    xAxis.set("baseInterval", intervalConfig);
                    series.data.setAll(data.candles);
                    predictedSeries.data.setAll(data.predicted);
                    statusEl.innerText = 'Prediction complete.';
                } catch (error) {
                    console.error('Error:', error);
                    statusEl.innerText = `Error: ${error.message}`;
                    if (series) series.data.setAll([]);
                    if (predictedSeries) predictedSeries.data.setAll([]);
                } finally {
                    fetchButton.disabled = false;
                    setTimeout(() => { statusEl.innerText = ''; }, 5000);
                }
            }
            
            // --- NEW: Event listeners for the position modal buttons ---
            document.getElementById('set-position-btn').addEventListener('click', () => {
                const entryPrice = parseFloat(entryPriceInput.value);
                const tpPercent = parseFloat(document.getElementById('tp-percent').value);
                const slPercent = parseFloat(document.getElementById('sl-percent').value);
                const direction = document.querySelector('input[name="direction"]:checked').value;
                
                if (isNaN(entryPrice) || isNaN(tpPercent) || isNaN(slPercent) || !selectedCandleTimestamp) return;

                let tpPrice, slPrice;
                if (direction === 'long') {
                    tpPrice = entryPrice * (1 + tpPercent / 100);
                    slPrice = entryPrice * (1 - slPercent / 100);
                } else { // short
                    tpPrice = entryPrice * (1 - tpPercent / 100);
                    slPrice = entryPrice * (1 + slPercent / 100);
                }
                
                drawPositionOnChart(entryPrice, tpPrice, slPrice, direction, selectedCandleTimestamp);
                positionModal.classList.remove('visible');
            });

            document.getElementById('cancel-position-btn').addEventListener('click', () => {
                positionModal.classList.remove('visible');
            });
            
            createChart();
            fetchButton.addEventListener('click', fetchDataAndPredict);
            symbolInput.addEventListener('keydown', (event) => { if (event.key === 'Enter') fetchDataAndPredict(); });
            fetchDataAndPredict();
        });
    </script>
</body>
</html>
"""

# --- Data Fetching & Caching ---
def get_bybit_data(symbol, interval):
    """
    Fetches candlestick data from the Bybit v5 API with in-memory caching.
    """
    cache_key = f"{symbol}-{interval}"
    current_time = time.time()

    if cache_key in cache:
        last_fetch_time, cached_data = cache[cache_key]
        if current_time - last_fetch_time < CACHE_TTL_SECONDS:
            return cached_data

    params = { "category": "spot", "symbol": symbol, "interval": interval, "limit": 500 }
    try:
        response = requests.get(BYBIT_API_URL, params=params)
        response.raise_for_status()
        data = response.json()

        if data.get("retCode") != 0: raise ValueError(data.get("retMsg", "Unknown Bybit API error"))

        candles = list(reversed(data["result"]["list"]))
        if not candles: return []
            
        cache[cache_key] = (current_time, candles)
        return candles
    except requests.exceptions.RequestException as e: raise ConnectionError(f"Failed to connect to Bybit API: {e}")
    except (ValueError, KeyError) as e: raise ValueError(f"Error processing Bybit response: {e}")

# --- Prediction Model (numpy-only) ---
def build_features(data):
    """
    Builds a feature matrix and target vector from candlestick data.
    """
    N = data.shape[0]
    if N < 101: return np.array([]), np.array([])

    closes = data[:, 3]
    
    data_prev = data[:-1]
    data_prev[data_prev == 0] = 1e-10
    log_returns = np.log(data[1:] / data_prev)

    ma9 = np.convolve(closes, np.ones(9), 'valid') / 9
    ma50 = np.convolve(closes, np.ones(50), 'valid') / 50
    ma100 = np.convolve(closes, np.ones(100), 'valid') / 100
    
    body = np.abs(data[:, 0] - data[:, 3])
    upper_wick = data[:, 1] - np.maximum(data[:, 0], data[:, 3])
    lower_wick = np.minimum(data[:, 0], data[:, 3]) - data[:, 2]

    num_samples = N - 100
    target = log_returns[99:, 3]

    feat_log_returns = log_returns[98:-1, :4]
    feat_ma100 = ma100[:num_samples]
    feat_ma50 = ma50[50 : 50 + num_samples]
    feat_ma9 = ma9[91 : 91 + num_samples]
    feat_body = body[99 : 99 + num_samples]
    feat_upper_wick = upper_wick[99 : 99 + num_samples]
    feat_lower_wick = lower_wick[99 : 99 + num_samples]
    
    features = np.column_stack([
        feat_log_returns, feat_ma9, feat_ma50, feat_ma100,
        feat_body, feat_upper_wick, feat_lower_wick
    ])
    
    return features, target

def build_single_feature_vec(data):
    """Efficiently builds one feature vector for the most recent candle."""
    N = data.shape[0]
    if N < 100: return None
    closes = data[N-100:, 3]
    
    last_two = data[N-2:N].copy()
    last_two[0, last_two[0] == 0] = 1e-10
    log_ret = np.log(last_two[1] / last_two[0])[:4]
    
    last_candle = data[-1]
    return np.hstack([
        log_ret, np.mean(closes[-9:]), np.mean(closes[-50:]), np.mean(closes),
        np.abs(last_candle[0] - last_candle[3]),
        last_candle[1] - np.maximum(last_candle[0], last_candle[3]),
        np.minimum(last_candle[0], last_candle[3]) - last_candle[2]
    ])

def find_similar_patterns(data, window_size=20, top_n=5):
    """Finds historical patterns similar to the most recent one using cosine similarity."""
    closes = data[:, 3]
    log_returns_close = np.log(closes[1:] / closes[:-1] + 1e-10)
    
    if len(log_returns_close) < 2 * window_size: return None

    current_pattern = log_returns_close[-window_size:]
    current_norm = np.linalg.norm(current_pattern)
    if current_norm == 0: return None

    shape = (len(log_returns_close) - window_size, window_size)
    strides = (log_returns_close.strides[0], log_returns_close.strides[0])
    historical_patterns = np.lib.stride_tricks.as_strided(log_returns_close, shape=shape, strides=strides)
    
    dot_products = np.dot(historical_patterns, current_pattern)
    historical_norms = np.linalg.norm(historical_patterns, axis=1)
    
    mask = historical_norms > 0
    similarities = dot_products[mask] / (current_norm * historical_norms[mask])
    
    if len(similarities) < top_n: return None
        
    top_indices = np.argpartition(similarities, -top_n)[-top_n:]
    original_indices = np.where(mask)[0][top_indices]
    
    outcome_indices = original_indices + window_size
    valid_indices = outcome_indices[outcome_indices < len(log_returns_close)]
    if len(valid_indices) == 0: return None
        
    return np.mean(log_returns_close[valid_indices]), np.mean(similarities[top_indices])

def predict_next_candles(candles_data, num_predictions=5):
    """Trains a model and predicts the next N candles."""
    if len(candles_data) < 150: return []

    data = np.array([[float(c[1]), float(c[2]), float(c[3]), float(c[4]), float(c[5])] for c in candles_data])
    features, target = build_features(data)
    if features.shape[0] < 30: return []
        
    X_train, y_train = features[-300:], target[-300:]
    
    mean, std = np.mean(X_train, axis=0), np.std(X_train, axis=0)
    std[std == 0] = 1
    X_train_norm = (X_train - mean) / std
    X_train_int = np.c_[np.ones(X_train_norm.shape[0]), X_train_norm]
    
    try:
        coeffs, _, _, _ = np.linalg.lstsq(X_train_int, y_train, rcond=None)
    except np.linalg.LinAlgError: return []

    predictions = []
    current_data = data.copy()
    avg_upper_wick = np.mean(data[:, 1] - np.maximum(data[:, 0], data[:, 3]))
    avg_lower_wick = np.mean(np.minimum(data[:, 0], data[:, 3]) - data[:, 2])

    for i in range(num_predictions):
        last_feature_vec = build_single_feature_vec(current_data)
        if last_feature_vec is None: break
        
        last_feature_vec_norm = (last_feature_vec - mean) / std
        last_feature_vec_int = np.insert(last_feature_vec_norm, 0, 1)
        predicted_log_return = last_feature_vec_int @ coeffs
        
        pattern_info = find_similar_patterns(current_data)
        if pattern_info:
            pattern_outcome, confidence = pattern_info
            blending_factor = min(confidence * 0.5, 0.5)
            predicted_log_return = (predicted_log_return * (1 - blending_factor)) + (pattern_outcome * blending_factor)

        last_close = current_data[-1, 3]
        predicted_close = last_close * np.exp(predicted_log_return)
        
        pred_open = last_close
        pred_high = max(pred_open, predicted_close) + avg_upper_wick
        pred_low = min(pred_open, predicted_close) - avg_lower_wick
        
        last_ts = int(candles_data[-1][0])
        interval_ms = int(candles_data[-1][0]) - int(candles_data[-2][0])
        new_ts = last_ts + (i + 1) * interval_ms
        
        new_candle_data = np.array([[pred_open, pred_high, pred_low, predicted_close, 0]])
        current_data = np.vstack([current_data, new_candle_data])

        predictions.append({"t": new_ts, "o": pred_open, "h": pred_high, "l": pred_low, "c": predicted_close})

    return predictions

# --- Flask Routes ---
@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE)

@app.route('/api/candles')
def api_candles():
    symbol = request.args.get('symbol', 'BTCUSDT').upper()
    interval = request.args.get('interval', '15')
    num_predictions = request.args.get('predictions', 5, type=int)
    num_predictions = max(1, min(num_predictions, 20)) 

    if interval not in ALLOWED_INTERVALS: return jsonify({"error": f"Invalid interval"}), 400

    try:
        raw_candles = get_bybit_data(symbol, interval)
        if not raw_candles: return jsonify({"error": "No data from Bybit API (check symbol)"}), 404
        
        historical = [{"t": int(c[0]), "o": float(c[1]), "h": float(c[2]), "l": float(c[3]), "c": float(c[4]), "v": float(c[5])} for c in raw_candles]
        predicted = predict_next_candles(raw_candles, num_predictions)

        return jsonify({"symbol": symbol, "interval": interval, "candles": historical, "predicted": predicted})
    except (ConnectionError, ValueError) as e: return jsonify({"error": str(e)}), 500
    except Exception as e:
        app.logger.error(f"An unexpected error occurred: {e}")
        return jsonify({"error": "An internal server error occurred."}), 500

# --- Main Execution ---
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)