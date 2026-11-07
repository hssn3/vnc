"""VNC Link — portable GUI app: share this desktop through a Cloudflare link.

The window is a modern, RTL-friendly dashboard:
  * create / stop a password protected link (a password is optional)
  * see the live state of the current link (online / offline + uptime)
  * browse the history of every link that was created
  * deliver the link to a Telegram bot
Everything (settings.json, links.json, the log) is stored in the app folder.
"""
import os
import queue
import random
import socket
import string
import sys
import threading
import time
import tkinter as tk
import urllib.request
import webbrowser
from tkinter import messagebox, ttk

import history
import telegram_bot
from core import RemoteLink
from settings import app_dir, load_settings, save_settings, settings_path

VERSION = "1.3.0"
PASSWORD_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"

# palette ---------------------------------------------------------------------
BG = "#eef2f7"
CARD = "#ffffff"
BORDER = "#e2e8f0"
TEXT = "#0f172a"
MUTED = "#64748b"
ACCENT = "#2563eb"
ACCENT_HOVER = "#1d4ed8"
GREEN = "#16a34a"
GREEN_SOFT = "#e7f6ec"
RED = "#dc2626"
RED_SOFT = "#fdecec"
AMBER = "#b45309"
AMBER_SOFT = "#fdf3e3"
SOFT = "#f1f5f9"
SOFT_HOVER = "#e2e8f0"

FONT = ("Segoe UI", 10)
FONT_B = ("Segoe UI Semibold", 10)
FONT_SM = ("Segoe UI", 9)
FONT_TITLE = ("Segoe UI Semibold", 13)


# --------------------------------------------------------------------------- #
# child-process entry points
# --------------------------------------------------------------------------- #
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
# small UI helpers
# --------------------------------------------------------------------------- #
def card(parent):
    """A white panel with a 1px border."""
    return tk.Frame(parent, bg=CARD, highlightbackground=BORDER, highlightthickness=1)


def card_title(parent, text, subtitle=None):
    row = tk.Frame(parent, bg=CARD)
    row.pack(fill="x", padx=14, pady=(12, 6))
    tk.Label(row, text=text, bg=CARD, fg=TEXT, font=FONT_B).pack(side="right")
    if subtitle:
        tk.Label(row, text=subtitle, bg=CARD, fg=MUTED, font=FONT_SM).pack(side="right", padx=(0, 8))
    return row


def mk_button(parent, text, command, kind="primary", width=None):
    palette = {
        "primary": (ACCENT, "white", ACCENT_HOVER),
        "soft": (SOFT, TEXT, SOFT_HOVER),
        "danger": (RED, "white", "#b91c1c"),
        "ghost": (CARD, MUTED, SOFT),
    }
    bg, fg, hover = palette[kind]
    btn = tk.Button(parent, text=text, command=command, bg=bg, fg=fg,
                    activebackground=hover, activeforeground=fg, relief="flat", bd=0,
                    highlightthickness=0, padx=14, pady=6, font=FONT_B, cursor="hand2",
                    disabledforeground="#94a3b8")
    if width:
        btn.configure(width=width)
    btn._bg, btn._fg, btn._hover = bg, fg, hover

    def on_enter(_=None):
        if btn["state"] != "disabled":
            btn.configure(bg=hover)

    def on_leave(_=None):
        if btn["state"] == "disabled":
            btn.configure(bg=SOFT, fg="#94a3b8", cursor="arrow")
        else:
            btn.configure(bg=bg, fg=fg, cursor="hand2")

    btn.bind("<Enter>", on_enter)
    btn.bind("<Leave>", on_leave)
    return btn


def enable(btn, on=True):
    btn.configure(state="normal" if on else "disabled")
    if on:
        btn.configure(bg=btn._bg, fg=btn._fg, cursor="hand2")
    else:
        btn.configure(bg=SOFT, fg="#94a3b8", cursor="arrow")


def mk_entry(parent, var, show=None, width=None):
    ent = tk.Entry(parent, textvariable=var, bg="#f8fafc", fg=TEXT, relief="flat",
                   highlightthickness=1, highlightbackground=BORDER, highlightcolor=ACCENT,
                   insertbackground=TEXT, font=FONT, bd=0)
    ent.configure(insertwidth=2)
    if show:
        ent.configure(show=show)
    if width:
        ent.configure(width=width)
    return ent


