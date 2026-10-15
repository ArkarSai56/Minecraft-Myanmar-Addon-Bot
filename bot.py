
import json
import logging
import os
import re

import requests
from flask import Flask, request, jsonify

# ----- Configuration from Render environment variables -----

TOKEN = os.environ["BOT_TOKEN"]
ADMIN_ID = os.environ.get("ADMIN_ID", "").strip()
WEBHOOK_SECRET = os.environ["WEBHOOK_SECRET"]
PUBLIC_URL = os.environ.get("RENDER_EXTERNAL_URL", "").rstrip("/")

# Example:
# {"abc123":{"file_id":"TELEGRAM_FILE_ID","filename":"addon.mcaddon"}}
try:
    DOWNLOADS = json.loads(os.environ.get("FILES_JSON", "{}"))
except json.JSONDecodeError:
    raise RuntimeError("FILES_JSON must contain valid JSON")

API = f"https://api.telegram.org/bot{TOKEN}"
app = Flask(__name__)
logging.basicConfig(level=logging.INFO)


def telegram(method, data=None, files=None):
    """Call the Telegram Bot API."""
    response = requests.post(
        f"{API}/{method}",
        data=data,
        files=files,
        timeout=60,
    )
    response.raise_for_status()
    result = response.json()

    if not result.get("ok"):
        raise RuntimeError(result.get("description", "Telegram API error"))

    return result["result"]


def send_message(chat_id, text):
    telegram("sendMessage", {
        "chat_id": chat_id,
        "text": text,
    })


def send_document(chat_id, file_id, filename):
    telegram("sendDocument", {
        "chat_id": chat_id,
        "document": file_id,
        "caption": f"Your Minecraft add-on: {filename}",
    })


def is_admin(user_id):
    return bool(ADMIN_ID) and str(user_id) == ADMIN_ID


def process_update(update):
    message = update.get("message")
    if not message:
        return

    chat_id = message["chat"]["id"]
    user_id = message["from"]["id"]
    text = message.get("text", "").strip()

    # Let users find their Telegram numeric ID.
    if text == "/id":
        send_message(
            chat_id,
            f"Your Telegram user ID is: {user_id}\n"
            "Only share this ID when needed; never share your bot token."
        )
        return

    # Admin uploads a file to discover its reusable Telegram file_id.
    document = message.get("document")
    if document:
        if not is_admin(user_id):
            send_message(chat_id, "Only the bot admin can register files.")
            return

        filename = document.get("file_name", "addon")
        extension = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""

        if extension not in {"mcaddon", "mcpack"}:
            send_message(chat_id, "Please upload a .mcaddon or .mcpack file.")
            return

        send_message(
            chat_id,
            "File received!\n\n"
            f"Filename: {filename}\n"
            f"File ID:\n{document['file_id']}\n\n"
            "Copy this file ID into the FILES_JSON environment variable "
            "on Render, mapped to the code you want."
        )
        return

    if not text:
        return

    command = text.split()[0].split("@")[0].lower()

    if command == "/start":
        send_message(
            chat_id,
            "Welcome to Minecraft Add-on Downloads!\n\n"
            "Download an add-on with:\n"
            "/get abc123\n\n"
            "Send /help for instructions."
        )
        return

    if command == "/help":
        send_message(
            chat_id,
            "Send /get followed by your download code.\n"
            "Example: /get abc123"
        )
        return

    if command == "/get":
        parts = text.split(maxsplit=1)

        if len(parts) != 2:
            send_message(chat_id, "Usage: /get abc123")
            return

        code = parts[1].strip().lower()

        # Keep codes simple and prevent unexpected input.
        if not re.fullmatch(r"[a-z0-9_-]{1,40}", code):
            send_message(chat_id, "That download code is invalid.")
            return

        item = DOWNLOADS.get(code)
        if not isinstance(item, dict):
            send_message(chat_id, "Unknown download code.")
            return

        file_id = item.get("file_id", "")
        filename = item.get("filename", "")

        if (
            not isinstance(file_id, str)
            or not file_id
            or not isinstance(filename, str)
            or filename.lower().rsplit(".", 1)[-1]
                not in {"mcaddon", "mcpack"}
        ):
            send_message(chat_id, "This download is not configured correctly.")
            return

        try:
            send_document(chat_id, file_id, filename)
        except Exception:
            logging.exception("File delivery failed")
            send_message(
                chat_id,
                "Telegram couldn't send this file. Please try again later."
            )
        return

    send_message(chat_id, "Unknown command. Try /help.")


@app.get("/")
def health():
    return jsonify({"status": "Minecraft add-on bot is running"})


@app.post("/webhook")
def webhook():
    # Reject requests that do not contain Telegram's webhook secret.
    received_secret = request.headers.get(
        "X-Telegram-Bot-Api-Secret-Token", ""
    )
    if received_secret != WEBHOOK_SECRET:
        return jsonify({"error": "forbidden"}), 403

    update = request.get_json(silent=True)
    if not isinstance(update, dict):
        return jsonify({"error": "invalid update"}), 400

    try:
        process_update(update)
    except Exception:
        logging.exception("Could not process Telegram update")

    # Acknowledge the update to Telegram.
    return jsonify({"ok": True})


if __name__ == "__main__":
    if not PUBLIC_URL.startswith("https://"):
        raise RuntimeError(
            "RENDER_EXTERNAL_URL must be set to your Render HTTPS URL."
        )

    if not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", WEBHOOK_SECRET):
        raise RuntimeError(
            "WEBHOOK_SECRET must use only letters, numbers, _ or -."
        )

    # Configure Telegram to deliver messages to this Render service.
    result = telegram("setWebhook", {
        "url": f"{PUBLIC_URL}/webhook",
        "secret_token": WEBHOOK_SECRET,
        "allowed_updates": json.dumps(["message"]),
    })
    logging.info("Webhook configured: %s", result)

    port = int(os.environ.get("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
