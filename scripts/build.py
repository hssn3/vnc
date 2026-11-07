"""Build VNC Link.

Default  : a single self-contained executable  -> dist\\VNC-Link.exe
           (nothing else is needed; settings.json/links.json appear next to it)
--folder : an onedir bundle                     -> dist\\VNC-Link\\
           plus the self-repair launcher (Run-VNC-Link.bat + bootstrap.ps1)
"""
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NAME = "VNC-Link"


def main():
    folder_mode = "--folder" in sys.argv
    subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "prepare_assets.py")],
                   check=True)

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--onedir" if folder_mode else "--onefile",
        "--windowed",
        "--name", NAME,
        "--add-data", os.path.join(ROOT, "assets") + os.pathsep + "assets",
        "--hidden-import", "websockify.websocketproxy",
        "--hidden-import", "websockify.websockifyserver",
        "--collect-submodules", "websockify",
    ]
    icon = os.path.join(ROOT, "assets", "icon.ico")
    if os.path.isfile(icon):
        cmd += ["--icon", icon]
    cmd.append(os.path.join(ROOT, "app", "main.py"))
    subprocess.run(cmd, check=True, cwd=ROOT)

    if folder_mode:
        app_dir = os.path.join(ROOT, "dist", NAME)
        launcher = os.path.join(ROOT, "launcher")
        if os.path.isdir(launcher):
            for item in os.listdir(launcher):
                src = os.path.join(launcher, item)
                if os.path.isfile(src):
                    shutil.copy2(src, os.path.join(app_dir, item))
            print("launcher copied")
        total = sum(os.path.getsize(os.path.join(d, f))
                    for d, _, files in os.walk(app_dir) for f in files)
        print("\nBuilt:", os.path.join(app_dir, NAME + ".exe"), f"(folder {total / 1e6:.1f} MB)")
    else:
        exe = os.path.join(ROOT, "dist", NAME + ".exe")
        print("\nBuilt:", exe, f"({os.path.getsize(exe) / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
