"""Dump the raw RFB bytes the server sends after choosing security type None."""
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))
import core  # noqa: E402


def main():
    link = core.RemoteLink(log=lambda m: print("[log]", m))
    try:
        link.start("", 6080)
        import websocket
        ws = websocket.create_connection("ws://127.0.0.1:6080/websockify",
                                         subprotocols=["binary"], timeout=5,
                                         suppress_origin=True)
        buf = b""

        def read(n):
            nonlocal buf
            while len(buf) < n:
                d = ws.recv()
                if isinstance(d, str):
                    d = d.encode("latin-1")
                buf += d
            out, buf = buf[:n], buf[n:]
            return out

        print("banner:", read(12))
        ws.send_binary(b"RFB 003.008\n")
        n = read(1)[0]
        print("security types:", list(read(n)))
        ws.send_binary(b"\x01")   # choose None -> ClientInit happens in noVNC code
        collected = b""
        deadline = time.time() + 3
        ws.settimeout(1.5)
        while time.time() < deadline:
            try:
                d = ws.recv()
            except Exception:
                break
            if isinstance(d, str):
                d = d.encode("latin-1")
            if not d:
                break
            collected += d
        print("bytes after choosing None (no ClientInit sent):", len(collected), collected.hex())
        ws.send_binary(b"\x01")   # ClientInit: shared
        collected2 = b""
        deadline = time.time() + 3
        while time.time() < deadline:
            try:
                d = ws.recv()
            except Exception:
                break
            if isinstance(d, str):
                d = d.encode("latin-1")
            if not d:
                break
            collected2 += d
        print("bytes after ClientInit:", len(collected2), collected2.hex())
        if len(collected2) >= 24:
            import struct
            w, h = struct.unpack(">HH", collected2[:4])
            print("ServerInit -> width=%d height=%d" % (w, h))
        ws.close()
    finally:
        link.stop()


if __name__ == "__main__":
    main()