def pill(parent, text, bg, fg):
    lbl = tk.Label(parent, text="●  " + text, bg=bg, fg=fg, font=FONT_SM,
                   padx=10, pady=4)
    return lbl


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

        self.link_base = None          # https://xxx.trycloudflare.com/
        self.link_started = 0.0
        self.link_online = None        # None = unknown, True/False
        self.link_latency = 0
        self.link_last_check = ""
        self._checking = False
        self._ticks = 0
        self._hist_items = []

        root.title(f"VNC Link {VERSION} — اشتراک دسکتاپ")
        root.geometry("1080x760")
        root.minsize(940, 680)
        root.configure(bg=BG)
        root.option_add("*Font", FONT)

        self.style = ttk.Style(root)
        try:
            self.style.theme_use("clam")
        except tk.TclError:
            pass
        self.style.configure("Treeview", background=CARD, fieldbackground=CARD, foreground=TEXT,
                             rowheight=26, borderwidth=0, font=FONT_SM)
        self.style.configure("Treeview.Heading", background=SOFT, foreground=MUTED,
                             relief="flat", font=("Segoe UI", 9, "bold"))
        self.style.map("Treeview", background=[("selected", "#dbeafe")],
                       foreground=[("selected", TEXT)])
        self.style.configure("TCheckbutton", background=CARD, foreground=TEXT, font=FONT_SM)

        self._build_header()
        self._build_body()

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(150, self._poll)
        self.root.after(1000, self._tick)
        self._refresh_history()
        self._log(f"VNC Link {VERSION} — آماده.")
        self._log("پوشهٔ داده: " + app_dir())

    # ---------------- header ----------------
    def _build_header(self):
        head = tk.Frame(self.root, bg=CARD, height=70)
        head.pack(fill="x")
        head.pack_propagate(False)

        right = tk.Frame(head, bg=CARD)
        right.pack(side="right", padx=18, pady=12, fill="y")
        tk.Label(right, text="VNC Link", bg=CARD, fg=TEXT, font=FONT_TITLE).pack(side="right")
        tk.Label(right, text=VERSION, bg=CARD, fg=MUTED, font=FONT_SM).pack(side="right", padx=(0, 6), pady=(4, 0))
        tk.Label(right, text="اشتراک دسکتاپ با لینک ابری — بدون نصب برای طرف مقابل",
                 bg=CARD, fg=MUTED, font=FONT_SM).pack(side="right", padx=(12, 0), pady=(4, 0))

        left = tk.Frame(head, bg=CARD)
        left.pack(side="left", padx=18, pady=12)
        self.state_pill = pill(left, "آماده", SOFT, MUTED)
        self.state_pill.pack(side="left")

        tk.Frame(self.root, bg=BORDER, height=1).pack(fill="x")

    # ---------------- body ----------------
    def _build_body(self):
        body = tk.Frame(self.root, bg=BG)
        body.pack(fill="both", expand=True, padx=14, pady=12)
        body.columnconfigure(0, weight=1)
        body.columnconfigure(1, weight=0, minsize=420)
        body.rowconfigure(0, weight=1)

        left = tk.Frame(body, bg=BG)
        left.grid(row=0, column=0, sticky="nsew")
        right = tk.Frame(body, bg=BG)
        right.grid(row=0, column=1, sticky="nsew", padx=(12, 0))

        self._build_share_card(right)
        self._build_telegram_card(right)
        self._build_help_card(right)
        self._build_save_row(right)

        self._build_current_card(left)
        self._build_history_card(left)
        self._build_log_card(left)

    # ---- right column: share ----
    def _build_share_card(self, parent):
        box = card(parent)
        box.pack(fill="x")
        card_title(box, "اشتراک‌گذاری دسکتاپ")

        row = tk.Frame(box, bg=CARD)
        row.pack(fill="x", padx=14, pady=(2, 0))
        tk.Label(row, text="رمز عبور", bg=CARD, fg=MUTED, font=FONT_SM).pack(side="right")
        self.pw_var = tk.StringVar(value=self.cfg.get("password") or "")
        self.pw_entry = mk_entry(row, self.pw_var, show="•")
        self.pw_entry.pack(side="right", fill="x", expand=True, padx=8, ipady=4)
        mk_button(row, "تصادفی", self._gen_password, "soft").pack(side="right")
        mk_button(row, "نمایش", lambda: self._toggle(self.pw_entry, "•"), "ghost").pack(side="right", padx=4)

        self.pw_hint = tk.Label(box, text="۱ تا ۸ کاراکتر انگلیسی/عدد — یا خالی بگذارید تا بدون رمز باز شود",
                                bg=CARD, fg=MUTED, font=FONT_SM, anchor="e", justify="right")
        self.pw_hint.pack(fill="x", padx=14, pady=(4, 0))

        btns = tk.Frame(box, bg=CARD)
        btns.pack(fill="x", padx=14, pady=(10, 14))
        self.start_btn = mk_button(btns, "ایجاد لینک", self.on_start, "primary")
        self.start_btn.pack(side="right", fill="x", expand=True)
        self.stop_btn = mk_button(btns, "توقف", self.on_stop, "danger", width=8)
        self.stop_btn.pack(side="right", padx=8)
        enable(self.stop_btn, False)

    # ---- right column: telegram ----
    def _build_telegram_card(self, parent):
        box = card(parent)
        box.pack(fill="x", pady=(12, 0))
        card_title(box, "ربات تلگرام", "اختیاری")

        r1 = tk.Frame(box, bg=CARD)
        r1.pack(fill="x", padx=14, pady=(2, 0))
        tk.Label(r1, text="توکن ربات", bg=CARD, fg=MUTED, font=FONT_SM).pack(side="right")
        self.token_var = tk.StringVar(value=self.cfg.get("bot_token") or "")
        self.token_entry = mk_entry(r1, self.token_var, show="•")
        self.token_entry.pack(side="right", fill="x", expand=True, padx=8, ipady=4)
        mk_button(r1, "نمایش", lambda: self._toggle(self.token_entry, "•"), "ghost").pack(side="right")

        r2 = tk.Frame(box, bg=CARD)
        r2.pack(fill="x", padx=14, pady=(8, 0))
        tk.Label(r2, text="چت آی‌دی", bg=CARD, fg=MUTED, font=FONT_SM).pack(side="right")
        self.chat_var = tk.StringVar(value=str(self.cfg.get("chat_id") or ""))
        mk_entry(r2, self.chat_var).pack(side="right", fill="x", expand=True, padx=8, ipady=4)
        self.find_btn = mk_button(r2, "دریافت خودکار", self.on_find_chat, "soft")
        self.find_btn.pack(side="right")

        r3 = tk.Frame(box, bg=CARD)
        r3.pack(fill="x", padx=14, pady=(10, 0))
        self.test_btn = mk_button(r3, "تست اتصال", self.on_test_telegram, "soft")
        self.test_btn.pack(side="right")
        self.remember_var = tk.BooleanVar(value=bool(self.cfg.get("remember_password")))
        tk.Checkbutton(r3, text="ذخیرهٔ رمز", variable=self.remember_var, bg=CARD,
                       activebackground=CARD, fg=TEXT, selectcolor="white", bd=0,
                       highlightthickness=0, font=FONT_SM, cursor="hand2").pack(side="right", padx=8)
        self.auto_var = tk.BooleanVar(value=bool(self.cfg.get("auto_send")))
        tk.Checkbutton(r3, text="ارسال خودکار لینک", variable=self.auto_var, bg=CARD,
                       activebackground=CARD, fg=TEXT, selectcolor="white", bd=0,
                       highlightthickness=0, font=FONT_SM, cursor="hand2").pack(side="right")

        self.tg_state = tk.Label(box, text="توکن را از @BotFather بگیرید و در ربات /start بزنید.",
                                 bg=CARD, fg=MUTED, font=FONT_SM, anchor="e", justify="right",
                                 wraplength=380)
        self.tg_state.pack(fill="x", padx=14, pady=(8, 14))

    def _build_help_card(self, parent):
        box = card(parent)
        box.pack(fill="x", pady=(12, 0))
        card_title(box, "راهنمای سریع")
        tips = (
            "• رمز را خالی بگذارید تا لینک بدون پرسیدن رمز باز شود (کلید داخل خود لینک می‌نشیند).",
            "• دسکتاپ برای طرف مقابل تمام‌صفحه و بدون نصب نمایش داده می‌شود.",
            "• «توقف» سرور، تونل و لینک را کامل می‌بندد.",
            "• لینک را فقط به فرد مورد اعتماد بدهید.",
        )
        body = tk.Frame(box, bg=CARD)
        body.pack(fill="x", padx=14, pady=(0, 14))
        for tip in tips:
            tk.Label(body, text=tip, bg=CARD, fg=MUTED, font=FONT_SM, anchor="e",
                     justify="right", wraplength=388).pack(fill="x", pady=2)

    def _build_save_row(self, parent):
        row = tk.Frame(parent, bg=BG)
        row.pack(side="bottom", fill="x", pady=(12, 0))
        mk_button(row, "ذخیرهٔ تنظیمات", self.on_save, "soft").pack(side="right")
        self.status_var = tk.StringVar(value="آماده")
        tk.Label(row, textvariable=self.status_var, bg=BG, fg=MUTED, font=FONT_SM,
                 anchor="e", justify="right").pack(side="right", padx=10, fill="x", expand=True)

    # ---- left column: current link ----
    def _build_current_card(self, parent):
        box = card(parent)
        box.pack(fill="x")

        top = card_title(box, "لینک فعلی")
        self.state_pill2 = pill(top, "بدون لینک", SOFT, MUTED)
        self.state_pill2.pack(side="left")

        row = tk.Frame(box, bg=CARD)
        row.pack(fill="x", padx=14, pady=(0, 6))
        self.url_var = tk.StringVar()
        self.url_entry = mk_entry(row, self.url_var)
        self.url_entry.configure(state="readonly", readonlybackground="#f8fafc")
        self.url_entry.pack(side="right", fill="x", expand=True, ipady=5)
        self.open_btn = mk_button(row, "باز کردن", self.on_open, "soft")
        self.open_btn.pack(side="right", padx=6)
        self.copy_btn = mk_button(row, "کپی", self.on_copy, "primary", width=6)
        self.copy_btn.pack(side="right")
        enable(self.copy_btn, False)
        enable(self.open_btn, False)

        info = tk.Frame(box, bg=CARD)
        info.pack(fill="x", padx=14, pady=(0, 14))
        self.mon_var = tk.StringVar(value="برای ساخت لینک، رمز را وارد کنید و «ایجاد لینک» را بزنید.")
        tk.Label(info, textvariable=self.mon_var, bg=CARD, fg=MUTED, font=FONT_SM,
                 anchor="e", justify="right").pack(side="right")
        self.uptime_var = tk.StringVar(value="")
        tk.Label(info, textvariable=self.uptime_var, bg=CARD, fg=MUTED, font=FONT_SM,
                 anchor="w").pack(side="left")

    # ---- left column: history ----
    def _build_history_card(self, parent):
        box = card(parent)
        box.pack(fill="both", expand=True, pady=(12, 0))

        top = card_title(box, "تاریخچهٔ لینک‌ها")
        mk_button(top, "پاک کردن", self.on_clear_history, "ghost").pack(side="left")

        wrap = tk.Frame(box, bg=CARD)
        wrap.pack(fill="both", expand=True, padx=14, pady=(0, 6))
        cols = ("url", "time", "state")
        self.hist_tree = ttk.Treeview(wrap, columns=cols, show="headings", selectmode="browse")
        self.hist_tree.heading("url", text="لینک")
        self.hist_tree.heading("time", text="زمان ساخت")
        self.hist_tree.heading("state", text="وضعیت")
        self.hist_tree.column("url", width=320, minwidth=180, stretch=True, anchor="w")
        self.hist_tree.column("time", width=140, minwidth=120, stretch=False, anchor="center")
        self.hist_tree.column("state", width=70, minwidth=60, stretch=False, anchor="center")
        sb = ttk.Scrollbar(wrap, orient="vertical", command=self.hist_tree.yview)
        self.hist_tree.configure(yscrollcommand=sb.set)
        self.hist_tree.pack(side="right", fill="both", expand=True)
        sb.pack(side="left", fill="y")
        self.hist_tree.bind("<Double-1>", lambda e: self.on_hist_open())

        bar = tk.Frame(box, bg=CARD)
        bar.pack(fill="x", padx=14, pady=(0, 12))
        mk_button(bar, "ارسال به تلگرام", self.on_hist_send, "soft").pack(side="right")
        mk_button(bar, "باز کردن", self.on_hist_open, "soft").pack(side="right", padx=6)
        mk_button(bar, "کپی لینک", self.on_hist_copy, "primary", width=10).pack(side="right")

    # ---- left column: log ----
    def _build_log_card(self, parent):
        box = card(parent)
        box.pack(fill="x", pady=(12, 0))
        card_title(box, "گزارش")
        self.log_text = tk.Text(box, height=6, state="disabled", wrap="word", bd=0,
                                bg="#0f172a", fg="#cbd5e1", insertbackground="#cbd5e1",
                                font=("Consolas", 9), padx=10, pady=8)
        self.log_text.pack(fill="x", padx=14, pady=(0, 12))

    # ---------------- helpers ----------------
    def _toggle(self, widget, mask):
        widget.configure(show="" if widget.cget("show") else mask)

    def _gen_password(self):
        self.pw_var.set("".join(random.choice(PASSWORD_ALPHABET) for _ in range(8)))

    def _set_state(self, text, bg, fg):
        for p in (self.state_pill, getattr(self, "state_pill2", None)):
            if p is not None:
                p.configure(text="●  " + text, bg=bg, fg=fg)

    def _log_async(self, msg):
        self.q.put(("log", msg))

    def _log(self, msg):
        line = time.strftime("%H:%M:%S") + "  " + msg
        self.log_text.configure(state="normal")
        self.log_text.insert("end", line + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")
        try:
            with open(os.path.join(app_dir(), "vnc-link.log"), "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError:
            pass

    def _collect(self):
        return {
            "bot_token": self.token_var.get().strip(),
            "chat_id": self.chat_var.get().strip(),
            "auto_send": bool(self.auto_var.get()),
            "remember_password": bool(self.remember_var.get()),
            "password": self.pw_var.get() if self.remember_var.get() else "",
            "ws_port": int(self.cfg.get("ws_port") or 6080),
        }

    # ---------------- event pump ----------------
    def _poll(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "log":
                    self._log(payload)
                elif kind == "tg_state":
                    text, color = payload
                    self.tg_state.configure(text=text, fg=color)
                elif kind == "tg_busy":
                    enable(self.test_btn, not payload)
                    enable(self.find_btn, not payload)
                elif kind == "tg_chat":
                    self.chat_var.set(payload)
                elif kind == "status":
                    self.status_var.set(payload)
                elif kind == "started":
                    self._on_started(*payload)
                elif kind == "failed":
                    self._on_failed(payload)
                elif kind == "stopped":
                    self._on_stopped()
                elif kind == "linkstate":
                    self._on_linkstate(*payload)
        except queue.Empty:
            pass
        self.root.after(150, self._poll)

    # ---------------- link lifecycle ----------------
    def _on_started(self, url, tg_msg, password):
        self.busy = False
        self.link_base = url
        self.link_started = time.time()
        self.link_online = None
        self.url_var.set(url)
        enable(self.start_btn, False)
        enable(self.stop_btn, True)
        if self.link_online is None:
            self._set_state("در حال بررسی", AMBER_SOFT, AMBER)
        enable(self.copy_btn, True)
        enable(self.open_btn, True)
        self.pw_entry.configure(state="disabled")
        self.status_var.set("لینک ساخته شد" + ("  |  تلگرام: " + tg_msg if tg_msg else ""))
        self.mon_var.set("وضعیت: در حال بررسی اتصال…")
        self._log("لینک ساخته شد: " + url)
        self._refresh_history()
        self._spawn_check()

    def _on_failed(self, message):
        self.busy = False
        enable(self.start_btn, True)
        self._set_state("خطا", RED_SOFT, RED)
        self.status_var.set("خطا")
        self._log("خطا: " + message)
        messagebox.showerror("خطا", message)

    def _on_stopped(self):
        self.busy = False
        self.link_base = None
        self.link_online = None
        self.url_var.set("")
        self.mon_var.set("لینک بسته شد. برای ساخت لینک تازه «ایجاد لینک» را بزنید.")
        self.uptime_var.set("")
        self._set_state("متوقف", SOFT, MUTED)
        self.status_var.set("متوقف شد")
        enable(self.start_btn, True)
        enable(self.stop_btn, False)
        enable(self.copy_btn, False)
        enable(self.open_btn, False)
        self.pw_entry.configure(state="normal")
        history.close_active()
        self._refresh_history()

    def _on_linkstate(self, state, detail):
        if not self.link_base:
            return
        self.link_last_check = time.strftime("%H:%M:%S")
        if state == "online":
            self.link_latency = detail
            self.link_online = True
            self._set_state("آنلاین", GREEN_SOFT, GREEN)
            self.mon_var.set(f"وضعیت: آنلاین • پاسخ {detail} میلی‌ثانیه • آخرین بررسی {self.link_last_check}")
        else:
            self.link_online = False
            self._set_state("آفلاین", RED_SOFT, RED)
            self.mon_var.set(f"وضعیت: آفلاین • آخرین بررسی {self.link_last_check} • {detail}")

    def _spawn_check(self):
        if self._checking or not self.link_base:
            return
        self._checking = True
        url = self.link_base

        def worker():
            started = time.time()
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "VNC-Link/monitor"})
                with urllib.request.urlopen(req, timeout=8) as resp:
                    if 200 <= resp.status < 400:
                        self.q.put(("linkstate", ("online", int((time.time() - started) * 1000))))
                    else:
                        self.q.put(("linkstate", ("offline", "HTTP %s" % resp.status)))
            except Exception as exc:  # noqa: BLE001
                self.q.put(("linkstate", ("offline", str(exc)[:90])))
            finally:
                self._checking = False

        threading.Thread(target=worker, daemon=True).start()

    def _tick(self):
        self._ticks += 1
        if self.link_base and self.link_started:
            up = int(time.time() - self.link_started)
            self.uptime_var.set("مدت فعال بودن: " + time.strftime("%H:%M:%S", time.gmtime(up)))
            if self._ticks % 10 == 0:
                self._spawn_check()
        self.root.after(1000, self._tick)

    # ---------------- history ----------------
    def _refresh_history(self, select=None):
        items = history.load()
        self._hist_items = items
        for row in self.hist_tree.get_children():
            self.hist_tree.delete(row)
        for idx, item in enumerate(items):
            state = "فعال" if item.get("state") == "active" else "بسته"
            self.hist_tree.insert("", "end", iid=str(idx),
                                  values=(item.get("url", ""), item.get("created_at", ""), state))
        children = self.hist_tree.get_children()
        if children:
            target = select if select is not None else children[0]
            if target in children:
                self.hist_tree.selection_set(target)

    def _selected_hist(self):
        sel = self.hist_tree.selection()
        if not sel:
            return None
        idx = int(sel[0])
        if 0 <= idx < len(self._hist_items):
            return self._hist_items[idx]
        return None

    def on_hist_copy(self):
        item = self._selected_hist()
        if not item:
            self.status_var.set("یک لینک را از تاریخچه انتخاب کنید.")
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(item.get("url", ""))
        self.status_var.set("لینک انتخاب‌شده کپی شد.")

    def on_hist_open(self):
        item = self._selected_hist()
        if item:
            webbrowser.open(item.get("url", ""))

    def on_hist_send(self):
        item = self._selected_hist()
        if not item:
            self.status_var.set("یک لینک را از تاریخچه انتخاب کنید.")
            return
        url, password = item.get("url", ""), item.get("password", "")
        self.status_var.set("در حال ارسال به تلگرام …")

        def worker():
            msg = self._send_link(url, password)
            self.q.put(("log", "تلگرام: " + msg))
            self.q.put(("status", msg))

        threading.Thread(target=worker, daemon=True).start()

    def on_clear_history(self):
        if not messagebox.askyesno("پاک کردن تاریخچه", "همهٔ لینک‌های ذخیره‌شده پاک شوند؟"):
            return
        history.clear()
        self._refresh_history()
        self.status_var.set("تاریخچه پاک شد.")

    # ---------------- actions ----------------
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
        self.q.put(("tg_state", ("در حال تست اتصال …", MUTED)))

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
                    GREEN if chat_id else AMBER)))
            except telegram_bot.TelegramError as exc:
                self.q.put(("tg_state", (str(exc), RED)))
            except Exception as exc:  # noqa: BLE001
                self.q.put(("tg_state", ("خطای غیرمنتظره: %s" % exc, RED)))
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
                    self.q.put(("tg_state", ("چت پیدا شد: %s (%s)" % (found, title), GREEN)))
                else:
                    self.q.put(("tg_state", (
                        "چتی پیدا نشد — یک بار در ربات /start بزنید و دوباره تلاش کنید.", AMBER)))
            except telegram_bot.TelegramError as exc:
                self.q.put(("tg_state", (str(exc), RED)))
            finally:
                self.q.put(("tg_busy", False))

        threading.Thread(target=worker, daemon=True).start()

    def _send_link(self, url, password):
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
        lock = ("🔑 رمز ورود: %s" % password) if password else "🔓 بدون پرسیدن رمز — لینک خودش کلید دسترسی دارد"
        text = (
            "🖥 دسکتاپ آماده اتصال است\n\n"
            f"🔗 لینک: {url}\n"
            f"{lock}\n"
            f"⏱ {time.strftime('%Y-%m-%d %H:%M')}\n\n"
            "لینک را در مرورگر باز کنید؛ دسکتاپ تمام‌صفحه نمایش داده می‌شود."
        )
        try:
            telegram_bot.send_message(token, chat_id, text,
                                      button_text="اتصال به دسکتاپ", button_url=url)
            self._log("لینک به تلگرام ارسال شد (چت %s)." % chat_id)
            return "لینک ارسال شد ✅"
        except telegram_bot.TelegramError as exc:
            self._log("ارسال تلگرام ناموفق: %s" % exc)
            return "ارسال ناموفق: %s" % exc

    def on_start(self):
        if self.busy:
            return
        password = self.pw_var.get().strip()
        if password and (len(password) > 8 or not all(33 <= ord(c) <= 126 for c in password)):
            messagebox.showwarning("رمز عبور", "رمز باید ۱ تا ۸ کاراکتر انگلیسی/عدد باشد، یا خالی بماند.")
            return
        self.busy = True
        enable(self.start_btn, False)
        self._set_state("در حال ساخت", AMBER_SOFT, AMBER)
        self.status_var.set("در حال ساخت لینک …")
        self._log("شروع: راه‌اندازی سرور VNC، پل noVNC و تونل Cloudflare …")
        if not password:
            self._log("بدون رمز: یک کلید تصادفی ساخته و داخل خود لینک قرار می‌گیرد؛ "
                      "هر کسی این لینک را داشته باشد وارد می‌شود.")
        auto = bool(self.auto_var.get())

        def worker():
            try:
                url = self.link.start(password)
                history.add(url, password)
                tg_msg = self._send_link(url, password) if auto else ""
                self.q.put(("started", (url, tg_msg, password)))
            except Exception as exc:  # noqa: BLE001
                self.q.put(("failed", str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def on_stop(self):
        if not self.link.running:
            return
        enable(self.stop_btn, False)
        self.status_var.set("در حال توقف …")
        self._set_state("در حال توقف", AMBER_SOFT, AMBER)

        def worker():
            self.link.stop()
            self.q.put(("stopped", None))

        threading.Thread(target=worker, daemon=True).start()

    def on_copy(self):
        url = self.url_var.get()
        if not url:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(url)
        self.status_var.set("لینک در کلیپ‌بورد کپی شد.")

    def on_open(self):
        if self.url_var.get():
            webbrowser.open(self.url_var.get())

    def on_close(self):
        if self.link.running:
            if not messagebox.askyesno("خروج", "لینک فعال است؛ متوقف شود و خارج شوید؟"):
                return
            self.link.stop()
            history.close_active()
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
    if "--selftest" in sys.argv:
        port = int(_arg("--selftest-port", "6080"))
        run_selftest(_arg("--selftest") or "", port, _arg("--out"), _arg("--stop-file"))
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
