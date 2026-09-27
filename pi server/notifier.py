import requests

TELEGRAM_BOT_TOKEN = "8762455798:AAHW4FRDlVwimqKGn15bviXBJR81_uiBmiM"
TELEGRAM_CHAT_ID = "7233689169"

def send_telegram_message(message: str) -> bool:
    """Sends a markdown-formatted message via Telegram Bot API."""
    if not TELEGRAM_BOT_TOKEN or "YOUR_BOT_TOKEN" in TELEGRAM_BOT_TOKEN:
        print("[Notifier] Telegram credentials not configured.")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True
    }

    try:
        res = requests.post(url, json=payload, timeout=5)
        return res.status_code == 200
    except Exception as e:
        print(f"[Notifier] Telegram dispatch error: {e}")
        return False
