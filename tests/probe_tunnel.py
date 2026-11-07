"""Probe: compare the RFB handshake locally vs through the Cloudflare tunnel."""
import os
import re
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PASSWORD = "Test1234"
STOP = os.path.join(tempfile.gettempdir(), "vnclink_probe.stop")
OUT = os.path.join(tempfile.gettempdir(), "vnclink_probe.out")


def drain(ws, label, seconds=20):
    deadline = time.time() + seconds
    ws.settimeout(2)
    chunks = []
    while time.time() < deadline:
        try:
            data = ws.recv()
        except Exception as exc:
            if exc.__class__.__name__.endswith("TimeoutException"):
                continue
            chunks.append("<%s>" % exc.__class__.__name__)
            break
        if isinstance(data, str):
            data = data.encode("latin-1")
        if not data:
            chunks.append("<closed>")
            break
        chunks.append(repr(data))
        if len(data) and sum(len(c) for c in chunks) > 40:
            break
    print("   %s: %s" % (label, " | ".join(chunks) or "<nothing>"))


def session(url_host, label, tls=True):
    import websocket
    scheme = "wss" if tls else "ws"
    print("[%s] %s://%s/websockify" % (label, scheme, url_host))
    try:
        ws = websocket.create_connection(f"{scheme}://{url_host}/websockify",
                                         subprotocols=["binary"], timeout=25,
                                         suppress_origin=True)
    except Exception as exc:
        print("   connect failed:", exc)
        return
    try:
        ws.settimeout(20)
        banner = ws.recv()
        if isinstance(banner, str):
            banner = banner.encode("latin-1")
        print("   banner:", repr(banner))
        ws.send_binary(b"RFB 003.008\n")
        drain(ws, "after version", 15)
    finally:
        try:
            ws.close()
        except Exception:
            pass


def main():
    for p in (STOP, OUT):
        if os.path.exists(p):
            os.remove(p)
    env = dict(os.environ)
    proc = None
    import subprocess
    proc = subprocess.Popen(
        [sys.executable, os.path.join(ROOT, "app", "main.py"), "--selftest", PASSWORD,
         "--out", OUT, "--stop-file", STOP],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT,
        text=True, cwd=ROOT, env=env)
    try:
        url = None
        deadline = time.time() + 120
        while time.time() < deadline:
            if os.path.exists(OUT):
                txt = open(OUT, encoding="utf-8").read().strip()
                if txt.startswith("LINK="):
                    url = txt[5:]
                    break
                if txt.startswith("ERROR="):
                    raise SystemExit(txt)
            time.sleep(1)
        if not url:
            raise SystemExit("no link produced")
        host = re.match(r"https://([^/]+)", url).group(1)
        print("public host:", host)
        session("127.0.0.1:6080", "local", tls=False)
        for i in (1, 2):
            session(host, "tunnel #%d" % i, tls=True)
            time.sleep(2)
    finally:
        open(STOP, "w").close()
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    main()
