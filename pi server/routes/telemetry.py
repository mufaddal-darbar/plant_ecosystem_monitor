from datetime import datetime
from flask import Blueprint, request, jsonify
from database import get_db
from agronomy import compute_instant_metrics, compute_derivative_metrics

telemetry_bp = Blueprint('telemetry_bp', __name__)

@telemetry_bp.route('/api/telemetry', methods=['POST'])
def receive_data():
    data = request.get_json() or {}

    temp = float(data.get('temperature', 0.0))
    hum = float(data.get('humidity', 0.0))
    current_v = float(data.get('battery_voltage', 0.0))
    batt_pct = int(data.get('battery_percent', 0))
    current_soil = float(data.get('soil_moisture', 0.0))
    lux = float(data.get('lux', 0.0))
    rssi = int(data.get('wifi_rssi', -70))
    charging = bool(data.get('charging', False))
    pump_triggered = bool(data.get('pump_triggered', False))
    esp_version = int(data.get('config_ver', 0))

    incoming_ts = data.get('timestamp')
    if not incoming_ts or "--" in incoming_ts:
        incoming_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    vpd, dew = compute_instant_metrics(temp, hum)

    with get_db() as conn:
        batt_rate, soil_rate, dli = compute_derivative_metrics(conn, current_v, current_soil, lux)
        conn.execute('''
            INSERT INTO telemetry (
                timestamp, battery_voltage, battery_percent, charging, 
                temperature, humidity, soil_moisture, pump_triggered,
                lux, wifi_rssi, vpd, dew_point, batt_rate_mvh,
                soil_depletion_rate, dli
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            incoming_ts, current_v, batt_pct, charging, temp, hum,
            current_soil, pump_triggered, lux, rssi, vpd, dew,
            batt_rate, soil_rate, dli
        ))

        # Check configuration version
        cfg = conn.execute("SELECT low_batt, night_lux, day_sleep, night_sleep, config_version FROM device_config WHERE id = 1").fetchone()

    server_ver = int(cfg['config_version'])
    resp = {"status": "success"}

    # Attach configuration ONLY if the ESP node is running an older version
    if esp_version < server_ver:
        resp["config"] = dict(cfg)

    return jsonify(resp), 200

@telemetry_bp.route('/api/latest', methods=['GET'])
def get_latest():
    with get_db() as conn:
        row = conn.execute("SELECT * FROM telemetry ORDER BY id DESC LIMIT 1").fetchone()
        return jsonify(dict(row)) if row else jsonify({})

@telemetry_bp.route('/api/history', methods=['GET'])
def get_history():
    limit = request.args.get('limit', default=48, type=int)
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM (SELECT * FROM telemetry ORDER BY id DESC LIMIT ?) ORDER BY id ASC", (limit,)).fetchall()
        return jsonify([dict(r) for r in rows])
