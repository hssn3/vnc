"""Build the portable VNC-Link app as a self-contained folder (dist\\VNC-Link).

onedir is deliberate: every prerequisite (python runtime, cloudflared, noVNC,
TightVNC) lives inside the application folder instead of being unpacked into
%TEMP% on every start.
"""
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NAME = "VNC-Link"


def main():
    subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "prepare_assets.py")], check=True)
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean", "--onedir", "--windowed",
        "--name", NAME,
        "--add-data", os.path.join(ROOT, "assets") + os.pathsep + "assets",
        "--hidden-import", "websockify.websocketproxy",
        "--collect-submodules", "websockify",
    ]
    icon = os.path.join(ROOT, "assets", "icon.ico")
    if os.path.isfile(icon):
        cmd += ["--icon", icon]
    cmd.append(os.path.join(ROOT, "app", "main.py"))
    subprocess.run(cmd, check=True, cwd=ROOT)
    exe = os.path.join(ROOT, "dist", NAME, NAME + ".exe")

    # ship the one-click launcher (self-repair + start) inside the app folder
    launcher = os.path.join(ROOT, "launcher")
    if os.path.isdir(launcher):
        for item in os.listdir(launcher):
            src = os.path.join(launcher, item)
            if os.path.isfile(src):
                shutil.copy2(src, os.path.join(ROOT, "dist", NAME, item))
        print("launcher copied")

    total = sum(os.path.getsize(os.path.join(d, f))
                for d, _, files in os.walk(os.path.join(ROOT, "dist", NAME))
                for f in files)
    print("\nBuilt:", exe, f"(folder {total / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
