"""Unit tests for the Telegram helper + settings, using a local mock Bot API."""
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))

import settings  # noqa: E402
import telegram_bot  # noqa: E402

TOKEN = "123456789:AAFakeTokenForTestsOnly_abcdefgh"
SEEN = {}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _reply(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        self.do_GET()

    def do_GET(self):
        parsed = urlparse(self.path)
        method = parsed.path.rsplit("/", 1)[-1]
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode() if length else ""
        body = {k: v[0] for k, v in parse_qs(raw).items()} if raw else {}
        body.update({k: v[0] for k, v in parse_qs(parsed.query).items()})
        SEEN[method] = body

        if method == "getMe":
            self._reply({"ok": True, "result": {"id": 1, "username": "my_test_bot",
                                                "is_bot": True, "first_name": "T"}})
        elif method == "getUpdates":
            self._reply({"ok": True, "result": [
                {"update_id": 1, "message": {"chat": {"id": 42, "first_name": "Ali"}}},
                {"update_id": 2, "message": {"chat": {"id": 42, "first_name": "Ali"}}},
            ]})
        elif method == "sendMessage":
            if body.get("text") == "boom":
                self._reply({"ok": False, "error_code": 403,
                             "description": "Forbidden: bot was blocked by the user"}, 403)
            else:
                self._reply({"ok": True, "result": {"message_id": 7}})
        else:
            self._reply({"ok": False, "error_code": 404, "description": "Not Found"}, 404)


def main():
    srv = HTTPServer(("127.0.0.1", 0), Handler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    telegram_bot.API_ROOT = "http://127.0.0.1:%d/bot{token}/{method}" % port
    failures = []

    def check(name, cond, extra=""):
        print(("  OK  " if cond else "  FAIL") + "  " + name + ("  " + extra if extra else ""))
        if not cond:
            failures.append(name)

    try:
        me = telegram_bot.get_me(TOKEN)
        check("getMe returns bot username", me.get("username") == "my_test_bot", str(me))

        chat_id, title = telegram_bot.find_chat_id(TOKEN)
        check("getUpdates -> chat id", chat_id == "42", "%s / %s" % (chat_id, title))

        telegram_bot.send_message(TOKEN, "42", "hello", "باز کن", "https://x.trycloudflare.com")
        sent = SEEN.get("sendMessage", {})
        check("sendMessage passes chat_id", sent.get("chat_id") == "42", str(sent.get("chat_id")))
        check("sendMessage passes text", sent.get("text") == "hello")
        markup = json.loads(sent.get("reply_markup") or "{}")
        check("inline button carries the URL",
              markup.get("inline_keyboard", [[{}]])[0][0].get("url", "").startswith("https://"))

        try:
            telegram_bot.send_message(TOKEN, "42", "boom")
            check("403 raised as TelegramError", False)
        except telegram_bot.TelegramError as exc:
            check("403 raised as TelegramError", "403" in str(exc) or "مسدود" in str(exc), str(exc))

        try:
            telegram_bot.get_me("bad-token")
            check("malformed token rejected", False)
        except telegram_bot.TelegramError as exc:
            check("malformed token rejected", True, str(exc))

        # settings round-trip in a temp folder
        tmp = os.path.join(os.environ.get("TEMP", "."), "vnc_link_cfgtest")
        os.makedirs(tmp, exist_ok=True)
        orig = settings.settings_path
        settings.settings_path = lambda: os.path.join(tmp, "settings.json")
        payload = {"bot_token": TOKEN, "chat_id": "42", "auto_send": False,
                   "remember_password": True, "password": "Abc12345", "ws_port": 6080}
        settings.save_settings(payload)
        loaded = settings.load_settings()
        check("settings round-trip", all(loaded[k] == v for k, v in payload.items()), str(loaded))
        settings.settings_path = orig
    finally:
        srv.shutdown()

    print("\n%d failure(s)" % len(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
