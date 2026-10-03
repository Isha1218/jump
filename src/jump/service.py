"""launchd agent so macOS keeps `jump daemon` running (start at login, restart on crash)."""
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

from . import config

LABEL = "com.jump.daemon"


def plist_path() -> Path:
    return Path.home() / "Library/LaunchAgents" / f"{LABEL}.plist"


def build_plist(jump_bin: str, home: Path = config.HOME) -> dict:
    return {
        "Label": LABEL,
        "ProgramArguments": [jump_bin, "daemon"],
        "RunAtLoad": True,
        "KeepAlive": True,
        "StandardOutPath": str(home / "daemon.log"),
        "StandardErrorPath": str(home / "daemon.log"),
        "EnvironmentVariables": {"JUMP_HOME": str(home)},
    }


def install(path: Path | None = None, load: bool = False) -> Path:
    """Write the plist (and optionally `launchctl load` it). Returns the plist path."""
    path = path or plist_path()
    jump_bin = shutil.which("jump") or str(Path(sys.executable).with_name("jump"))
    path.parent.mkdir(parents=True, exist_ok=True)
    config.HOME.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        plistlib.dump(build_plist(jump_bin), f)
    if load:
        subprocess.run(["launchctl", "unload", str(path)], capture_output=True)
        subprocess.run(["launchctl", "load", str(path)], check=True)
    return path


def uninstall(path: Path | None = None) -> None:
    path = path or plist_path()
    if path.exists():
        subprocess.run(["launchctl", "unload", str(path)], capture_output=True)
        path.unlink()
