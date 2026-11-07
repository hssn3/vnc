"""Orchestration: TightVNC server + websockify/noVNC + Cloudflare quick tunnel."""
import os
import re
import socket
import subprocess
import sys
import threading
import time
import winreg

from vncrypt import encrypt_password

REG_PATH = r"Software\TightVNC\Server"
VNC_PORT = 5900
URL_RE = re.compile(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com")
CREATE_NO_WINDOW = 0x08000000

MANAGED_SETTINGS = {
    "UseVncAuthentication": (winreg.REG_DWORD, 1),
    "UseControlAuthentication": (winreg.REG_DWORD, 0),
    "QueryAcceptOnTimeout": (winreg.REG_DWORD, 1),
    "QueryTimeout": (winreg.REG_DWORD, 3),
    "LoopbackOnly": (winreg.REG_DWORD, 1),
    "AllowLoopback": (winreg.REG_DWORD, 1),
    "AlwaysShared": (winreg.REG_DWORD, 1),
    "DisconnectClients": (winreg.REG_DWORD, 0),
}


def base_dir():
    if getattr(sys, "frozen", False):
        return sys._MEIPASS
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


ASSETS = os.path.join(base_dir(), "assets")
TVN_EXE = os.path.join(ASSETS, "tvnserver", "tvnserver.exe")
CLOUDFLARED_EXE = os.path.join(ASSETS, "cloudflared.exe")
NOVNC_DIR = os.path.join(ASSETS, "novnc")


def port_free(port):
    with socket.socket() as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def wait_port(port, timeout=15):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return True
        except OSError:
            time.sleep(0.3)
    return False


class RemoteLink:
    def __init__(self, log=print):
        self.log = log
        self.url = None
        self._tvn = None
        self._ws = None
        self._cf = None
        self._reg_snapshot = None
        self._lock = threading.Lock()

    # ---------- registry ----------
    def _snapshot_registry(self):
        snap = {}
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_PATH, 0, winreg.KEY_READ)
        except FileNotFoundError:
            key = None
        for name in list(MANAGED_SETTINGS) + ["Password"]:
            if key is None:
                snap[name] = None
                continue
            try:
                snap[name] = winreg.QueryValueEx(key, name)
            except FileNotFoundError:
                snap[name] = None
        if key is not None:
            winreg.CloseKey(key)
        self._reg_snapshot = snap

    def _write_settings(self, password):
        key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, REG_PATH)
        try:
            for name, (typ, val) in MANAGED_SETTINGS.items():
                winreg.SetValueEx(key, name, 0, typ, val)
            winreg.SetValueEx(key, "Password", 0, winreg.REG_BINARY, encrypt_password(password))
        finally:
            winreg.CloseKey(key)

    def _restore_registry(self):
        if self._reg_snapshot is None:
            return
        try:
            key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, REG_PATH)
        except OSError:
            return
        try:
            for name, old in self._reg_snapshot.items():
                if old is None:
                    try:
                        winreg.DeleteValue(key, name)
                    except FileNotFoundError:
                        pass
                else:
                    val, typ = old
                    winreg.SetValueEx(key, name, 0, typ, val)
        finally:
            winreg.CloseKey(key)
        self._reg_snapshot = None

    # ---------- lifecycle ----------
    def start(self, password, ws_port=6080):
        with self._lock:
            if self._tvn or self._ws or self._cf:
                raise RuntimeError("در حال حاضر یک لینک فعال است؛ اول آن را متوقف کنید.")
            if not password or len(password) > 8 or not all(32 < ord(c) < 127 for c in password):
                raise ValueError("رمز عبور باید ۱ تا ۸ کاراکتر انگلیسی/عدد باشد (محدودیت پروتکل VNC).")
            for exe in (TVN_EXE, CLOUDFLARED_EXE):
                if not os.path.isfile(exe):
                    raise FileNotFoundError(f"فایل لازم پیدا نشد: {exe}")
            if not port_free(VNC_PORT):
                raise RuntimeError(f"پورت {VNC_PORT} اشغال است؛ یک سرور VNC دیگر در حال اجراست.")
            if not port_free(ws_port):
                raise RuntimeError(f"پورت {ws_port} اشغال است.")

            try:
                self._snapshot_registry()
                self._write_settings(password)
                self.log("تنظیمات سرور VNC اعمال شد.")

                self._tvn = subprocess.Popen([TVN_EXE, "-run"], creationflags=CREATE_NO_WINDOW)
                if not wait_port(VNC_PORT):
                    raise RuntimeError("سرور VNC روی پورت 5900 بالا نیامد.")
                self.log("سرور VNC روی پورت 5900 آماده است.")

                self._ws = subprocess.Popen(
                    self._websockify_cmd(ws_port),
                    creationflags=CREATE_NO_WINDOW,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                if not wait_port(ws_port):
                    raise RuntimeError("پل وب‌سوکت (noVNC) بالا نیامد.")
                self.log("noVNC روی پورت %s آماده است." % ws_port)

                self._cf = subprocess.Popen(
                    [CLOUDFLARED_EXE, "tunnel", "--url", f"http://127.0.0.1:{ws_port}",
                     "--no-autoupdate", "--protocol", "http2"],
                    creationflags=CREATE_NO_WINDOW,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                )
                self.log("در حال ساخت تونل Cloudflare ...")
                url = self._wait_tunnel_url(timeout=60)
                if not url:
                    raise RuntimeError("تونل Cloudflare ساخته نشد (اینترنت را بررسی کنید).")
                self.url = url + "/vnc.html?autoconnect=true&resize=remote&reconnect=true&reconnect_delay=2000"
                self.log("لینک آماده شد.")
                return self.url
            except Exception:
                self._teardown()
                raise

    def _websockify_cmd(self, ws_port):
        args = ["--run-websockify", NOVNC_DIR, str(ws_port), str(VNC_PORT)]
        if getattr(sys, "frozen", False):
            return [sys.executable] + args
        return [sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "main.py")] + args

    def _wait_tunnel_url(self, timeout):
        found = threading.Event()
        box = {}

        def reader():
            for raw in self._cf.stderr:
                line = raw.decode("utf-8", "replace").strip()
                m = URL_RE.search(line)
                if m and "url" not in box:
                    box["url"] = m.group(0)
                    found.set()

        t = threading.Thread(target=reader, daemon=True)
        t.start()
        if found.wait(timeout):
            return box.get("url")
        return None

    def stop(self):
        with self._lock:
            self._teardown()
            self.log("همه سرویس‌ها متوقف شدند.")

    def _teardown(self):
        for proc in (self._cf, self._ws, self._tvn):
            if proc is not None:
                try:
                    proc.terminate()
                    proc.wait(timeout=5)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
        self._cf = self._ws = self._tvn = None
        self.url = None
        self._restore_registry()

    @property
    def running(self):
        return self._cf is not None