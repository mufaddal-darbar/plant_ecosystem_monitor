import math
from datetime import datetime

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
            if 0.05 <= dt_hours <= 3.0:
                batt_rate_mvh = round(((current_v - prev_row[1]) * 1000.0) / dt_hours, 1)
                soil_diff = prev_row[2] - current_soil
                soil_depletion = round((soil_diff / dt_hours) * 24.0, 1)
        except Exception as e:
            print("[Agronomy] Derivative calculation error:", e)

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
            ppfd = avg_lux * 0.0185
            total_dli += (ppfd * dt_sec) / 1_000_000.0
    elif len(today_records) == 1:
        total_dli = (today_records[0][1] * 0.0185 * 600) / 1_000_000.0

    return batt_rate_mvh, max(0.0, soil_depletion), round(total_dli, 2)
