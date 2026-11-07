"""Debug helper: is the stall in TightVNC, in websockify, or in the tunnel?"""
import os
import socket
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PASSWORD = "Test1234"


def read_tcp(host, port, send=None, timeout=10):
    with socket.create_connection((host, port), timeout=timeout) as s:
        s.settimeout(timeout)
        banner = s.recv(12)
        if send:
            s.sendall(send)
        more = b""
        try:
            more = s.recv(64)
        except socket.timeout:
            more = b"<timeout>"
        return banner, more


def read_ws(host, port, send=None, timeout=10):
    import websocket
    ws = websocket.create_connection(f"ws://{host}:{port}/websockify",
                                     subprotocols=["binary"], timeout=timeout,
                                     suppress_origin=True)
    try:
        banner = ws.recv()
        if isinstance(banner, str):
            banner = banner.encode("latin-1")
        if send:
            ws.send_binary(send)
        try:
            more = ws.recv()
            if isinstance(more, str):
                more = more.encode("latin-1")
        except Exception as exc:
            more = ("<%s>" % exc.__class__.__name__).encode()
        return banner, more
    finally:
        ws.close()


def main():
    proc = subprocess.Popen(
        [sys.executable, os.path.join(ROOT, "app", "main.py"), "--selftest", PASSWORD],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", cwd=ROOT)
    try:
        deadline = time.time() + 120
        while time.time() < deadline:
            line = proc.stdout.readline()
            if not line:
                raise SystemExit("app exited early")
            print(line.rstrip())
            if line.startswith("LINK="):
                break
        print("\n[A] direct TCP 127.0.0.1:5900")
        try:
            print("   ", read_tcp("127.0.0.1", 5900, b"RFB 003.008\n"))
        except Exception as exc:
            print("    FAILED:", exc)
        print("[B] local websocket 127.0.0.1:6080")
        try:
            print("   ", read_ws("127.0.0.1", 6080, b"RFB 003.008\n"))
        except Exception as exc:
            print("    FAILED:", exc)
        print("[C] listing processes")
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-Process tvnserver,cloudflared,python -ErrorAction SilentlyContinue | "
             "Select-Object ProcessName,Id | Format-Table -AutoSize | Out-String"],
            capture_output=True, text=True)
        print(out.stdout)
    finally:
        proc.stdin.close()
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    main()
