import os
import re
import shutil
import socket
import tempfile

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
# Project root = parent of the `aipro` package dir, so screenshots/ and
# config.json stay at the repository root (not inside the package).
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCREENSHOTS_DIR = os.path.join(BASE_DIR, "screenshots")

# Chrome profiles are NO LONGER persisted. Each browser launch gets a throwaway
# profile created under the system temp folder (outside the project, so it never
# bloats the repo) and it is deleted again as soon as the run finishes. This keeps
# the machine light and means no login/cookie data is kept between runs.
PROFILES_DIR = os.path.join(tempfile.gettempdir(), "gaipro_profiles")

os.makedirs(SCREENSHOTS_DIR, exist_ok=True)


def get_profile_dir(acc_id: str, email: str) -> str:
    """Create and return a fresh throwaway Chrome profile directory for one launch.
    Nothing is reused between launches (profile saving has been removed)."""
    os.makedirs(PROFILES_DIR, exist_ok=True)
    safe_name = re.sub(r"[^a-zA-Z0-9_.-]", "_", email or acc_id)[:40]
    return tempfile.mkdtemp(prefix=f"profile_{safe_name}_", dir=PROFILES_DIR)


def cleanup_profile_cache() -> int:
    """Delete any leftover throwaway Chrome profiles. Returns how many were removed.
    Safe to call at startup: profiles still locked by a running Chrome are skipped."""
    removed = 0
    if not os.path.isdir(PROFILES_DIR):
        return 0
    for name in os.listdir(PROFILES_DIR):
        p = os.path.join(PROFILES_DIR, name)
        if os.path.isdir(p):
            before = os.path.exists(p)
            shutil.rmtree(p, ignore_errors=True)
            if before and not os.path.exists(p):
                removed += 1
    return removed


def is_port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.8)
        return s.connect_ex(("127.0.0.1", port)) == 0
