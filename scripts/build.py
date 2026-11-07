"""Build the portable VNC-Link.exe (single file, assets embedded)."""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "prepare_assets.py")], check=True)
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean", "--onefile", "--windowed",
        "--name", "VNC-Link",
        "--add-data", os.path.join(ROOT, "assets") + os.pathsep + "assets",
        "--hidden-import", "websockify.websocketproxy",
        "--collect-submodules", "websockify",
    ]
    icon = os.path.join(ROOT, "assets", "icon.ico")
    if os.path.isfile(icon):
        cmd += ["--icon", icon]
    cmd.append(os.path.join(ROOT, "app", "main.py"))
    subprocess.run(cmd, check=True, cwd=ROOT)
    exe = os.path.join(ROOT, "dist", "VNC-Link.exe")
    print("\nBuilt:", exe, f"({os.path.getsize(exe) / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()