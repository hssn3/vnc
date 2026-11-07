"""End-to-end test: start the pipeline, verify the public tunnel URL, the noVNC
page, and a full authenticated RFB session over the public WebSocket.

Usage:
    python tests\\test_e2e.py                 # runs the source app
    python tests\\test_e2e.py --exe dist\\VNC-Link.exe   # runs the frozen exe
"""
import os
import re
import socket
import struct
import subprocess
import sys
import tempfile
import time
import urllib.request

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))
from vncrypt import challenge_response  # noqa: E402

PASSWORD = os.environ.get("VNC_LINK_TEST_PASSWORD", "Test1234")


class Reader:
    """Buffered reader: ws.recv() returns whole frames, so keep the surplus."""

    def __init__(self, ws):
        self.ws = ws
        self.buf = b""

    def read(self, n):
        while len(self.buf) < n:
            chunk = self.ws.recv()
            if isinstance(chunk, str):
                chunk = chunk.encode("latin-1")
            if not chunk:
                raise ConnectionError("websocket closed")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out


def rfb_session(host, password, expect_ok):
    import websocket
    ws = websocket.create_connection(
        f"wss://{host}/websockify", subprotocols=["binary"], timeout=30,
        header={"User-Agent": "Mozilla/5.0"}, suppress_origin=True)
    try:
        rd = Reader(ws)
        banner = rd.read(12)
        assert banner.startswith(b"RFB "), banner
        print("   server version:", banner.strip().decode())
        ws.send_binary(b"RFB 003.008\n")
        n = rd.read(1)[0]
        assert n > 0, "server offered no security types"
        types = list(rd.read(n))
        assert 2 in types, types
        print("   security types offered:", types)
        ws.send_binary(b"\x02")
        challenge = rd.read(16)
        ws.send_binary(challenge_response(challenge, password))
        result = struct.unpack(">I", rd.read(4))[0]
        if not expect_ok:
            assert result != 0, "wrong password was accepted!"
            print("   wrong password correctly rejected")
            return
        assert result == 0, f"auth failed with correct password (result={result})"
        ws.send_binary(b"\x01")
        hdr = rd.read(24)
        w, h = struct.unpack(">HH", hdr[:4])
        name_len = struct.unpack(">I", hdr[20:24])[0]
        name = rd.read(name_len)
        print(f"   authenticated OK, desktop {w}x{h} name={name!r}")
        ws.send_binary(struct.pack(">BBHHHH", 3, 0, 0, 0, min(w, 64), min(h, 64)))
        assert rd.read(1)[0] == 0, "no framebuffer update"
        print("   framebuffer update received over the public tunnel")
    finally:
        ws.close()


def start_app(exe):
    """Returns (proc, get_link, stop)."""
    if exe:
        out = os.path.join(tempfile.gettempdir(), "vnclink_e2e.out")
        stop = os.path.join(tempfile.gettempdir(), "vnclink_e2e.stop")
        for p in (out, stop):
            if os.path.exists(p):
                os.remove(p)
        proc = subprocess.Popen(
            [exe, "--selftest", PASSWORD, "--out", out, "--stop-file", stop],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, cwd=os.path.dirname(os.path.abspath(exe)))

        def get_link(timeout=150):
            deadline = time.time() + timeout
            while time.time() < deadline:
                if os.path.exists(out):
                    txt = open(out, encoding="utf-8").read().strip()
                    if txt.startswith("LINK="):
                        return txt[5:]
                    if txt.startswith("ERROR="):
                        raise RuntimeError(txt[6:])
                if proc.poll() is not None:
                    raise RuntimeError("exe exited early (code %s)" % proc.returncode)
                time.sleep(1)
            raise RuntimeError("no link produced by exe")

        def do_stop():
            open(stop, "w").close()
            try:
                proc.wait(timeout=40)
            except subprocess.TimeoutExpired:
                proc.kill()

        return proc, get_link, do_stop

    proc = subprocess.Popen(
        [sys.executable, os.path.join(ROOT, "app", "main.py"), "--selftest", PASSWORD],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", cwd=ROOT)

    def get_link(timeout=150):
        deadline = time.time() + timeout
        while time.time() < deadline:
            line = proc.stdout.readline()
            if not line:
                raise RuntimeError("app exited early")
            print(line.rstrip())
            if line.startswith("LINK="):
                return line.strip()[5:]
        raise RuntimeError("no LINK produced")

    def do_stop():
        proc.stdin.close()
        try:
            proc.wait(timeout=25)
            print("app shut down cleanly, exit code", proc.returncode)
        except subprocess.TimeoutExpired:
            proc.kill()

    return proc, get_link, do_stop


def main():
    exe = None
    if "--exe" in sys.argv:
        exe = os.path.abspath(sys.argv[sys.argv.index("--exe") + 1])
        assert os.path.isfile(exe), exe
        print("target: frozen exe", exe)
    else:
        print("target: source app")

    proc, get_link, do_stop = start_app(exe)
    try:
        url = get_link()
        host = re.match(r"https://([^/]+)", url).group(1)
        print("\n[1] public URL:", url)

        print("[2] fetching noVNC page through the tunnel ...")
        body = None
        for attempt in range(12):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                body = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "replace")
                break
            except Exception as exc:
                print(f"  attempt {attempt+1}: {exc}; waiting for DNS ...")
                time.sleep(5)
        assert body, "could not fetch noVNC page through the tunnel"
        assert "noVNC" in body, "page is not noVNC"
        print("  noVNC page served correctly (%d bytes)" % len(body))

        print("[3] RFB session with CORRECT password through the tunnel ...")
        rfb_session(host, PASSWORD, expect_ok=True)

        print("[4] RFB session with WRONG password through the tunnel ...")
        rfb_session(host, "Wrong999", expect_ok=False)

        print("\nALL E2E TESTS PASSED")
    finally:
        do_stop()
        for port in (5900, 6080):
            with socket.socket() as s:
                try:
                    s.bind(("127.0.0.1", port))
                    print(f"port {port} released")
                except OSError:
                    print(f"WARNING: port {port} still occupied")


if __name__ == "__main__":
    main()
