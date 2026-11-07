"""Orchestration: TightVNC server + in-process websockify/noVNC + Cloudflare tunnel.

Design notes (why it looks like this):

* websockify runs as a **daemon thread inside this process** instead of a child
  process.  In a one-file build a child process would unpack a second copy of
  the whole application to %TEMP%; running it in-process is faster and, more
  importantly, it cannot survive the app.
* tvnserver.exe and cloudflared.exe are the only external processes.  Both are
  assigned to a Windows Job Object with KILL_ON_JOB_CLOSE, so they are killed by
  the OS even if the app is force-terminated.
"""
import ctypes
import os
import re
import secrets
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
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


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
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


def random_password(length=8):
    alphabet = "abcdefghijkmnpqrstuvwxyz23456789"
    return "".join(secrets.choice(alphabet) for _ in range(length))


class JobGuard:
    """Keep every child process inside a job object that dies with us."""

    _handle = None
    _tried = False

    @classmethod
    def _ensure(cls):
        if cls._tried:
            return cls._handle
        cls._tried = True
        try:
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)

            class IO_COUNTERS(ctypes.Structure):
                _fields_ = [("ReadOperationCount", ctypes.c_ulonglong),
                            ("WriteOperationCount", ctypes.c_ulonglong),
                            ("OtherOperationCount", ctypes.c_ulonglong),
                            ("ReadTransferCount", ctypes.c_ulonglong),
                            ("WriteTransferCount", ctypes.c_ulonglong),
                            ("OtherTransferCount", ctypes.c_ulonglong)]

            class BASIC(ctypes.Structure):
                _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64),
                            ("PerJobUserTimeLimit", ctypes.c_int64),
                            ("LimitFlags", ctypes.c_uint32),
                            ("MinimumWorkingSetSize", ctypes.c_size_t),
                            ("MaximumWorkingSetSize", ctypes.c_size_t),
                            ("ActiveProcessLimit", ctypes.c_uint32),
                            ("Affinity", ctypes.c_size_t),
                            ("PriorityClass", ctypes.c_uint32),
                            ("SchedulingClass", ctypes.c_uint32)]

            class EXTENDED(ctypes.Structure):
                _fields_ = [("BasicLimitInformation", BASIC),
                            ("IoInfo", IO_COUNTERS),
                            ("ProcessMemoryLimit", ctypes.c_size_t),
                            ("JobMemoryLimit", ctypes.c_size_t),
                            ("PeakProcessMemoryUsed", ctypes.c_size_t),
                            ("PeakJobMemoryUsed", ctypes.c_size_t)]

            job = k32.CreateJobObjectW(None, None)
            if not job:
                return None
            info = EXTENDED()
            info.BasicLimitInformation.LimitFlags = 0x00002000  # KILL_ON_JOB_CLOSE
            if not k32.SetInformationJobObject(job, 9, ctypes.byref(info),
                                               ctypes.sizeof(info)):
                return None
            cls._handle = job
        except Exception:
            cls._handle = None
        return cls._handle

    @classmethod
    def assign(cls, proc):
        try:
            job = cls._ensure()
            if not job or proc is None:
                return False
            k32 = ctypes.windll.kernel32
            handle = k32.OpenProcess(0x1F0FFF, False, proc.pid)
            if not handle:
                return False
            ok = k32.AssignProcessToJobObject(job, handle)
            k32.CloseHandle(handle)
            return bool(ok)
        except Exception:
            return False


# --------------------------------------------------------------------------- #
# in-process websockify
# --------------------------------------------------------------------------- #
_BRIDGE = {"started": False, "port": None}
_BRIDGE_LOCK = threading.Lock()


def _run_bridge(webroot, ws_port, vnc_port):
    """websockify_init() blocks forever; it lives in a daemon thread."""
    import signal
    import websockify.websockifyserver as wss
    import websockify.websocketproxy as wsp

    # websockify spawns a multiprocessing.Process per connection; threads are
    # far more reliable on Windows. The listener closes its copy of the socket
    # right after "spawn", so every handler needs its own duplicated handle.
    class _ThreadProcess:
        _children = []

        def __init__(self, target=None, args=()):
            self._socks = []
            dup_args = []
            for arg in args:
                if isinstance(arg, socket.socket):
                    dup = arg.dup()
                    self._socks.append(dup)
                    dup_args.append(dup)
                else:
                    dup_args.append(arg)

            def runner():
                try:
                    target(*dup_args)
                finally:
                    for sock in self._socks:
                        try:
                            sock.close()
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
            _ThreadProcess._children = [c for c in _ThreadProcess._children
                                       if c._thread.is_alive()]
            return _ThreadProcess._children

    wss.multiprocessing = _MPShim

    # start_server() installs SIGINT/SIGTERM handlers, which is illegal outside
    # the main thread; swallow only that error and keep everything else intact.
    real_signal = signal.signal

    def safe_signal(sig, handler):
        try:
            return real_signal(sig, handler)
        except (ValueError, OSError):
            return None

    signal.signal = safe_signal
    sys.argv = ["websockify", "--web", webroot,
                "127.0.0.1:%d" % ws_port, "127.0.0.1:%d" % vnc_port]
    try:
        wsp.websockify_init()
    except SystemExit:
        pass
    except Exception:  # pragma: no cover - only on shutdown races
        pass


