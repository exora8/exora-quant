# ==============================================================================
# Candlestick Chart with AI Prediction (Flask, amCharts5) - PURE PYTHON VERSION
# ==============================================================================
# This single-file Python project runs a Flask web server to display a
# candlestick chart with AI-predicted future candles.
#
# Core Libraries:
# - Flask: Web server framework.
# - requests: To fetch data from the Bybit API.
# - math, statistics: Standard libraries for numerical operations.
#
# Prohibited Libraries (as per requirements):
# - numpy: NOT USED. All numerical/statistical code is pure Python.
# - pandas: Not used for data manipulation.
# - scikit-learn: Not used for machine learning models.
#
# How to Run:
# 1. Install dependencies:
#    pip install Flask requests
#
# 2. Run the Flask development server:
#    python app.py
#
# 3. Access the application in your browser:
#    http://127.0.0.1:5000
# ==============================================================================

import time
import requests
import math
import statistics
from flask import Flask, jsonify, render_template_string, request

# --- Configuration ---
# MODIFIED: Removed smaller timeframes like 1, 3, 5 minutes. 3H (180) is not supported by the API.
ALLOWED_INTERVALS = ["15", "30", "60", "120", "240", "360", "720", "D", "W", "M"]
BYBIT_API_URL = "https://api.bybit.com/v5/market/kline"
CACHE_TTL_SECONDS = 15

# --- Flask App Initialization ---
app = Flask(__name__)
cache = {}


