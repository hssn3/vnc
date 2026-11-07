"""Minimal Telegram Bot API helper (standard library only).

Only three endpoints are used: getMe (token check), getUpdates (auto-discover
the chat id) and sendMessage (deliver the link + password).
"""
import json
import re
import urllib.error
import urllib.parse
import urllib.request

API_ROOT = "https://api.telegram.org/bot{token}/{method}"
USER_AGENT = "VNC-Link/1.3 (+https://github.com/atomich000w/vnc-link)"
TOKEN_RE = re.compile(r"^\d{5,}:([A-Za-z0-9_-]{30,})$")
_JUNK_RE = re.compile(r"[\s\u200b\u200c\u200d\u00a0\ufeff]+")


def clean_token(value):
    """Strip spaces / zero-width characters / a leading "bot" keyword."""
    token = _JUNK_RE.sub("", value or "")
    if token.lower().startswith("bot") and ":" in token:
        token = token[3:]
    return token


class TelegramError(Exception):
    """Raised with a message that is safe to show to the user."""


def _http(url, payload=None, timeout=25):
    data = None
    if payload is not None:
        data = urllib.parse.urlencode(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        raw = exc.read()
    except urllib.error.URLError as exc:
        raise TelegramError(
            "اتصال به تلگرام برقرار نشد: مشکل اینترنت یا فیلترینگ. (%s)" % exc.reason
        )
    except TimeoutError:
        raise TelegramError("پاسخی از تلگرام دریافت نشد (timeout). اینترنت را بررسی کنید.")
    try:
        return json.loads(raw.decode("utf-8", "replace"))
    except ValueError:
        raise TelegramError("پاسخ نامعتبر از تلگرام دریافت شد.")


def _call(token, method, payload=None, timeout=25):
    token = clean_token(token)
    if not token:
        raise TelegramError("توکن ربات وارد نشده است.")
    if not TOKEN_RE.match(token):
        raise TelegramError("قالب توکن ربات درست نیست (مثال: 123456789:AA...).")
    url = API_ROOT.format(token=token, method=method)
    result = _http(url, payload, timeout=timeout)
    if not isinstance(result, dict):
        raise TelegramError("پاسخ نامعتبر از تلگرام دریافت شد.")
    if not result.get("ok"):
        code = result.get("error_code")
        desc = result.get("description") or "خطای نامشخص"
        if code == 401:
            raise TelegramError("توکن ربات نامعتبر است یا باطل شده (401).")
        if code == 403:
            raise TelegramError(
                "ربات اجازه ارسال به این چت را ندارد (403). ابتدا در ربات /start بزنید."
            )
        if code == 400 and "chat not found" in desc.lower():
            raise TelegramError("چت آی‌دی پیدا نشد (400). یک بار در ربات /start بزنید.")
        if code == 409:
            raise TelegramError("تداخل در دریافت پیام‌ها (409). چند لحظه بعد دوباره تلاش کنید.")
        if code == 429:
            raise TelegramError("محدودیت نرخ تلگرام (429). کمی بعد دوباره تلاش کنید.")
        raise TelegramError("خطای تلگرام %s: %s" % (code, desc))
    return result.get("result")


def get_me(token):
    """Return the bot info dict; raises TelegramError when the token is bad."""
    return _call(token, "getMe", timeout=20) or {}


def find_chat_id(token):
    """Pick the most recent chat id that talked to the bot.

    Returns (chat_id, title) or (None, None) when nobody has started the bot yet.
    """
    updates = _call(token, "getUpdates", {"timeout": 0, "limit": 100}, timeout=25) or []
    best = None
    for upd in updates:
        msg = (
            upd.get("message")
            or upd.get("edited_message")
            or upd.get("channel_post")
            or upd.get("my_chat_member")
        )
        if not msg:
            continue
        chat = msg.get("chat") or {}
        if chat.get("id") is None:
            continue
        best = chat  # keep the newest one
    if not best:
        return None, None
    title = (
        best.get("title")
        or best.get("username")
        or " ".join(filter(None, [best.get("first_name"), best.get("last_name")]))
        or str(best.get("id"))
    )
    return str(best.get("id")), title


def send_message(token, chat_id, text, button_text=None, button_url=None):
    """Send a message; optionally attach a single URL button under it."""
    chat_id = str(chat_id or "").strip()
    if not chat_id:
        raise TelegramError("چت آی‌دی خالی است؛ اول دکمه «دریافت خودکار» را بزنید.")
    payload = {"chat_id": chat_id, "text": text, "disable_web_page_preview": "true"}
    if button_url:
        payload["reply_markup"] = json.dumps(
            {"inline_keyboard": [[{"text": button_text or "اتصال", "url": button_url}]]}
        )
    return _call(token, "sendMessage", payload, timeout=30)
