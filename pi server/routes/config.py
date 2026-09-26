from flask import Blueprint, request, jsonify
from database import get_db

config_bp = Blueprint('config_bp', __name__)

@config_bp.route('/api/config', methods=['GET'])
def get_device_config():
    with get_db() as conn:
        row = conn.execute("SELECT low_batt, night_lux, day_sleep, night_sleep, config_version FROM device_config WHERE id = 1").fetchone()
        return jsonify(dict(row)), 200

@config_bp.route('/api/config/update', methods=['POST'])
def update_device_config():
    data = request.get_json() or {}
    low_batt = float(data.get('low_batt', 3.65))
    night_lux = float(data.get('night_lux', 50.0))
    day_sleep = int(data.get('day_sleep', 10))
    night_sleep = int(data.get('night_sleep', 30))

    with get_db() as conn:
        conn.execute('''
            UPDATE device_config 
            SET low_batt = ?, night_lux = ?, day_sleep = ?, night_sleep = ?, config_version = config_version + 1, updated_at = CURRENT_TIMESTAMP
            WHERE id = 1
        ''', (low_batt, night_lux, day_sleep, night_sleep))

    return jsonify({"status": "updated"}), 200