# --- HTML & JavaScript Template ---
# This single string contains the entire frontend code.
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
        
        /* --- MODIFIED: Wrapper for controls and toggle button --- */
        #controls-wrapper {
            position: absolute; top: 15px; left: 15px; z-index: 100;
            display: flex; align-items: flex-start; gap: 10px;
        }

        /* --- NEW: Style for the toggle button --- */
        #toggle-controls-btn {
            width: 40px; height: 40px; padding: 0; font-size: 20px;
            border-radius: 8px; border: 1px solid #444; background-color: rgba(25, 25, 25, 0.85);
            color: #eee; cursor: pointer; backdrop-filter: blur(5px);
            display: flex; align-items: center; justify-content: center;
        }

        .controls-overlay {
            background-color: rgba(25, 25, 25, 0.85); backdrop-filter: blur(5px);
            padding: 12px; border-radius: 8px; border: 1px solid #333;
            display: flex; flex-wrap: wrap; align-items: center; gap: 12px; box-shadow: 0 4px 15px rgba(0,0,0,0.5);
            /* --- NEW: Transition for smooth hide/show --- */
            transition: transform 0.3s ease-in-out, opacity 0.3s ease-in-out;
        }
        /* --- NEW: Hidden state for the overlay --- */
        .controls-overlay.hidden {
            transform: translateX(calc(-100% - 20px));
            opacity: 0;
            pointer-events: none;
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

        @media (max-width: 768px) {
            #controls-wrapper { top: 10px; left: 10px; right: 10px; }
            .controls-overlay {
                flex-direction: column; align-items: stretch; gap: 10px;
                /* --- MODIFIED: Ensure overlay expands in mobile view --- */
                flex-grow: 1;
            }
            .controls-overlay input, .controls-overlay select, .controls-overlay button {
                width: 100%; box-sizing: border-box;
            }
            #status { margin-left: 0; margin-top: 5px; text-align: center; }
        }
    </style>
    <!-- amCharts 5 CDN -->
    <script src="https://cdn.amcharts.com/lib/5/index.js"></script>
    <script src="https://cdn.amcharts.com/lib/5/xy.js"></script>
    <script src="https://cdn.amcharts.com/lib/5/themes/Animated.js"></script>
    <script src="https://cdn.amcharts.com/lib/5/themes/Dark.js"></script>
</head>
<body>
    <div id="chartdiv"></div>

    <!-- --- MODIFIED: New wrapper and toggle button added --- -->
    <div id="controls-wrapper">
        <button id="toggle-controls-btn" title="Toggle Controls">☰</button>
        <div class="controls-overlay">
            <label for="symbol">Symbol:</label>
            <input type="text" id="symbol" value="BTCUSDT" placeholder="e.g., BTCUSDT">
            <label for="interval">Timeframe:</label>
            <!-- MODIFIED: Removed small timeframes and added larger ones like 2H, 6H, 12H -->
            <select id="interval">
                <option value="60">1 hour</option>
                <option value="120">2 hours</option>
                <option value="240">4 hours</option>
                <option value="360">6 hours</option>
                <option value="720">12 hours</option>
                <option value="D">Daily</option>
                <option value="W">Weekly</option>
                <option value="M">Monthly</option>
            </select>
            <label for="num_predictions">Predictions:</label>
            <input type="number" id="num_predictions" value="5" min="1" max="20">
            <button id="fetchButton">Fetch & Predict</button>
            <div id="status"></div>
        </div>
    </div>

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
            const positionModal = document.getElementById('position-modal');
            const entryPriceInput = document.getElementById('entry-price');
            
            // --- NEW: Elements for toggling controls ---
            const toggleBtn = document.getElementById('toggle-controls-btn');
            const controlsOverlay = document.querySelector('.controls-overlay');

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
                
                const cursor = chart.set("cursor", am5xy.XYCursor.new(root, { behavior: "panX" }));
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

                series.columns.template.events.on("click", function(ev) {
                    const dataItem = ev.target.dataItem;
                    if (dataItem) {
                        selectedCandleTimestamp = dataItem.get("valueX");
                        entryPriceInput.value = dataItem.get("valueY");
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
            
            function drawPositionOnChart(entryPrice, tpPrice, slPrice, direction, startTime) {
                positionRanges.forEach(range => range.dispose());
                positionRanges = [];
                const entryRange = yAxis.createAxisRange(yAxis.makeDataItem({ value: entryPrice }));
                entryRange.get("grid").setAll({ stroke: am5.color(0x0099ff), strokeWidth: 2, strokeOpacity: 1, strokeDasharray: [3, 3] });
                entryRange.get("label").setAll({ text: "Entry", fill: am5.color(0x0099ff), location: 0, inside: true, align: "right", dx: 60 });
                positionRanges.push(entryRange);
                const tpRange = yAxis.createAxisRange(yAxis.makeDataItem({ value: tpPrice }));
                tpRange.get("grid").setAll({ stroke: am5.color(0x00c782), strokeWidth: 2, strokeOpacity: 1 });
                tpRange.get("label").setAll({ text: "TP", fill: am5.color(0x00c782), location: 0, inside: true, align: "right", dx: 30 });
                positionRanges.push(tpRange);
                const slRange = yAxis.createAxisRange(yAxis.makeDataItem({ value: slPrice }));
                slRange.get("grid").setAll({ stroke: am5.color(0xf34a4a), strokeWidth: 2, strokeOpacity: 1 });
                slRange.get("label").setAll({ text: "SL", fill: am5.color(0xf34a4a), location: 0, inside: true, align: "right" });
                positionRanges.push(slRange);
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
                    const intervalConfig = !isNaN(interval) ? { timeUnit: "minute", count: parseInt(interval) } : { timeUnit: { 'D': 'day', 'W': 'week', 'M': 'month' }[interval] || 'day', count: 1 };
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
            
            // --- NEW: Event listener for the control panel toggle button ---
            toggleBtn.addEventListener('click', () => {
                controlsOverlay.classList.toggle('hidden');
            });

            document.getElementById('set-position-btn').addEventListener('click', () => {
                const entryPrice = parseFloat(entryPriceInput.value);
                const tpPercent = parseFloat(document.getElementById('tp-percent').value);
                const slPercent = parseFloat(document.getElementById('sl-percent').value);
                const direction = document.querySelector('input[name="direction"]:checked').value;
                if (isNaN(entryPrice) || isNaN(tpPercent) || isNaN(slPercent) || !selectedCandleTimestamp) return;
                let tpPrice = direction === 'long' ? entryPrice * (1 + tpPercent / 100) : entryPrice * (1 - tpPercent / 100);
                let slPrice = direction === 'long' ? entryPrice * (1 - slPercent / 100) : entryPrice * (1 + slPercent / 100);
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
    """Fetches candlestick data from the Bybit v5 API with in-memory caching."""
    cache_key = f"{symbol}-{interval}"
    current_time = time.time()
    if cache_key in cache:
        last_fetch_time, cached_data = cache[cache_key]
        if current_time - last_fetch_time < CACHE_TTL_SECONDS:
            return cached_data
    params = {"category": "spot", "symbol": symbol, "interval": interval, "limit": 500}
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

# --- Prediction Model (Pure Python) ---
def find_similar_patterns_pure_python(data_series, window_size=20, top_n=5):
    """
    Finds historical patterns similar to the most recent one using cosine similarity.
    This is a pure Python implementation without numpy.
    """
    if len(data_series) < 2 * window_size: return None

    def dot_product(v1, v2):
        return sum(x * y for x, y in zip(v1, v2))

    def norm(v):
        return math.sqrt(sum(x * x for x in v))

    current_pattern = data_series[-window_size:]
    current_norm = norm(current_pattern)
    if current_norm == 0: return None

    similarities = []
    for i in range(len(data_series) - window_size):
        historical_pattern = data_series[i : i + window_size]
        historical_norm = norm(historical_pattern)
        if historical_norm > 0:
            similarity = dot_product(historical_pattern, current_pattern) / (historical_norm * current_norm)
            similarities.append({"sim": similarity, "outcome_index": i + window_size})

    if not similarities: return None
    
    similarities.sort(key=lambda x: x["sim"], reverse=True)
    top_patterns = similarities[:top_n]

    if not top_patterns: return None

    avg_outcome = statistics.mean(data_series[p["outcome_index"]] for p in top_patterns)
    return avg_outcome

def predict_next_candles(candles_data, num_predictions=5):
    """
    Trains a simplified model and predicts the next N candles using pure Python.
    """
    if len(candles_data) < 50: return []

    data = [[float(c[i]) for i in range(6)] for c in candles_data]
    
    upper_wicks = [d[2] - max(d[1], d[4]) for d in data]
    lower_wicks = [min(d[1], d[4]) - d[3] for d in data]
    avg_upper_wick = statistics.mean(upper_wicks) if upper_wicks else 0
    avg_lower_wick = statistics.mean(lower_wicks) if lower_wicks else 0

    predictions = []
    current_candles = data[:]
    
    for i in range(num_predictions):
        closes = [c[4] for c in current_candles]
        
        log_returns_close = []
        for j in range(1, len(closes)):
            if closes[j-1] > 0:
                log_returns_close.append(math.log(closes[j] / closes[j-1]))
        
        if not log_returns_close: break

        predicted_log_return = find_similar_patterns_pure_python(log_returns_close)
        
        if predicted_log_return is None: break

        last_close = current_candles[-1][4]
        predicted_close = last_close * math.exp(predicted_log_return)
        
        pred_open = last_close
        pred_high = max(pred_open, predicted_close) + avg_upper_wick
        pred_low = min(pred_open, predicted_close) - avg_lower_wick
        
        last_ts = int(current_candles[-1][0])
        interval_ms = int(current_candles[-1][0]) - int(current_candles[-2][0])
        new_ts = last_ts + interval_ms
        
        new_candle = [new_ts, pred_open, pred_high, pred_low, predicted_close, 0]
        current_candles.append(new_candle)

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
    if interval not in ALLOWED_INTERVALS: return jsonify({"error": "Invalid interval"}), 400
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
