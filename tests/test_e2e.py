"""End-to-end test: start the app pipeline, verify the public tunnel URL,
noVNC page, and a full authenticated RFB session over WebSocket."""
import os
import re
import socket
import struct
import subprocess
import sys
import time
import urllib.request

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))
from vncrypt import challenge_response  # noqa: E402

PASSWORD = "Test1234"


def recvn(ws, n):
    buf = b""
    while len(buf) < n:
        chunk = ws.recv()
        if isinstance(chunk, str):
            chunk = chunk.encode("latin-1")
        if not chunk:
            raise ConnectionError("websocket closed")
        buf += chunk
    return buf


def rfb_session(host, password, expect_ok):
    import websocket
    ws = websocket.create_connection(
        f"wss://{host}/websockify", subprotocols=["binary"], timeout=30,
        header={"User-Agent": "Mozilla/5.0"}, suppress_origin=True)
    try:
        banner = recvn(ws, 12)
        assert banner.startswith(b"RFB "), banner
        ws.send_binary(b"RFB 003.008\n")
        n = recvn(ws, 1)[0]
        assert n > 0, "server offered no security types"
        types = list(recvn(ws, n))
        assert 2 in types, types
        ws.send_binary(b"\x02")
        challenge = recvn(ws, 16)
        ws.send_binary(challenge_response(challenge, password))
        result = struct.unpack(">I", recvn(ws, 4))[0]
        if not expect_ok:
            assert result != 0, "wrong password was accepted!"
            print("  wrong password correctly rejected")
            return
        assert result == 0, f"auth failed with correct password (result={result})"
        ws.send_binary(b"\x01")
        hdr = recvn(ws, 24)
        w, h = struct.unpack(">HH", hdr[:4])
        name_len = struct.unpack(">I", hdr[20:24])[0]
        name = recvn(ws, name_len)
        print(f"  authenticated OK, desktop {w}x{h} name={name!r}")
        ws.send_binary(struct.pack(">BBHHHH", 3, 0, 0, 0, min(w, 64), min(h, 64)))
        assert recvn(ws, 1)[0] == 0, "no framebuffer update"
        print("  framebuffer update received over the public tunnel")
    finally:
        ws.close()


def main():
    proc = subprocess.Popen(
        [sys.executable, os.path.join(ROOT, "app", "main.py"), "--selftest", PASSWORD],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", cwd=ROOT)
    url = None
    deadline = time.time() + 120
    try:
        while time.time() < deadline:
            line = proc.stdout.readline()
            if not line:
                raise RuntimeError("app exited early")
            print(line.rstrip())
            if line.startswith("LINK="):
                url = line.strip()[5:]
                break
        assert url, "no LINK produced"
        host = re.match(r"https://([^/]+)", url).group(1)
        print("\n[1] public URL:", url)

        print("[2] fetching noVNC page through the tunnel ...")
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        body = None
        for attempt in range(12):
            try:
                body = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "replace")
                break
            except Exception as e:
                print(f"  attempt {attempt+1}: {e}; waiting for DNS/tunnel ...")
                time.sleep(5)
        assert body, "could not fetch noVNC page through tunnel"
        assert "noVNC" in body, "page is not noVNC"
        print("  noVNC page served correctly (%d bytes)" % len(body))

        print("[3] RFB session with CORRECT password through the tunnel ...")
        rfb_session(host, PASSWORD, expect_ok=True)

        print("[4] RFB session with WRONG password through the tunnel ...")
        rfb_session(host, "Wrong999", expect_ok=False)

        print("\nALL E2E TESTS PASSED")
    finally:
        proc.stdin.close()
        try:
            proc.wait(timeout=20)
            print("app shut down cleanly, exit code", proc.returncode)
        except subprocess.TimeoutExpired:
            proc.kill()
            print("WARNING: app had to be killed")
        for port in (5900, 6080):
            with socket.socket() as s:
                try:
                    s.bind(("127.0.0.1", port))
                    print(f"port {port} released")
                except OSError:
                    print(f"WARNING: port {port} still occupied")


if __name__ == "__main__":
    main()