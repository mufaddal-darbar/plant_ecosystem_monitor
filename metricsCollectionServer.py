import math
import sqlite3
from datetime import datetime
from flask import Flask, request, jsonify, render_template_string

app = Flask(__name__)
DB_NAME = 'plant_data.db'

def init_db():
    with sqlite3.connect(DB_NAME) as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS device_config (
                id INTEGER PRIMARY KEY,
                low_batt REAL DEFAULT 3.65,
                night_lux REAL DEFAULT 50.0,
                day_sleep INTEGER DEFAULT 10,
                night_sleep INTEGER DEFAULT 30,
                config_version INTEGER DEFAULT 1,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        # Seed initial row if empty
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM device_config")
        if cur.fetchone()[0] == 0:
            conn.execute('''
                INSERT INTO device_config (id, low_batt, night_lux, day_sleep, night_sleep, config_version)
                VALUES (1, 3.65, 50.0, 10, 30, 1)
            ''')
    with sqlite3.connect(DB_NAME) as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS telemetry (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                battery_voltage REAL,
                battery_percent INTEGER,
                charging BOOLEAN,
                temperature REAL,
                humidity REAL,
                soil_moisture REAL,
                pump_triggered BOOLEAN,
                lux REAL DEFAULT 0,
                wifi_rssi INTEGER DEFAULT -70,
                vpd REAL DEFAULT 0,
                dew_point REAL DEFAULT 0,
                batt_rate_mvh REAL DEFAULT 0,
                soil_depletion_rate REAL DEFAULT 0,
                dli REAL DEFAULT 0
            )
        ''')
        # Ensure migration columns exist
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(telemetry)")
        cols = [c[1] for c in cursor.fetchall()]
        new_cols = [
            ("lux", "REAL DEFAULT 0"),
            ("wifi_rssi", "INTEGER DEFAULT -70"),
            ("vpd", "REAL DEFAULT 0"),
            ("dew_point", "REAL DEFAULT 0"),
            ("batt_rate_mvh", "REAL DEFAULT 0"),
            ("soil_depletion_rate", "REAL DEFAULT 0"),
            ("dli", "REAL DEFAULT 0")
        ]
        for col, col_type in new_cols:
            if col not in cols:
                cursor.execute(f"ALTER TABLE telemetry ADD COLUMN {col} {col_type}")
        conn.commit()

init_db()

def compute_instant_metrics(temp: float, hum: float):
    # Vapor Pressure Deficit (VPD in kPa)
    vp_sat = 0.61078 * math.exp((17.27 * temp) / (temp + 237.3))
    vp_air = vp_sat * (max(hum, 1.0) / 100.0)
    vpd = round(vp_sat - vp_air, 2)

    # Dew Point (°C) via Magnus Formula
    a, b = 17.27, 237.7
    gamma = ((a * temp) / (b + temp)) + math.log(max(hum, 1.0) / 100.0)
    dew_point = round((b * gamma) / (a - gamma), 1)

    return vpd, dew_point

def compute_derivative_metrics(conn, current_v: float, current_soil: float, current_lux: float):
    cursor = conn.cursor()
    cursor.execute("SELECT timestamp, battery_voltage, soil_moisture FROM telemetry ORDER BY id DESC LIMIT 1")
    prev_row = cursor.fetchone()

    batt_rate_mvh = 0.0
    soil_depletion = 0.0

    if prev_row:
        try:
            prev_time = datetime.strptime(prev_row[0], "%Y-%m-%d %H:%M:%S")
            dt_hours = (datetime.utcnow() - prev_time).total_seconds() / 3600.0
            if 0.05 <= dt_hours <= 3.0: # Valid window between 3 mins and 3 hours
                # mV / hr: positive = charging, negative = discharging
                batt_rate_mvh = round(((current_v - prev_row[1]) * 1000.0) / dt_hours, 1)
                
                # Soil % drop per day: positive indicates water drying up
                soil_diff = prev_row[2] - current_soil
                soil_depletion = round((soil_diff / dt_hours) * 24.0, 1)
        except Exception as e:
            print("Derivative parse error:", e)

    # Compute Today's DLI (mol / m² / day)
    today_str = datetime.utcnow().strftime("%Y-%m-%d")
    cursor.execute("SELECT timestamp, lux FROM telemetry WHERE timestamp >= ? ORDER BY id ASC", (today_str + " 00:00:00",))
    today_records = cursor.fetchall()
    
    total_dli = 0.0
    if len(today_records) >= 2:
        for i in range(len(today_records) - 1):
            t1 = datetime.strptime(today_records[i][0], "%Y-%m-%d %H:%M:%S")
            t2 = datetime.strptime(today_records[i+1][0], "%Y-%m-%d %H:%M:%S")
            dt_sec = (t2 - t1).total_seconds()
            avg_lux = (today_records[i][1] + today_records[i+1][1]) / 2.0
            # PPFD conversion for daylight: 1 lux ≈ 0.0185 µmol/(m²·s)
            ppfd = avg_lux * 0.0185
            total_dli += (ppfd * dt_sec) / 1_000_000.0
    elif len(today_records) == 1:
        total_dli = (today_records[0][1] * 0.0185 * 600) / 1_000_000.0

    return batt_rate_mvh, max(0.0, soil_depletion), round(total_dli, 2)



@app.route('/api/config', methods=['GET'])
def get_device_config():
    with sqlite3.connect(DB_NAME) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT low_batt, night_lux, day_sleep, night_sleep, config_version FROM device_config WHERE id = 1").fetchone()
        return jsonify(dict(row)), 200

@app.route('/api/config/update', methods=['POST'])
def update_device_config():
    data = request.get_json() or {}
    low_batt = float(data.get('low_batt', 3.65))
    night_lux = float(data.get('night_lux', 50.0))
    day_sleep = int(data.get('day_sleep', 10))
    night_sleep = int(data.get('night_sleep', 30))

    with sqlite3.connect(DB_NAME) as conn:
        conn.execute('''
            UPDATE device_config 
            SET low_batt = ?, night_lux = ?, day_sleep = ?, night_sleep = ?, config_version = config_version + 1, updated_at = CURRENT_TIMESTAMP
            WHERE id = 1
        ''', (low_batt, night_lux, day_sleep, night_sleep))

    return jsonify({"status": "updated"}), 200



@app.route('/api/telemetry', methods=['POST'])
def receive_data():
    data = request.get_json() or {}

    # Extract all incoming fields from payload
    temp = float(data.get('temperature', 0.0))
    hum = float(data.get('humidity', 0.0))
    current_v = float(data.get('battery_voltage', 0.0))
    batt_pct = int(data.get('battery_percent', 0))       # <--- MUST BE DEFINED HERE
    current_soil = float(data.get('soil_moisture', 0.0))
    lux = float(data.get('lux', 0.0))
    rssi = int(data.get('wifi_rssi', -70))
    charging = bool(data.get('charging', False))
    pump_triggered = bool(data.get('pump_triggered', False))

    incoming_ts = data.get('timestamp')
    if not incoming_ts or "--" in incoming_ts:
        incoming_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    vpd, dew = compute_instant_metrics(temp, hum)

    with sqlite3.connect(DB_NAME) as conn:
        batt_rate, soil_rate, dli = compute_derivative_metrics(conn, current_v, current_soil, lux)
        conn.execute('''
            INSERT INTO telemetry (
                timestamp, battery_voltage, battery_percent, charging, 
                temperature, humidity, soil_moisture, pump_triggered,
                lux, wifi_rssi, vpd, dew_point, batt_rate_mvh,
                soil_depletion_rate, dli
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            incoming_ts,
            current_v, 
            batt_pct,          # <--- Line 134 now has access to batt_pct
            charging,
            temp, 
            hum, 
            current_soil, 
            pump_triggered,
            lux, 
            rssi, 
            vpd, 
            dew, 
            batt_rate, 
            soil_rate, 
            dli
        ))
    with sqlite3.connect(DB_NAME) as conn:
        conn.row_factory = sqlite3.Row
        cfg = conn.execute("SELECT low_batt, night_lux, day_sleep, night_sleep, config_version FROM device_config WHERE id = 1").fetchone()
    esp_ver = int(data.get('config_ver', 0))
    server_ver = int(cfg['config_version'])

    resp = {"status": "success"}
    if esp_ver < server_ver:
        resp["config"] = dict(cfg)
    return jsonify(resp), 200
    #return jsonify({"status": "success", "vpd": vpd, "dew_point": dew, "dli": dli}), 200