def ensure_bridge(webroot, ws_port, vnc_port, log=print):
    with _BRIDGE_LOCK:
        if _BRIDGE["started"] and _BRIDGE["port"] == ws_port:
            return True
        threading.Thread(target=_run_bridge, args=(webroot, ws_port, vnc_port),
                         daemon=True, name="websockify").start()
        if not wait_port(ws_port, timeout=20):
            raise RuntimeError("پل وب‌سوکت (noVNC) روی پورت %d بالا نیامد." % ws_port)
        _BRIDGE["started"] = True
        _BRIDGE["port"] = ws_port
        log("noVNC روی پورت %d آماده است." % ws_port)
        return True


# --------------------------------------------------------------------------- #
# main controller
# --------------------------------------------------------------------------- #
class RemoteLink:
    def __init__(self, log=print):
        self.log = log
        self.url = None
        self.password = ""
        self.auto_password = False
        self._tvn = None
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
            if password:
                winreg.SetValueEx(key, "UseVncAuthentication", 0, winreg.REG_DWORD, 1)
                winreg.SetValueEx(key, "Password", 0, winreg.REG_BINARY,
                                  encrypt_password(password))
            else:
                # no password requested: let clients in without authentication
                winreg.SetValueEx(key, "UseVncAuthentication", 0, winreg.REG_DWORD, 0)
                try:
                    winreg.DeleteValue(key, "Password")
                except FileNotFoundError:
                    pass
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
            if self._tvn or self._cf:
                raise RuntimeError("در حال حاضر یک لینک فعال است؛ اول آن را متوقف کنید.")
            password = (password or "").strip()
            if password and (len(password) > 8 or not all(33 <= ord(c) <= 126 for c in password)):
                raise ValueError("رمز عبور باید ۱ تا ۸ کاراکتر انگلیسی/عدد باشد (محدودیت پروتکل VNC).")
            self.auto_password = not password
            if self.auto_password:
                # TightVNC's "None" security type trips up standard clients, so
                # instead we generate a random key and put it inside the link:
                # the viewer connects without asking, the wire format stays standard.
                password = random_password()
                self.log("بدون رمز درخواست شد: یک کلید تصادفی ساخته و داخل لینک قرار گرفت.")
            self.password = password

            for exe in (TVN_EXE, CLOUDFLARED_EXE):
                if not os.path.isfile(exe):
                    raise FileNotFoundError(f"فایل لازم پیدا نشد: {exe}")
            if not port_free(VNC_PORT):
                raise RuntimeError(f"پورت {VNC_PORT} اشغال است؛ یک سرور VNC دیگر در حال اجراست.")
            if not _BRIDGE["started"] and not port_free(ws_port):
                raise RuntimeError(f"پورت {ws_port} اشغال است.")

            try:
                self._snapshot_registry()
                self._write_settings(password)
                self.log("تنظیمات سرور VNC اعمال شد.")

                self._tvn = subprocess.Popen([TVN_EXE, "-run"], creationflags=CREATE_NO_WINDOW)
                JobGuard.assign(self._tvn)
                if not wait_port(VNC_PORT):
                    raise RuntimeError("سرور VNC روی پورت 5900 بالا نیامد.")
                self.log("سرور VNC روی پورت 5900 آماده است.")

                ensure_bridge(NOVNC_DIR, ws_port, VNC_PORT, log=self.log)

                self._cf = subprocess.Popen(
                    [CLOUDFLARED_EXE, "tunnel", "--url", f"http://127.0.0.1:{ws_port}",
                     "--no-autoupdate", "--protocol", "http2"],
                    creationflags=CREATE_NO_WINDOW,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                )
                JobGuard.assign(self._cf)
                self.log("در حال ساخت تونل Cloudflare ...")
                url = self._wait_tunnel_url(timeout=60)
                if not url:
                    raise RuntimeError("تونل Cloudflare ساخته نشد (اینترنت را بررسی کنید).")
                # index.html is our own full-screen viewer that fits the remote
                # desktop to the browser window (slightly zoomed out).
                self.url = url + "/"
                if self.auto_password:
                    self.url += "?password=" + urllib.parse.quote(password)
                self.log("لینک آماده شد.")
                return self.url
            except Exception:
                self._teardown()
                raise

    def _wait_tunnel_url(self, timeout):
        found = threading.Event()
        box = {}

        def reader():
            for raw in self._cf.stderr:
                line = raw.decode("utf-8", "replace").strip()
                match = URL_RE.search(line)
                if match and "url" not in box:
                    box["url"] = match.group(0)
                    found.set()

        t = threading.Thread(target=reader, daemon=True)
        t.start()
        if found.wait(timeout):
            return box.get("url")
        return None

    def stop(self):
        with self._lock:
            self._teardown()
            self.log("سرور VNC و تونل متوقف شدند (لینک بسته شد).")

    def _teardown(self):
        for proc in (self._cf, self._tvn):
            if proc is not None:
                try:
                    proc.terminate()
                    proc.wait(timeout=5)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
        self._cf = self._tvn = None
        self.url = None
        self._restore_registry()

    @property
    def running(self):
        return self._cf is not None
