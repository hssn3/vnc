"""VNC Link — portable GUI app: share this desktop through a Cloudflare link.

Run it, type a password, press "ایجاد لینک": the app starts a loopback-only
TightVNC server, bridges it to the browser with noVNC/websockify and publishes
it through a Cloudflare quick tunnel.  The resulting link (+ password) is
optionally delivered to a Telegram bot.
"""
import os
import queue
import random
import string
import sys
import threading
import time
import tkinter as tk
import webbrowser
from tkinter import messagebox, ttk

import telegram_bot
from core import RemoteLink
from settings import load_settings, save_settings, settings_path

VERSION = "1.1.0"
PASSWORD_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"


# --------------------------------------------------------------------------- #
# child-process entry points
# --------------------------------------------------------------------------- #
def run_websockify():
    # args: --run-websockify <webroot> <listen_port> <target_port>
    webroot, listen_port, target_port = sys.argv[2:5]
    sys.argv = ["websockify", "--web", webroot,
                f"127.0.0.1:{listen_port}", f"127.0.0.1:{target_port}"]

    # websockify spawns a multiprocessing.Process per connection; on Windows
    # (and inside a frozen exe) spawn is unreliable, so run handlers as threads.
    # The listener closes its socket copy right after "spawn", so each thread
    # needs a duplicated socket handle of its own.
    import socket as _socket
    import websockify.websockifyserver as wss

    class _ThreadProcess:
        _children = []

        def __init__(self, target=None, args=()):
            self._socks = []
            dup_args = []
            for a in args:
                if isinstance(a, _socket.socket):
                    d = a.dup()
                    self._socks.append(d)
                    dup_args.append(d)
                else:
                    dup_args.append(a)

            def runner():
                try:
                    target(*dup_args)
                finally:
                    for s in self._socks:
                        try:
                            s.close()
                        except OSError:
                            pass

            self._thread = threading.Thread(target=runner, daemon=True)
            self.pid = id(self)

        def start(self):
            self._thread.start()
            _ThreadProcess._children.append(self)

        def terminate(self):
            pass

    class _MPShim:
        Process = _ThreadProcess

        @staticmethod
        def active_children():
            _ThreadProcess._children = [c for c in _ThreadProcess._children if c._thread.is_alive()]
            return _ThreadProcess._children

    wss.multiprocessing = _MPShim
    import websockify.websocketproxy
    websockify.websocketproxy.websockify_init()


def _arg(name, default=None):
    if name in sys.argv:
        i = sys.argv.index(name)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return default


def run_selftest(password, ws_port, out_path, stop_path):
    """Head-less mode used by the automated tests (and for remote smoke checks)."""
    def emit(msg):
        try:
            print("[log]", msg, flush=True)
        except Exception:
            pass

    link = RemoteLink(log=emit)
    try:
        url = link.start(password, ws_port)
        line = "LINK=" + url
        try:
            print(line, flush=True)
        except Exception:
            pass
        if out_path:
            with open(out_path, "w", encoding="utf-8") as fh:
                fh.write(line + "\n")
        stop = threading.Event()
        if not stop_path:
            def watch_stdin():
                # only meaningful when a real stdin (pipe/console) is attached;
                # a windowed build has sys.stdin = None and must not exit early.
                if sys.stdin is None:
                    return
                try:
                    sys.stdin.read()
                except Exception:
                    return
                stop.set()

            threading.Thread(target=watch_stdin, daemon=True).start()

        while not stop.is_set():
            if stop_path and os.path.exists(stop_path):
                break
            time.sleep(0.5)
    except Exception as exc:  # keep the reason visible to the test harness
        if out_path:
            with open(out_path, "w", encoding="utf-8") as fh:
                fh.write("ERROR=" + str(exc) + "\n")
        raise
    finally:
        link.stop()


