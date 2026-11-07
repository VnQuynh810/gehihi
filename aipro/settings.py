import os
import re
import socket

def _find_chrome_path() -> str:
    candidates = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    return candidates[0]


CHROME_PATH = _find_chrome_path()
# Project root = parent of the `aipro` package dir, so profiles/, screenshots/
# and config.json stay at the repository root (not inside the package).
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROFILES_DIR = os.path.join(BASE_DIR, "profiles")
SCREENSHOTS_DIR = os.path.join(BASE_DIR, "screenshots")

os.makedirs(PROFILES_DIR, exist_ok=True)
os.makedirs(SCREENSHOTS_DIR, exist_ok=True)


def get_profile_dir(acc_id: str, email: str) -> str:
    safe_name = re.sub(r"[^a-zA-Z0-9_.-]", "_", email or acc_id)
    path = os.path.join(PROFILES_DIR, f"profile_{safe_name}")
    os.makedirs(path, exist_ok=True)
    return path


def is_port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.8)
        return s.connect_ex(("127.0.0.1", port)) == 0
