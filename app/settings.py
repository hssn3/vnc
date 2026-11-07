"""Portable settings storage.

settings.json lives next to the executable (portable behaviour: copy the folder /
exe anywhere and the settings travel with it).  If that location is not
writable (e.g. the exe was started from a read-only share) we fall back to
%LOCALAPPDATA%\\VNC-Link.
"""
import json
import os
import sys
import tempfile

# پیش‌فرض کارخانه: ربات تلگرام از پیش تنظیم‌شده
DEFAULT_BOT_TOKEN = "REDACTED"
DEFAULT_CHAT_ID = "203439171"

DEFAULTS = {
    "bot_token": DEFAULT_BOT_TOKEN,
    "chat_id": DEFAULT_CHAT_ID,
    "auto_send": True,
    "remember_password": True,
    "password": "",
    "ws_port": 6080,
}


def _base_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def app_dir():
    base = _base_dir()
    probe = os.path.join(base, ".write-test")
    try:
        with open(probe, "w", encoding="utf-8") as fh:
            fh.write("ok")
        os.remove(probe)
        return base
    except OSError:
        fallback = os.path.join(
            os.environ.get("LOCALAPPDATA") or tempfile.gettempdir(), "VNC-Link"
        )
        os.makedirs(fallback, exist_ok=True)
        return fallback


def settings_path():
    return os.path.join(app_dir(), "settings.json")


def load_settings():
    data = dict(DEFAULTS)
    try:
        with open(settings_path(), "r", encoding="utf-8") as fh:
            saved = json.load(fh)
    except (OSError, ValueError):
        return data
    if isinstance(saved, dict):
        for key in DEFAULTS:
            if key in saved:
                data[key] = saved[key]
    if not data.get("bot_token"):
        # keep the built-in bot even if the field was cleared
        data["bot_token"] = DEFAULT_BOT_TOKEN
    if not data.get("chat_id"):
        data["chat_id"] = DEFAULT_CHAT_ID
    return data


def save_settings(data):
    path = settings_path()
    payload = {key: data.get(key, DEFAULTS[key]) for key in DEFAULTS}
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    return path
