# ==============================================================================
# Candlestick Chart with AI Prediction (Flask, amCharts5) - VERSI TERMUX INDONESIA
# ==============================================================================
# Skrip ini telah dimodifikasi untuk eksekusi yang ringan dan andal di Termux,
# dengan dukungan penuh untuk perintah suara dalam Bahasa Indonesia.
#
# Fitur Suara (Termux):
# - Ketika skrip menampilkan "Mendengarkan...", pop-up Termux API akan muncul
#   agar Anda dapat berbicara.
# - Ucapkan nama pasangan kripto (contoh: "Bitcoin", "Ethereum").
# - Skrip akan mengoreksinya secara otomatis ke ticker yang valid ("BTC", "ETH").
# - Analisis akan dilakukan pada pasangan USDT (misal: BTCUSDT) pada timeframe 1 jam.
# - Ringkasan analisis akan diucapkan kembali kepada Anda menggunakan suara Bahasa Indonesia.
#
# Cara Menjalankan di Termux:
# 1. Instal aplikasi Termux:API dari F-Droid atau Google Play Store.
#
# 2. Di terminal Termux, instal paket yang diperlukan:
#    pkg update && pkg upgrade
#    pkg install python termux-api
#
# 3. Instal library Python yang dibutuhkan:
#    pip install Flask requests "thefuzz"
#
# 4. (PENTING) Atur Bahasa Input Suara di Android Anda:
#    - Buka Pengaturan -> Sistem -> Bahasa & masukan -> Keyboard virtual.
#    - Pilih "Pengetikan Suara Google" (atau yang serupa).
#    - Pastikan Bahasa utamanya adalah "Bahasa Indonesia".
#
# 5. Simpan skrip ini sebagai file Python (misal: chart_app_id.py).
#
# 6. Jalankan skrip:
#    python chart_app_id.py
#
# 7. Akses chart di browser ponsel Anda: http://localhost:5000
# 8. Ikuti petunjuk di terminal untuk menggunakan perintah suara.
# ==============================================================================

import time
import requests
import math
import statistics
import threading
import subprocess
import json
from flask import Flask, jsonify, render_template_string, request

# --- Voice and Parsing Libraries ---
try:
    from thefuzz import process as fuzzy_process
    termux_api_check = subprocess.run(['termux-toast', '-s', 'API OK'], capture_output=True, text=True)
    if termux_api_check.returncode != 0:
        raise ImportError("Termux API not working.")
    VOICE_ENABLED = True
except (ImportError, FileNotFoundError):
    print("="*50)
    print("PERINGATAN: Ketergantungan perintah suara untuk Termux tidak terpenuhi.")
    print("Pastikan Anda sudah menjalankan:")
    print("1. pkg install termux-api")
    print("2. pip install thefuzz")
    print("3. Menginstal aplikasi Termux:API di ponsel Anda.")
    print("Fitur suara akan dinonaktifkan.")
    print("="*50)
    VOICE_ENABLED = False


# --- Configuration ---
ALLOWED_INTERVALS = ["15", "30", "60", "120", "240", "360", "720", "D", "W", "M"]
BYBIT_API_URL = "https://api.bybit.com/v5/market/kline"
BYBIT_SYMBOLS_URL = "https://api.bybit.com/v5/market/tickers"
CACHE_TTL_SECONDS = 15

# --- Flask App Initialization ---
app = Flask(__name__)
cache = {}

# --- Global variables for voice assistant ---
VALID_TICKERS = []

# --- HTML & JavaScript Template (UNMODIFIED) ---
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
        #controls-wrapper {
            position: absolute; top: 15px; left: 15px; z-index: 100;
            display: flex; align-items: flex-start; gap: 10px;
        }
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
            transition: transform 0.3s ease-in-out, opacity 0.3s ease-in-out;
        }
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
            .controls-overlay { flex-direction: column; align-items: stretch; gap: 10px; flex-grow: 1; }
            .controls-overlay input, .controls-overlay select, .controls-overlay button { width: 100%; box-sizing: border-box; }
            #status { margin-left: 0; margin-top: 5px; text-align: center; }
        }
    </style>
    <script src="https://cdn.amcharts.com/lib/5/index.js"></script>
    <script src="https://cdn.amcharts.com/lib/5/xy.js"></script>
    <script src="https://cdn.amcharts.com/lib/5/themes/Animated.js"></script>
    <script src="https://cdn.amcharts.com/lib/5/themes/Dark.js"></script>
