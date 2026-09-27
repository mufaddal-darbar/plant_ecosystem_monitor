import json
import sqlite3
import requests
from database import get_db
from notifier import send_telegram_message

# Coordinates (Set to your plant's geographic location)
LATITUDE = 23.58      # Example: Rajasthan / MP region
LONGITUDE = 74.17

GROQ_API_KEY = <your_api_key>
GROQ_ENDPOINT = "https://api.groq.com/openai/v1/chat/completions"
MODEL_NAME = "openai/gpt-oss-20b"

def get_weather_forecast():
    """Fetches next 24h solar radiation, cloud cover, and precipitation."""
    url = (
        f"https://api.open-meteo.com/v1/forecast?"
        f"latitude={LATITUDE}&longitude={LONGITUDE}"
        f"&hourly=precipitation_probability,precipitation,cloudcover,direct_normal_irradiance"
        f"&forecast_days=1&timezone=auto"
    )
    try:
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            data = r.json().get('hourly', {})
            # Aggregate next 24 hours
            total_rain = sum(data.get('precipitation', [0]))
            avg_clouds = sum(data.get('cloudcover', [0])) / max(len(data.get('cloudcover', [1])), 1)
            avg_irradiance = sum(data.get('direct_normal_irradiance', [0])) / max(len(data.get('direct_normal_irradiance', [1])), 1)
            
            return {
                "total_rain_mm": round(total_rain, 1),
                "avg_cloudcover_pct": round(avg_clouds, 1),
                "avg_solar_irradiance_wm2": round(avg_irradiance, 1)
            }
    except Exception as e:
        print(f"[Agent] Weather fetch error: {e}")
    return None

def fetch_system_state():
    """Gathers current configuration and 24h hardware/soil trends."""
    with get_db() as conn:
        cfg = dict(conn.execute("SELECT * FROM device_config WHERE id = 1").fetchone())
        
        # Last 12 readings (~3-6 hours)
        rows = conn.execute("""
            SELECT battery_voltage, batt_rate_mvh, soil_moisture, 
                   soil_depletion_rate, vpd, lux 
            FROM telemetry ORDER BY id DESC LIMIT 12
        """).fetchall()

    if not rows:
        return None, None

    records = [dict(r) for r in reversed(rows)]
    latest = records[-1]
    
    return cfg, latest

def run_autonomous_agent():
    cfg, latest = fetch_system_state()
    if not latest:
        print("[Agent] No telemetry available yet. Aborting run.")
        return

    weather = get_weather_forecast()
    weather_summary = (
        f"Rain Expected: {weather['total_rain_mm']}mm, "
        f"Cloud Cover: {weather['avg_cloudcover_pct']}%, "
        f"Avg Solar Irradiance: {weather['avg_solar_irradiance_wm2']} W/m²"
        if weather else "Weather data unavailable."
    )

    prompt = f"""
You are an autonomous IoT closed-loop controller managing an ESP-12F solar-powered plant monitor.

CURRENT SYSTEM SETTINGS:
- Low Battery Threshold: {cfg['low_batt']} V
- Night Lux Cutoff: {cfg['night_lux']} Lux
- Day Sleep: {cfg['day_sleep']} min
- Night Sleep: {cfg['night_sleep']} min

CURRENT SENSORS & TELEMETRY:
- Battery: {latest['battery_voltage']}V (Net rate: {latest['batt_rate_mvh']} mV/h)
- Soil Moisture: {latest['soil_moisture']}% (Depletion: {latest['soil_depletion_rate']}%/day)
- VPD: {latest['vpd']} kPa | Current Lux: {latest['lux']} Lux

24-HOUR WEATHER OUTLOOK:
{weather_summary}

DECISION RULES:
1. If tomorrow is cloudy/rainy or battery is dropping below 3.75V, lengthen 'day_sleep' (e.g. 15-30m) to preserve energy.
2. If abundant sunshine is forecasted and battery is full (>4.0V), you can shorten 'day_sleep' (e.g. 8-10m) for higher fidelity telemetry.
3. If heavy rain is forecast and plant is outdoors, soil will saturate naturally; do not shorten daytime monitoring unnecessarily.
4. If current parameters are already optimal, keep them unchanged and set "thresholds_modified": false.

Respond ONLY with this valid JSON schema:
{{
  "reasoning": "2 concise sentences explaining why parameters were adjusted or kept.",
  "thresholds_modified": true/false,
  "suggested_config": {{
    "low_batt": float,
    "night_lux": float,
    "day_sleep": int,
    "night_sleep": int
  }}
}}
"""

    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": MODEL_NAME,
        "messages": [
            {"role": "system", "content": "You are a precision IoT energy and irrigation control agent outputting strict JSON."},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.1,
        "response_format": {"type": "json_object"}
    }

    try:
        res = requests.post(GROQ_ENDPOINT, headers=headers, json=payload, timeout=12)
        if res.status_code != 200:
            print(f"[Agent] Groq API error: {res.text}")
            return

        decision = json.loads(res.json()['choices'][0]['message']['content'])
        reasoning = decision.get("reasoning", "")
        modified = decision.get("thresholds_modified", False)
        new_cfg = decision.get("suggested_config", {})

        print(f"[Agent Decision] Modified: {modified} | Reason: {reasoning}")

        if modified and new_cfg:
            # Commit autonomous decision directly to SQLite
            with get_db() as conn:
                conn.execute("""
                    UPDATE device_config 
                    SET low_batt = ?, night_lux = ?, day_sleep = ?, night_sleep = ?, 
                        config_version = config_version + 1, updated_at = CURRENT_TIMESTAMP
                    WHERE id = 1
                """, (
                    float(new_cfg.get('low_batt', cfg['low_batt'])),
                    float(new_cfg.get('night_lux', cfg['night_lux'])),
                    int(new_cfg.get('day_sleep', cfg['day_sleep'])),
                    int(new_cfg.get('night_sleep', cfg['night_sleep']))
                ))

            # Dispatch change event to Telegram
            alert = (
                f"🤖 *Autonomous Agent Adjustment*\n\n"
                f"*Reason:* {reasoning}\n\n"
                f"⚙️ *New Sleep Cycles:*\n"
                f"• Day Sleep: `{new_cfg.get('day_sleep')} min`\n"
                f"• Night Sleep: `{new_cfg.get('night_sleep')} min`\n"
                f"• Low Batt Cutoff: `{new_cfg.get('low_batt')} V`\n\n"
                f"📡 *Downlink:* Version bumped. Node will update on next wake."
            )
            send_telegram_message(alert)
        else:
            print("[Agent] Thresholds remain optimal. No changes made.")

    except Exception as e:
        print(f"[Agent] Execution error: {e}")

if __name__ == "__main__":
    run_autonomous_agent()
