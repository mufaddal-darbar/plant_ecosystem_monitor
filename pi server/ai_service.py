import json
import sqlite3
import requests
from database import get_db
from notifier import send_telegram_message

# Get your free key from https://console.groq.com/
GROQ_API_KEY = <your_api_key>
GROQ_ENDPOINT = "https://api.groq.com/openai/v1/chat/completions"
MODEL_NAME = "openai/gpt-oss-20b"

def gather_context_window(limit=24):
    """Fetches rolling historical readings from SQLite for agronomic context."""
    with get_db() as conn:
        rows = conn.execute("""
            SELECT timestamp, battery_voltage, temperature, humidity, 
                   soil_moisture, lux, vpd, batt_rate_mvh, soil_depletion_rate
            FROM telemetry 
            ORDER BY id DESC LIMIT ?
        """, (limit,)).fetchall()
        
    if not rows:
        return None

    # Compute high-level summary metrics
    records = [dict(r) for r in reversed(rows)]
    latest = records[-1]
    
    avg_soil = round(sum(r['soil_moisture'] for r in records) / len(records), 1)
    max_temp = round(max(r['temperature'] for r in records), 1)
    avg_vpd = round(sum(r['vpd'] for r in records) / len(records), 2)
    max_lux = round(max(r['lux'] for r in records), 0)
    
    return {
        "latest": latest,
        "history_summary": {
            "avg_soil_moisture": avg_soil,
            "max_ambient_temp": max_temp,
            "avg_vpd": avg_vpd,
            "max_lux": max_lux,
            "points_sampled": len(records)
        }
    }

def analyze_plant_health():
    context = gather_context_window(limit=24)
    if not context:
        return {"error": "Insufficient telemetry records for analysis"}

    prompt = f"""
    You are an expert autonomous agronomy and IoT embedded power diagnostics AI.
    Analyze this solar-powered IoT plant monitor telemetry:

    ELECTRICAL & POWER METRICS:
    - Battery Voltage: {context['latest']['battery_voltage']}V (Net Rate: {context['latest']['batt_rate_mvh']} mV/h)
    - Light Level: {context['latest']['lux']} Lux (Peak 24h: {context['history_summary']['max_lux']} Lux)

    AGRONOMIC METRICS:
    - Soil Moisture: {context['latest']['soil_moisture']}% (Depletion Rate: {context['latest']['soil_depletion_rate']}%/day)
    - Temperature: {context['latest']['temperature']}°C | Humidity: {context['latest']['humidity']}%
    - Vapor Pressure Deficit (VPD): {context['latest']['vpd']} kPa

    Respond ONLY with a valid JSON object matching this schema:
    {{
      "summary": "2 sentences analyzing plant physiological stress, VPD, and soil depletion.",
      "irrigation_advice": "Actionable watering recommendation.",
      "power_advice": "1-2 sentences analyzing solar charge rate vs battery depletion, recommending whether sleep intervals should be tightened or relaxed.",
      "anomaly_detected": true/false
    }}
    """

    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "model": MODEL_NAME,
        "messages": [
            {"role": "system", "content": "You are a concise agronomy IoT analyzer that outputs strict JSON."},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.2,
        "response_format": {"type": "json_object"}
    }

    try:
        res = requests.post(GROQ_ENDPOINT, headers=headers, json=payload, timeout=10)
        if res.status_code == 200:
            result = res.json()['choices'][0]['message']['content']
            parsed = json.loads(result)
            
            # Store insight into SQLite
            with get_db() as conn:
                conn.execute("""
                    INSERT INTO ai_insights (summary, irrigation_advice, power_advice, anomaly_detected, model_used)
                    VALUES (?, ?, ?, ?, ?)
                """, (
                    parsed.get("summary", ""),
                    parsed.get("irrigation_advice", ""),
                    parsed.get("power_advice", ""),
                    1 if parsed.get("anomaly_detected") else 0,
                    MODEL_NAME
                ))
            # Format clean alert
            is_anomaly = parsed.get("anomaly_detected", False)
            status_icon = "⚠️ *ANOMALY DETECTED*" if is_anomaly else "🌱 *Plant Health Diagnostic*"

            tg_message = (
                f"{status_icon}\n\n"
                f"*Summary:* {parsed.get('summary')}\n\n"
                f"💧 *Irrigation:* {parsed.get('irrigation_advice')}\n\n"
                f"🔋 *Power & Solar:* {parsed.get('power_advice')}\n\n"
                f"📊 *Snapshot:* Soil {context['latest']['soil_moisture']}% | "
                f"VPD {context['latest']['vpd']} kPa | "
                f"Batt {context['latest']['battery_voltage']}V ({context['latest']['batt_rate_mvh']} mV/h)"
            )
            send_telegram_message(tg_message)
            return parsed
        else:
            return {"error": f"API returned status {res.status_code}: {res.text}"}
    except Exception as e:
        return {"error": str(e)}