</head>
<body>
    <div id="chartdiv"></div>
    <div id="controls-wrapper">
        <button id="toggle-controls-btn" title="Toggle Controls">☰</button>
        <div class="controls-overlay">
            <label for="symbol">Symbol:</label>
            <input type="text" id="symbol" value="BTCUSDT" placeholder="e.g., BTCUSDT">
            <label for="interval">Timeframe:</label>
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
            const toggleBtn = document.getElementById('toggle-controls-btn');
            const controlsOverlay = document.querySelector('.controls-overlay');
            let selectedCandleTimestamp = null;
            let positionRanges = [];
            let root, chart, xAxis, yAxis, series, predictedSeries;
            function createChart() {
                if (root) root.dispose();
                root = am5.Root.new("chartdiv");
                root.setThemes([am5themes_Animated.new(root), am5themes_Dark.new(root)]);
                chart = root.container.children.push(am5xy.XYChart.new(root, { panX: true, panY: false, wheelX: "panX", wheelY: "zoomX", pinchZoomX: true }));
                const cursor = chart.set("cursor", am5xy.XYCursor.new(root, { behavior: "panX" }));
                cursor.lineY.set("visible", false);
                xAxis = chart.xAxes.push(am5xy.DateAxis.new(root, { baseInterval: { timeUnit: "minute", count: 1 }, renderer: am5xy.AxisRendererX.new(root, { minGridDistance: 70 }), tooltip: am5.Tooltip.new(root, {}) }));
                yAxis = chart.yAxes.push(am5xy.ValueAxis.new(root, { renderer: am5xy.AxisRendererY.new(root, {}), tooltip: am5.Tooltip.new(root, {}) }));
                series = chart.series.push(am5xy.CandlestickSeries.new(root, { name: "Historical", xAxis: xAxis, yAxis: yAxis, valueXField: "t", openValueYField: "o", highValueYField: "h", lowValueYField: "l", valueYField: "c", tooltip: am5.Tooltip.new(root, { labelText: "Source: Real\\nOpen: {openValueY}\\nHigh: {highValueY}\\nLow: {lowValueY}\\nClose: {valueY}" }) }));
                series.columns.template.events.on("click", function (ev) {
                    const dataItem = ev.target.dataItem;
                    if (dataItem) {
                        selectedCandleTimestamp = dataItem.get("valueX");
                        entryPriceInput.value = dataItem.get("valueY");
                        positionModal.classList.add("visible");
                    }
                });
                predictedSeries = chart.series.push(am5xy.CandlestickSeries.new(root, { name: "Predicted", xAxis: xAxis, yAxis: yAxis, valueXField: "t", openValueYField: "o", highValueYField: "h", lowValueYField: "l", valueYField: "c", tooltip: am5.Tooltip.new(root, { labelText: "Source: AI Prediction\\nOpen: {openValueY}\\nHigh: {highValueY}\\nLow: {lowValueY}\\nClose: {valueY}" }) }));
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
            toggleBtn.addEventListener('click', () => { controlsOverlay.classList.toggle('hidden'); });
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
            document.getElementById('cancel-position-btn').addEventListener('click', () => { positionModal.classList.remove('visible'); });
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
    except requests.exceptions.RequestException as e: raise ConnectionError(f"Gagal terhubung ke API Bybit: {e}")
    except (ValueError, KeyError) as e: raise ValueError(f"Error saat memproses respons Bybit: {e}")

# --- Prediction Model (Pure Python) ---
def find_similar_patterns_pure_python(data_series, window_size=20, top_n=5):
    if len(data_series) < 2 * window_size: return None
    def dot_product(v1, v2): return sum(x * y for x, y in zip(v1, v2))
    def norm(v): return math.sqrt(sum(x * x for x in v))
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
        log_returns_close = [math.log(closes[j] / closes[j-1]) for j in range(1, len(closes)) if closes[j-1] > 0]
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

# --- [TERMUX INDONESIA] FUNGSI PERINTAH SUARA ---

def speak(text):
    """Menggunakan Termux-API untuk mengucapkan teks dengan suara Bahasa Indonesia."""
    print(f"Mengucapkan: {text}")
    try:
        subprocess.run(['termux-tts-speak', '-l', 'id-ID', text], check=True)
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        print(f"Error dengan termux-tts-speak: {e}")
        print("Pastikan Termux:API sudah terinstal dan dikonfigurasi.")

def get_all_bybit_tickers():
    """Mengambil semua ticker spot USDT dari Bybit untuk parser otomatis."""
    global VALID_TICKERS
    print("Mengambil ticker yang tersedia dari Bybit untuk koreksi otomatis...")
    try:
        params = {"category": "spot"}
        response = requests.get(BYBIT_SYMBOLS_URL, params=params)
        response.raise_for_status()
        data = response.json()
        if data.get("retCode") == 0:
            symbols = [
                item['symbol'].replace('USDT', '')
                for item in data['result']['list']
                if item['symbol'].endswith('USDT')
            ]
            VALID_TICKERS = list(set(symbols))
            print(f"Berhasil memuat {len(VALID_TICKERS)} ticker untuk koreksi otomatis.")
        else:
            print(f"Peringatan: Tidak dapat mengambil ticker dari Bybit: {data.get('retMsg')}")
    except Exception as e:
        print(f"Error saat mengambil ticker Bybit: {e}")

def find_closest_ticker(text, ticker_list):
    """Mencari ticker yang paling mirip dari daftar menggunakan fuzzy matching."""
    if not ticker_list or not text:
        return None
    for ticker in ticker_list:
        if ticker.lower() in text.lower().split():
            return ticker
    best_match = fuzzy_process.extractOne(text, ticker_list)
    if best_match and best_match[1] > 60:
        return best_match[0]
    return None

def analyze_and_speak(ticker):
    """Melakukan analisis dan mengucapkan hasilnya dalam Bahasa Indonesia."""
    symbol = f"{ticker}USDT"
    interval = "60"
    num_predictions = 20
    
    friendly_names = {"BTC": "Bitcoin", "ETH": "Ethereum", "SOL": "Solana"}
    ticker_name = friendly_names.get(ticker, ticker)

    print(f"Menganalisis {symbol} pada timeframe {interval}m dengan {num_predictions} prediksi...")
    
    try:
        raw_candles = get_bybit_data(symbol, interval)
        if not raw_candles:
            speak(f"Maaf, saya tidak dapat menemukan data untuk {ticker_name}.")
            return

        predicted_candles = predict_next_candles(raw_candles, num_predictions)
        if not predicted_candles:
            speak(f"Maaf, saya tidak dapat membuat prediksi untuk {ticker_name}.")
            return

        last_price = float(raw_candles[-1][4])
        predicted_closes = [p['c'] for p in predicted_candles]
        final_predicted_price = predicted_closes[-1]
        
        direction = "naik" if final_predicted_price > last_price else "turun"
        percent_change = abs((final_predicted_price - last_price) / last_price * 100)
        
        combined_prices = [float(c[4]) for c in raw_candles[-10:]] + predicted_closes
        consolidation_low = min(combined_prices)
        consolidation_high = max(combined_prices)
        
        direction_move = "pergerakan naik" if direction == "naik" else "pergerakan turun"

        summary = (
            f"Baik, untuk {ticker_name} pada timeframe 1 jam, harga kemungkinan akan {direction} "
            f"sekitar {percent_change:.2f} persen dari harga terakhir di {last_price:,.2f}. "
            f"Harga mungkin akan berkonsolidasi di sekitar {consolidation_low:,.2f} dan {consolidation_high:,.2f} "
            f"sebelum melanjutkan {direction_move}."
        )
        
        speak(summary)

    except Exception as e:
        print(f"Terjadi error saat analisis: {e}")
        speak(f"Maaf, terjadi error saat menganalisis {ticker_name}.")

def voice_command_loop():
    """Loop utama untuk mendengarkan perintah suara menggunakan Termux-API."""
    print("\n🚀 Asisten Suara Termux Siap! 🚀")
    speak("Asisten suara online.")

    while True:
        try:
            # --- PENJELASAN DURASI MENDENGARKAN ---
            # Durasi mendengarkan diatur oleh sistem Android, bukan oleh skrip.
            # Android akan berhenti mendengarkan secara otomatis setelah Anda berhenti berbicara
            # selama beberapa saat. Tidak ada cara untuk memperpanjangnya dari dalam skrip.
            print("\nMendengarkan... Ucapkan perintah setelah pop-up muncul. Sistem akan berhenti merekam setelah Anda diam.")
            
            # Memanggil API Termux untuk pengenalan suara.
            # Tidak ada parameter untuk mengatur durasi secara manual.
            result = subprocess.run(
                ['termux-speech-to-text'],
                capture_output=True, text=True, check=True
            )
            
            text = result.stdout.strip()
            
            if not text:
                print("Tidak ada input suara diterima.")
                continue

            print(f"Terdengar: '{text}'")
            
            matched_ticker = find_closest_ticker(text, VALID_TICKERS)
            
            if matched_ticker:
                print(f"Dikoreksi menjadi: '{matched_ticker}'")
                analyze_and_speak(matched_ticker)
            else:
                speak("Maaf, saya tidak mengenali pasangan kripto itu. Silakan coba lagi.")

        except subprocess.CalledProcessError:
            print("Pengenalan suara dibatalkan atau gagal.")
        except FileNotFoundError:
            print("Error: Perintah 'termux-speech-to-text' tidak ditemukan.")
            speak("Error, perintah Termux API tidak ditemukan. Mematikan asisten suara.")
            break
        except Exception as e:
            print(f"Terjadi error tak terduga di loop suara: {e}")
            time.sleep(2)

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
    if VOICE_ENABLED:
        get_all_bybit_tickers()
        voice_thread = threading.Thread(target=voice_command_loop, daemon=True)
        voice_thread.start()
    
    app.run(host='0.0.0.0', port=5000, debug=False)