# --------------------------------------------------------------------------- #
# GUI
# --------------------------------------------------------------------------- #
class App:
    def __init__(self, root):
        self.root = root
        self.link = RemoteLink(log=self._log_async)
        self.q = queue.Queue()
        self.busy = False
        self.cfg = load_settings()

        root.title(f"VNC Link {VERSION} — اشتراک ریموت دسکتاپ")
        root.geometry("800x720")
        root.minsize(720, 660)
        self.style = ttk.Style(root)
        try:
            self.style.theme_use("vista")
        except tk.TclError:
            pass
        root.option_add("*Font", ("Segoe UI", 10))

        outer = ttk.Frame(root, padding=10)
        outer.pack(fill="both", expand=True)

        self._build_share(outer)
        self._build_link(outer)
        self._build_telegram(outer)
        self._build_log(outer)
        self._build_footer(outer)

        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(3, weight=1)

        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.after(120, self._poll)
        self._log(f"VNC Link {VERSION} — آماده.")
        self._log("فایل تنظیمات: " + settings_path())
        if self.cfg.get("bot_token") and self.cfg.get("chat_id"):
            self.tg_state.configure(
                text="ذخیره‌شده: توکن و چت‌آی‌دی بارگذاری شد (برای اطمینان «تست اتصال» را بزنید).",
                foreground="#0a7")

    # ---------- layout ----------
    def _build_share(self, parent):
        box = ttk.LabelFrame(parent, text=" ۱) اشتراک‌گذاری دسکتاپ ", padding=10)
        box.grid(row=0, column=0, sticky="ew")
        box.columnconfigure(0, weight=1)

        ttk.Label(box, text="رمز عبور ورود:").grid(row=0, column=1, sticky="e", padx=(10, 0), pady=4)
        row = ttk.Frame(box)
        row.grid(row=0, column=0, sticky="ew", pady=4)
        self.pw_var = tk.StringVar(value=self.cfg.get("password") or "")
        self.pw_entry = ttk.Entry(row, textvariable=self.pw_var, show="*", width=22)
        self.pw_entry.pack(side="right")
        ttk.Button(row, text="نمایش", width=7,
                   command=lambda: self._toggle(self.pw_entry, "*")).pack(side="right", padx=6)
        ttk.Button(row, text="تصادفی", width=8, command=self._gen_password).pack(side="right")
        ttk.Label(box, text="۱ تا ۸ کاراکتر انگلیسی/عدد — محدودیت پروتکل VNC؛ همین رمز را به طرف مقابل بدهید.",
                  foreground="#666", wraplength=620, justify="right", anchor="e")\
            .grid(row=1, column=0, columnspan=2, sticky="ew")

        btns = ttk.Frame(box)
        btns.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        self.send_btn = ttk.Button(btns, text="ارسال به تلگرام", command=self.on_send_manual, state="disabled")
        self.send_btn.pack(side="right")
        self.stop_btn = ttk.Button(btns, text="توقف", command=self.on_stop, state="disabled")
        self.stop_btn.pack(side="right", padx=6)
        self.start_btn = ttk.Button(btns, text="ایجاد لینک", command=self.on_start)
        self.start_btn.pack(side="right")

    def _build_link(self, parent):
        box = ttk.LabelFrame(parent, text=" ۲) لینک اشتراک ", padding=10)
        box.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        box.columnconfigure(0, weight=1)
        self.url_var = tk.StringVar()
        self.url_entry = ttk.Entry(box, textvariable=self.url_var, state="readonly")
        self.url_entry.grid(row=0, column=0, sticky="ew")
        self.copy_btn = ttk.Button(box, text="کپی", width=6, command=self.on_copy, state="disabled")
        self.copy_btn.grid(row=0, column=1, padx=4)
        self.open_btn = ttk.Button(box, text="باز کردن", width=9, command=self.on_open, state="disabled")
        self.open_btn.grid(row=0, column=2)

    def _build_telegram(self, parent):
        box = ttk.LabelFrame(parent, text=" ۳) ربات تلگرام (اختیاری) ", padding=10)
        box.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        box.columnconfigure(1, weight=1)

        ttk.Label(box, text="توکن ربات:").grid(row=0, column=1, sticky="e", padx=(10, 0), pady=3)
        self.token_var = tk.StringVar(value=self.cfg.get("bot_token") or "")
        tok_row = ttk.Frame(box)
        tok_row.grid(row=0, column=0, sticky="ew", pady=3)
        self.token_entry = ttk.Entry(tok_row, textvariable=self.token_var, show="*")
        self.token_entry.pack(side="right", fill="x", expand=True)
        ttk.Button(tok_row, text="نمایش", width=7,
                   command=lambda: self._toggle(self.token_entry, "*")).pack(side="right", padx=6)

        ttk.Label(box, text="چت آی‌دی:").grid(row=1, column=1, sticky="e", padx=(10, 0), pady=3)
        self.chat_var = tk.StringVar(value=str(self.cfg.get("chat_id") or ""))
        chat_row = ttk.Frame(box)
        chat_row.grid(row=1, column=0, sticky="ew", pady=3)
        ttk.Entry(chat_row, textvariable=self.chat_var).pack(side="right", fill="x", expand=True)
        self.find_btn = ttk.Button(chat_row, text="دریافت خودکار", command=self.on_find_chat)
        self.find_btn.pack(side="right", padx=6)

        row = ttk.Frame(box)
        row.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(6, 2))
        self.remember_var = tk.BooleanVar(value=bool(self.cfg.get("remember_password")))
        ttk.Checkbutton(row, text="ذخیره رمز عبور", variable=self.remember_var).pack(side="right")
        self.auto_var = tk.BooleanVar(value=bool(self.cfg.get("auto_send")))
        ttk.Checkbutton(row, text="ارسال خودکار لینک پس از ساخت", variable=self.auto_var)\
            .pack(side="right", padx=10)
        self.test_btn = ttk.Button(row, text="تست اتصال", command=self.on_test_telegram)
        self.test_btn.pack(side="right")

        self.tg_state = ttk.Label(
            box, text="توکن را از @BotFather بگیرید، ربات را استارت کنید و «دریافت خودکار» را بزنید.",
            foreground="#666", wraplength=620, justify="right", anchor="e")
        self.tg_state.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(6, 0))

    def _build_log(self, parent):
        box = ttk.LabelFrame(parent, text=" گزارش ", padding=6)
        box.grid(row=3, column=0, sticky="nsew", pady=(8, 0))
        box.rowconfigure(0, weight=1)
        box.columnconfigure(0, weight=1)
        self.log_text = tk.Text(box, height=8, state="disabled", wrap="word",
                                font=("Consolas", 9), background="#1e1e1e", foreground="#d4d4d4")
        self.log_text.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(box, command=self.log_text.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=sb.set)

    def _build_footer(self, parent):
        bar = ttk.Frame(parent)
        bar.grid(row=4, column=0, sticky="ew", pady=(8, 0))
        bar.columnconfigure(0, weight=1)
        self.status_var = tk.StringVar(value="آماده")
        ttk.Label(bar, textvariable=self.status_var, foreground="#0a7").grid(row=0, column=0, sticky="w")
        ttk.Button(bar, text="ذخیره تنظیمات", command=self.on_save).grid(row=0, column=1, sticky="e")

    # ---------- helpers ----------
    def _toggle(self, widget, mask):
        widget.configure(show="" if widget.cget("show") else mask)

    def _gen_password(self):
        self.pw_var.set("".join(random.choice(PASSWORD_ALPHABET) for _ in range(8)))

    def _log_async(self, msg):
        self.q.put(("log", msg))

    def _log(self, msg):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", time.strftime("%H:%M:%S") + "  " + msg + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _collect(self):
        return {
            "bot_token": self.token_var.get().strip(),
            "chat_id": self.chat_var.get().strip(),
            "auto_send": bool(self.auto_var.get()),
            "remember_password": bool(self.remember_var.get()),
            "password": self.pw_var.get() if self.remember_var.get() else "",
            "ws_port": int(self.cfg.get("ws_port") or 6080),
        }

    def _poll(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "log":
                    self._log(payload)
                elif kind == "tg_state":
                    text, color = payload
                    self.tg_state.configure(text=text, foreground=color)
                elif kind == "tg_busy":
                    state = "disabled" if payload else "normal"
                    self.test_btn.configure(state=state)
                    self.find_btn.configure(state=state)
                elif kind == "tg_chat":
                    self.chat_var.set(payload)
                elif kind == "status":
                    self.status_var.set(payload)
                elif kind == "started":
                    url, tg_msg = payload
                    self.busy = False
                    self.url_var.set(url)
                    self.start_btn.configure(state="disabled")
                    self.stop_btn.configure(state="normal")
                    self.copy_btn.configure(state="normal")
                    self.open_btn.configure(state="normal")
                    self.send_btn.configure(state="normal")
                    self.pw_entry.configure(state="disabled")
                    status = "لینک فعال است — هرکس لینک + رمز را داشته باشد وصل می‌شود."
                    if tg_msg:
                        status += "  |  تلگرام: " + tg_msg
                    self.status_var.set(status)
                elif kind == "failed":
                    self.busy = False
                    self.status_var.set("خطا")
                    self.start_btn.configure(state="normal")
                    self._log("خطا: " + payload)
                    messagebox.showerror("خطا", payload)
                elif kind == "stopped":
                    self.busy = False
                    self.url_var.set("")
                    self.status_var.set("متوقف شد")
                    self.start_btn.configure(state="normal")
                    self.stop_btn.configure(state="disabled")
                    self.copy_btn.configure(state="disabled")
                    self.open_btn.configure(state="disabled")
                    self.send_btn.configure(state="disabled")
                    self.pw_entry.configure(state="normal")
        except queue.Empty:
            pass
        self.root.after(120, self._poll)

    # ---------- actions ----------
    def on_save(self):
        try:
            path = save_settings(self._collect())
        except OSError as exc:
            messagebox.showerror("ذخیره", "ذخیره تنظیمات ممکن نشد: %s" % exc)
            return
        self.cfg = self._collect()
        self._log("تنظیمات ذخیره شد: " + path)
        self.status_var.set("تنظیمات ذخیره شد.")

    def on_test_telegram(self):
        token = self.token_var.get().strip()
        if not token:
            messagebox.showwarning("تلگرام", "اول توکن ربات را وارد کنید.")
            return
        self.q.put(("tg_busy", True))
        self.q.put(("tg_state", ("در حال تست اتصال ...", "#666")))

        def worker():
            try:
                me = telegram_bot.get_me(token)
                name = "@" + (me.get("username") or "?")
                chat_id = self.chat_var.get().strip()
                note = ""
                if not chat_id:
                    found, title = telegram_bot.find_chat_id(token)
                    if found:
                        chat_id = found
                        self.q.put(("tg_chat", found))
                        note = "  چت‌آی‌دی %s (%s) ذخیره شد." % (found, title)
                    else:
                        note = "  هنوز کسی به ربات پیام نداده؛ در ربات /start بزنید."
                self.q.put(("tg_state", (
                    "اتصال برقرار است: ربات %s%s" % (name, note),
                    "#0a7" if chat_id else "#c80")))
            except telegram_bot.TelegramError as exc:
                self.q.put(("tg_state", (str(exc), "#c00")))
            except Exception as exc:  # noqa: BLE001
                self.q.put(("tg_state", ("خطای غیرمنتظره: %s" % exc, "#c00")))
            finally:
                self.q.put(("tg_busy", False))

        threading.Thread(target=worker, daemon=True).start()

    def on_find_chat(self):
        token = self.token_var.get().strip()
        if not token:
            messagebox.showwarning("تلگرام", "اول توکن ربات را وارد کنید.")
            return
        self.q.put(("tg_busy", True))

        def worker():
            try:
                found, title = telegram_bot.find_chat_id(token)
                if found:
                    self.q.put(("tg_chat", found))
                    self.q.put(("tg_state", ("چت پیدا شد: %s (%s)" % (found, title), "#0a7")))
                else:
                    self.q.put(("tg_state", (
                        "چتی پیدا نشد — یک بار در ربات /start بزنید و دوباره تلاش کنید.", "#c80")))
            except telegram_bot.TelegramError as exc:
                self.q.put(("tg_state", (str(exc), "#c00")))
            finally:
                self.q.put(("tg_busy", False))

        threading.Thread(target=worker, daemon=True).start()

    def _send_link(self, url, password):
        """Send link+password to Telegram; returns a short status string."""
        token = self.token_var.get().strip()
        chat_id = self.chat_var.get().strip()
        if not token:
            return "توکن خالی است — ارسال نشد."
        if not chat_id:
            try:
                chat_id, _title = telegram_bot.find_chat_id(token)
            except telegram_bot.TelegramError:
                chat_id = None
            if chat_id:
                self.q.put(("tg_chat", chat_id))
        if not chat_id:
            return "چت‌آی‌دی پیدا نشد — ارسال نشد."
        text = (
            "🖥 دسکتاپ آماده اتصال است\n\n"
            f"🔗 لینک: {url}\n"
            f"🔑 رمز ورود: {password}\n"
            f"⏱ {time.strftime('%Y-%m-%d %H:%M')}\n\n"
            "لینک را در مرورگر باز کنید و رمز بالا را وارد کنید."
        )
        try:
            telegram_bot.send_message(token, chat_id, text,
                                      button_text="اتصال به دسکتاپ", button_url=url)
            self._log("لینک و رمز به تلگرام ارسال شد (چت %s)." % chat_id)
            return "لینک ارسال شد ✅"
        except telegram_bot.TelegramError as exc:
            self._log("ارسال تلگرام ناموفق: %s" % exc)
            return "ارسال ناموفق: %s" % exc

    def on_start(self):
        if self.busy:
            return
        password = self.pw_var.get().strip()
        if not password:
            messagebox.showwarning("رمز عبور", "لطفاً رمز عبور را وارد کنید.")
            return
        if len(password) > 8 or not all(33 <= ord(c) <= 126 for c in password):
            messagebox.showwarning("رمز عبور", "رمز باید ۱ تا ۸ کاراکتر انگلیسی/عدد باشد.")
            return
        self.busy = True
        self.start_btn.configure(state="disabled")
        self.status_var.set("در حال ساخت لینک ...")
        self._log("شروع: راه‌اندازی سرور VNC، پل noVNC و تونل Cloudflare ...")
        auto = bool(self.auto_var.get())

        def worker():
            try:
                url = self.link.start(password)
                tg_msg = self._send_link(url, password) if auto else ""
                self.q.put(("started", (url, tg_msg)))
            except Exception as exc:  # noqa: BLE001
                self.q.put(("failed", str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def on_send_manual(self):
        url = self.url_var.get().strip()
        if not url:
            return
        self.status_var.set("در حال ارسال به تلگرام ...")

        def worker():
            msg = self._send_link(url, self.pw_var.get())
            self.q.put(("log", "تلگرام: " + msg))
            self.q.put(("status", msg))

        threading.Thread(target=worker, daemon=True).start()

    def on_stop(self):
        self.stop_btn.configure(state="disabled")
        self.status_var.set("در حال توقف ...")

        def worker():
            self.link.stop()
            self.q.put(("stopped", None))

        threading.Thread(target=worker, daemon=True).start()

    def on_copy(self):
        self.root.clipboard_clear()
        self.root.clipboard_append(self.url_var.get())
        self.status_var.set("لینک در کلیپ‌بورد کپی شد.")

    def on_open(self):
        webbrowser.open(self.url_var.get())

    def on_close(self):
        if self.link.running:
            if not messagebox.askyesno("خروج", "لینک فعال است؛ متوقف شود و خارج شوید؟"):
                return
            self.link.stop()
        try:
            if self.remember_var.get() or self.token_var.get():
                save_settings(self._collect())
        except OSError:
            pass
        self.root.destroy()


def main():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    if "--run-websockify" in sys.argv:
        run_websockify()
        return
    if "--selftest" in sys.argv:
        port = int(_arg("--selftest-port", "6080"))
        run_selftest(_arg("--selftest"), port, _arg("--out"), _arg("--stop-file"))
        return
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    main()
