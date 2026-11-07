"""Download build-time assets that are too large / external for git."""
import os
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS = os.path.join(ROOT, "assets")

DOWNLOADS = {
    "cloudflared.exe": "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe",
}


def main():
    os.makedirs(ASSETS, exist_ok=True)
    for name, url in DOWNLOADS.items():
        dest = os.path.join(ASSETS, name)
        if os.path.isfile(dest) and os.path.getsize(dest) > 1_000_000:
            print(f"exists: {name}")
            continue
        print(f"downloading {name} ...")
        urllib.request.urlretrieve(url, dest)
        print(f"  -> {os.path.getsize(dest)} bytes")
    for required in ("novnc/vnc.html", "tvnserver/tvnserver.exe"):
        if not os.path.exists(os.path.join(ASSETS, required)):
            raise SystemExit(f"missing {required} — should be committed in the repo")
    print("assets ready")


if __name__ == "__main__":
    main()