@app.route('/api/latest', methods=['GET'])
def get_latest():
    with sqlite3.connect(DB_NAME) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT * FROM telemetry ORDER BY id DESC LIMIT 1")
        row = cur.fetchone()
        return jsonify(dict(row)) if row else jsonify({})

@app.route('/api/history', methods=['GET'])
def get_history():
    limit = request.args.get('limit', default=48, type=int)
    with sqlite3.connect(DB_NAME) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT * FROM (SELECT * FROM telemetry ORDER BY id DESC LIMIT ?) ORDER BY id ASC", (limit,))
        rows = cur.fetchall()
        return jsonify([dict(r) for r in rows])

@app.route('/', methods=['GET'])
def dashboard():
    return render_template_string(HTML_TEMPLATE)

# ----------------- UI TEMPLATE WITH ALL DERIVED CARDS -----------------
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Plant & Solar Telemetry Hub</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
  <style>
    :root {
      --bg: #0d1117;
      --card-bg: #161b22;
      --border: #30363d;
      --text: #c9d1d9;
      --text-muted: #8b949e;
      --green: #238636;
      --yellow: #d29922;
      --red: #da3633;
      --blue: #58a6ff;
      --purple: #bc8cff;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
    body { background-color: var(--bg); color: var(--text); padding: 16px; min-height: 100vh; }
    header { display: flex; justify-content: space-between; align-items: center; padding-bottom: 14px; border-bottom: 1px solid var(--border); margin-bottom: 18px; }
    h1 { font-size: 1.2rem; color: #f0f6fc; display: flex; align-items: center; gap: 8px; }
    .status-badge { font-size: 0.8rem; padding: 4px 10px; border-radius: 12px; background: #21262d; border: 1px solid var(--border); }

    .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 12px; margin-bottom: 18px; }
    .card { background: var(--card-bg); border: 1px solid var(--border); border-radius: 8px; padding: 12px; border-left: 4px solid var(--border); }
    .card-title { font-size: 0.72rem; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 6px; display: flex; justify-content: space-between; }
    .card-value { font-size: 1.45rem; font-weight: 700; color: #f0f6fc; display: flex; align-items: baseline; gap: 4px; }
    .card-sub { font-size: 0.74rem; color: var(--text-muted); margin-top: 4px; }
    .warn-icon { color: var(--red); display: none; font-size: 1rem; }

    .card.status-good { border-left-color: var(--green); }
    .card.status-warn { border-left-color: var(--yellow); }
    .card.status-crit { border-left-color: var(--red); }
    .card.status-crit .warn-icon { display: inline; animation: blink 1s infinite; }
    @keyframes blink { 0%, 100% { opacity: 1; } 50% { opacity: 0.2; } }

    .chart-container { background: var(--card-bg); border: 1px solid var(--border); border-radius: 8px; padding: 14px; margin-bottom: 16px; }
    .chart-title { font-size: 0.85rem; color: var(--text-muted); margin-bottom: 8px; }
  </style>
</head>
<body>

  <header>
    <h1>🌱 Plant Ecosystem Monitor</h1>
    <div class="status-badge" id="last-seen">Connecting...</div>
  </header>

  <div class="grid">
    <!-- Row 1: Core Parameters -->
    <div class="card" id="card-soil">
      <div class="card-title">Soil Moisture <span class="warn-icon" id="warn-soil">⚠️</span></div>
      <div class="card-value"><span id="val-soil">--</span><small>%</small></div>
      <div class="card-sub" id="sub-soil">Depletion: <span id="val-soil-rate">0.0</span> %/day</div>
    </div>

    <div class="card" id="card-batt">
      <div class="card-title">Battery Level <span class="warn-icon" id="warn-batt">⚠️</span></div>
      <div class="card-value"><span id="val-batt-v">--</span><small>V</small></div>
      <div class="card-sub" id="sub-batt-rate">Rate: -- mV/h</div>
    </div>

    <div class="card" id="card-vpd">
      <div class="card-title">VPD (Transpiration) <span class="warn-icon" id="warn-vpd">⚠️</span></div>
      <div class="card-value"><span id="val-vpd">--</span><small>kPa</small></div>
      <div class="card-sub">Optimum: 0.8 - 1.2</div>
    </div>

    <div class="card" id="card-dew">
      <div class="card-title">Dew Point</div>
      <div class="card-value"><span id="val-dew">--</span><small>°C</small></div>
      <div class="card-sub" id="sub-dew-margin">Condensation Margin</div>
    </div>

    <!-- Row 2: Secondary / Microclimate -->
    <div class="card" id="card-temp">
      <div class="card-title">Air Temperature</div>
      <div class="card-value"><span id="val-temp">--</span><small>°C</small></div>
      <div class="card-sub">Ambient Sensor</div>
    </div>

    <div class="card" id="card-hum">
      <div class="card-title">Humidity</div>
      <div class="card-value"><span id="val-hum">--</span><small>%</small></div>
      <div class="card-sub">Ambient RH</div>
    </div>

    <div class="card" id="card-dli">
      <div class="card-title">Daily Light (DLI)</div>
      <div class="card-value"><span id="val-dli">--</span><small>mol</small></div>
      <div class="card-sub">Cumulative Today</div>
    </div>

    <div class="card" id="card-lux">
      <div class="card-title">Solar Radiation</div>
      <div class="card-value"><span id="val-lux">--</span><small>Lux</small></div>
      <div class="card-sub" id="sub-lux">Light Status</div>
    </div>
  </div>
    <!-- Dedicated Card: Net Battery Rate -->
    <div class="card" id="card-batt-rate">
      <div class="card-title">Net Battery Rate</div>
      <div class="card-value"><span id="val-batt-rate">--</span><small>mV/h</small></div>
      <div class="card-sub" id="sub-batt-direction">Solar vs Load</div>
    </div>

    <!-- Dedicated Card: Soil Depletion Rate -->
    <div class="card" id="card-soil-rate">
      <div class="card-title">Soil Depletion</div>
      <div class="card-value"><span id="val-soil-rate-large">--</span><small>% / day</small></div>
      <div class="card-sub">Water Loss Trend</div>
    </div>

  <div class="chart-container">
    <div class="chart-title">Battery Voltage (V) & Solar Charge Recovery</div>
    <canvas id="chartBattery" height="80"></canvas>
  </div>

  <div class="chart-container">
    <div class="chart-title">Soil Moisture (%) & Vapor Pressure Deficit (kPa)</div>
    <canvas id="chartSoilVpd" height="80"></canvas>
  </div>
  
  <div class="card" style="background:#161b22; border:1px solid #30363d; border-radius:8px; padding:16px; margin-top:16px;">
  <h3 style="color:#58a6ff; margin-top:0;">Remote Dynamic Thresholds</h3>
  <div style="display:grid; grid-template-columns: 1fr 1fr; gap:12px;">
    <div>
      <label style="color:#8b949e; font-size:12px;">Low Battery (V)</label>
      <input id="cfg_low_batt" type="number" step="0.01" style="width:100%; padding:8px; background:#0d1117; color:#fff; border:1px solid #30363d; border-radius:4px;">
    </div>
    <div>
      <label style="color:#8b949e; font-size:12px;">Night Lux Cutoff</label>
      <input id="cfg_night_lux" type="number" step="1" style="width:100%; padding:8px; background:#0d1117; color:#fff; border:1px solid #30363d; border-radius:4px;">
    </div>
    <div>
      <label style="color:#8b949e; font-size:12px;">Day Sleep (min)</label>
      <input id="cfg_day_sleep" type="number" step="1" style="width:100%; padding:8px; background:#0d1117; color:#fff; border:1px solid #30363d; border-radius:4px;">
    </div>
    <div>
      <label style="color:#8b949e; font-size:12px;">Night Sleep (min)</label>
      <input id="cfg_night_sleep" type="number" step="1" style="width:100%; padding:8px; background:#0d1117; color:#fff; border:1px solid #30363d; border-radius:4px;">
    </div>
  </div>
  <button onclick="saveRemoteConfig()" style="width:100%; margin-top:14px; padding:10px; background:#238636; color:#fff; border:none; border-radius:6px; font-weight:bold; cursor:pointer;">Push Config to Node</button>
   </div>

  <script>
    let battChart, soilVpdChart;

    function initCharts() {
      const ctxBatt = document.getElementById('chartBattery').getContext('2d');
      battChart = new Chart(ctxBatt, {
        type: 'line',
        data: { labels: [], datasets: [{ label: 'Battery (V)', data: [], borderColor: '#58a6ff', backgroundColor: 'rgba(88,166,255,0.08)', fill: true, tension: 0.25 }] },
        options: {
          responsive: true,
          plugins: { legend: { display: false } },
          scales: {
            x: { grid: { color: '#21262d' }, ticks: { color: '#8b949e', maxTicksLimit: 8 } },
            y: { min: 3.2, max: 4.25, grid: { color: '#21262d' }, ticks: { color: '#8b949e' } }
          }
        }
      });

      const ctxSoil = document.getElementById('chartSoilVpd').getContext('2d');
      soilVpdChart = new Chart(ctxSoil, {
        type: 'line',
        data: {
          labels: [],
          datasets: [
            { label: 'Soil Moisture (%)', data: [], borderColor: '#39c5bb', yAxisID: 'ySoil', tension: 0.25 },
            { label: 'VPD (kPa)', data: [], borderColor: '#d29922', borderDash: [5, 5], yAxisID: 'yVpd', tension: 0.25 }
          ]
        },
        options: {
          responsive: true,
          scales: {
            x: { grid: { color: '#21262d' }, ticks: { color: '#8b949e', maxTicksLimit: 8 } },
            ySoil: { type: 'linear', position: 'left', min: 0, max: 100, grid: { color: '#21262d' }, ticks: { color: '#8b949e' } },
            yVpd: { type: 'linear', position: 'right', min: 0, max: 2.5, grid: { display: false }, ticks: { color: '#8b949e' } }
          }
        }
      });
    }

    async function refresh() {
      try {
        const rLatest = await fetch('/api/latest');
        const d = await rLatest.json();
        
        if (!d || !d.id) return;

        document.getElementById('val-soil').innerText = (d.soil_moisture || 0).toFixed(1);
        document.getElementById('val-soil-rate').innerText = (d.soil_depletion_rate || 0).toFixed(1);
        document.getElementById('val-batt-v').innerText = (d.battery_voltage || 0).toFixed(2);
        
        // Net Battery Drain/Charge indicator
        const bRate = d.batt_rate_mvh || 0;
        const bSign = bRate > 0 ? '▲ +' : (bRate < 0 ? '▼ ' : '');
        document.getElementById('sub-batt-rate').innerText = `${bSign}${bRate.toFixed(1)} mV/h (${d.battery_percent}%)`;

        document.getElementById('val-vpd').innerText = (d.vpd || 0).toFixed(2);
        document.getElementById('val-dew').innerText = (d.dew_point || 0).toFixed(1);
        const dewMargin = (d.temperature - d.dew_point).toFixed(1);
        document.getElementById('sub-dew-margin').innerText = `+${dewMargin}°C above dew`;

        document.getElementById('val-temp').innerText = (d.temperature || 0).toFixed(1);
        document.getElementById('val-hum').innerText = (d.humidity || 0).toFixed(1);
        document.getElementById('val-dli').innerText = (d.dli || 0).toFixed(2);
        document.getElementById('val-lux').innerText = Math.round(d.lux || 0).toLocaleString();
        document.getElementById('last-seen').innerText = `Synced: ${d.timestamp ? d.timestamp.split(' ')[1] : ''}`;

        document.getElementById('val-batt-rate').innerText = `${bSign}${Math.abs(bRate).toFixed(1)}`;
        document.getElementById('sub-batt-direction').innerText = bRate >= 0 ? '☀️ Net Solar Gain' : '🔋 Net Discharge';

        document.getElementById('val-soil-rate-large').innerText = (d.soil_depletion_rate || 0).toFixed(1);

        // Status styling: Soil Moisture
        const cSoil = document.getElementById('card-soil');
        cSoil.className = 'card';
        if (d.soil_moisture < 20 || d.soil_moisture > 80) {
          cSoil.classList.add('status-crit');
          document.getElementById('warn-soil').style.display = 'inline';
        } else if (d.soil_moisture < 30 || d.soil_moisture > 65) {
          cSoil.classList.add('status-warn');
          document.getElementById('warn-soil').style.display = 'none';
        } else {
          cSoil.classList.add('status-good');
          document.getElementById('warn-soil').style.display = 'none';
        }

        // Status styling: Battery
        const cBatt = document.getElementById('card-batt');
        cBatt.className = 'card';
        if (d.battery_voltage < 3.55) {
          cBatt.classList.add('status-crit');
          document.getElementById('warn-batt').style.display = 'inline';
        } else if (d.battery_voltage < 3.75) {
          cBatt.classList.add('status-warn');
          document.getElementById('warn-batt').style.display = 'none';
        } else {
          cBatt.classList.add('status-good');
          document.getElementById('warn-batt').style.display = 'none';
        }

        // Status styling: VPD
        const cVpd = document.getElementById('card-vpd');
        cVpd.className = 'card';
        if (d.vpd < 0.4 || d.vpd > 1.6) {
          cVpd.classList.add('status-warn');
        } else {
          cVpd.classList.add('status-good');
        }

        document.getElementById('sub-lux').innerText = (d.lux > 500) ? '☀️ Solar Harvesting' : '🌙 Low Light / Night';

        // Update Charts
        const rHist = await fetch('/api/history?limit=36');
        const hist = await rHist.json();
        const labels = hist.map(h => (h.timestamp ? h.timestamp.split(' ')[1] : ''));

        battChart.data.labels = labels;
        battChart.data.datasets[0].data = hist.map(h => h.battery_voltage);
        battChart.update();

        soilVpdChart.data.labels = labels;
        soilVpdChart.data.datasets[0].data = hist.map(h => h.soil_moisture);
        soilVpdChart.data.datasets[1].data = hist.map(h => h.vpd);
        soilVpdChart.update();

      } catch(e) {
        console.error("Dashboard refresh error:", e);
      }
    }

    window.onload = () => {
      initCharts();
      refresh();
      loadCurrentConfig();
      setInterval(refresh, 15000);
    };
    
    async function loadCurrentConfig() {
  const res = await fetch('/api/config');
  const d = await res.json();
  document.getElementById('cfg_low_batt').value = d.low_batt;
  document.getElementById('cfg_night_lux').value = d.night_lux;
  document.getElementById('cfg_day_sleep').value = d.day_sleep;
  document.getElementById('cfg_night_sleep').value = d.night_sleep;
}

async function saveRemoteConfig() {
  const payload = {
    low_batt: parseFloat(document.getElementById('cfg_low_batt').value),
    night_lux: parseFloat(document.getElementById('cfg_night_lux').value),
    day_sleep: parseInt(document.getElementById('cfg_day_sleep').value),
    night_sleep: parseInt(document.getElementById('cfg_night_sleep').value)
  };
  await fetch('/api/config/update', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(payload)
  });
  alert('Configuration pushed! ESP will synchronize on next wake.');
}
window.onload = () => { loadCurrentConfig(); };
    
    
  </script>
</body>
</html>
"""

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=7000)