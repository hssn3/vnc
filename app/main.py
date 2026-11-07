"""VNC Link — portable GUI app: share this desktop through a Cloudflare link."""
import os
import queue
import sys
import threading
import time
import tkinter as tk
import webbrowser
from tkinter import messagebox, ttk

from core import RemoteLink


def run_websockify():
    # args: --run-websockify <webroot> <listen_port> <target_port>
    webroot, listen_port, target_port = sys.argv[2:5]
    sys.argv = ["websockify", "--web", webroot,
                f"127.0.0.1:{listen_port}", f"127.0.0.1:{target_port}"]

    # websockify spawns a multiprocessing.Process per connection; on Windows
    # (and inside a frozen exe) spawn is unreliable, so run handlers as threads.
    import websockify.websockifyserver as wss

    class _ThreadProcess:
        _children = []

        def __init__(self, target=None, args=()):
            self._thread = threading.Thread(target=target, args=args, daemon=True)
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


def run_selftest():
    # args: --selftest <password> [ws_port]
    password = sys.argv[2]
    ws_port = int(sys.argv[3]) if len(sys.argv) > 3 else 6080
    link = RemoteLink(log=lambda m: print("[log]", m, flush=True))
    try:
        url = link.start(password, ws_port)
        print("LINK=" + url, flush=True)
        stop = threading.Event()

        def watch_stdin():
            try:
                sys.stdin.read()
            except Exception:
                pass
            stop.set()

        threading.Thread(target=watch_stdin, daemon=True).start()
        while not stop.wait(0.5):
            pass
    finally:
        link.stop()


class App:
    def __init__(self, root):
        self.root = root
        self.link = RemoteLink(log=self._log_async)
        self.q = queue.Queue()
        self.busy = False
        root.title("VNC Link — اشتراک ریموت دسکتاپ")
        root.geometry("660x520")
        root.minsize(560, 440)

        style = ttk.Style(root)
        try:
            style.theme_use("vista")
        except tk.TclError:
            pass
        default_font = ("Segoe UI", 10)
        root.option_add("*Font", default_font)

        pad = {"padx": 12, "pady": 6}
        frm = ttk.Frame(root)
        frm.pack(fill="both", expand=True)

        ttk.Label(frm, text="رمز عبور:").grid(row=0, column=0, sticky="e", **pad)
        self.pw_var = tk.StringVar()
        self.pw_entry = ttk.Entry(frm, textvariable=self.pw_var, show="*", width=24, font=default_font)
        self.pw_entry.grid(row=0, column=1, sticky="w", **pad)
        ttk.Label(frm, text="۱ تا ۸ کاراکتر انگلیسی یا عدد (محدودیت VNC)", foreground="#666")\
            .grid(row=1, column=1, sticky="w", padx=12)

        btns = ttk.Frame(frm)
        btns.grid(row=2, column=0, columnspan=3, sticky="w", **pad)
        self.start_btn = ttk.Button(btns, text="ایجاد لینک", command=self.on_start)
        self.start_btn.pack(side="left", padx=(0, 8))
        self.stop_btn = ttk.Button(btns, text="توقف", command=self.on_stop, state="disabled")
        self.stop_btn.pack(side="left")

        ttk.Label(frm, text="لینک اشتراک:").grid(row=3, column=0, sticky="e", **pad)
        link_row = ttk.Frame(frm)
        link_row.grid(row=3, column=1, columnspan=2, sticky="ew", **pad)
        self.url_var = tk.StringVar()
        self.url_entry = ttk.Entry(link_row, textvariable=self.url_var, state="readonly", width=42)
        self.url_entry.pack(side="left", fill="x", expand=True)
        self.copy_btn = ttk.Button(link_row, text="کپی", width=6, command=self.on_copy, state="disabled")
        self.copy_btn.pack(side="left", padx=(6, 0))
        self.open_btn = ttk.Button(link_row, text="باز کردن", width=8, command=self.on_open, state="disabled")
        self.open_btn.pack(side="left", padx=(6, 0))

        ttk.Label(frm, text="گزارش:").grid(row=4, column=0, sticky="ne", **pad)
        self.log_text = tk.Text(frm, height=12, state="disabled", wrap="word",
                                font=("Consolas", 9), background="#1e1e1e", foreground="#d4d4d4")
        self.log_text.grid(row=4, column=1, columnspan=2, sticky="nsew", **pad)

        self.status_var = tk.StringVar(value="آماده")
        ttk.Label(frm, textvariable=self.status_var, foreground="#0a7").grid(row=5, column=0, columnspan=3, sticky="w", **pad)

        frm.columnconfigure(1, weight=1)
        frm.rowconfigure(4, weight=1)
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.after(100, self._poll)
        self._log("برنامه آماده است. رمز عبور را وارد کنید و «ایجاد لینک» را بزنید.")

    # ---------- helpers ----------
    def _log_async(self, msg):
        self.q.put(("log", msg))

    def _log(self, msg):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", time.strftime("%H:%M:%S") + "  " + msg + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _poll(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "log":
                    self._log(payload)
                elif kind == "started":
                    self.busy = False
                    self.url_var.set(payload)
                    self.status_var.set("لینک فعال است — هرکس لینک و رمز را داشته باشد وصل می‌شود.")
                    self.start_btn.configure(state="disabled")
                    self.stop_btn.configure(state="normal")
                    self.copy_btn.configure(state="normal")
                    self.open_btn.configure(state="normal")
                    self.pw_entry.configure(state="disabled")
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
                    self.pw_entry.configure(state="normal")
        except queue.Empty:
            pass
        self.root.after(100, self._poll)

    # ---------- actions ----------
    def on_start(self):
        if self.busy:
            return
        password = self.pw_var.get()
        if not password:
            messagebox.showwarning("رمز عبور", "لطفاً رمز عبور را وارد کنید.")
            return
        self.busy = True
        self.start_btn.configure(state="disabled")
        self.status_var.set("در حال ساخت لینک ...")

        def worker():
            try:
                url = self.link.start(password)
                self.q.put(("started", url))
            except Exception as e:
                self.q.put(("failed", str(e)))

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
        run_selftest()
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