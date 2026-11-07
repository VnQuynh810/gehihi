"""
aipro — Google AI Pro provisioning automation package.

Split from the former monolithic `automation_engine.py` for readability:
  - settings   : Chrome path, directories, shared-browser profile, fs utils
  - tokens     : Google One offer tokens + zero-price regex
  - hooks      : injected browser HOOK_JS
  - totp       : 2FA TOTP generator
  - proxy      : proxy parsing/resolution + local auth bridge
  - automator  : GoogleUltraAutomator (the full provisioning pipeline)

`automation_engine.py` is kept as a thin backward-compatible facade.
"""

from .settings import (
    BASE_DIR,
    CHROME_PATH,
    PROFILES_DIR,
    SCREENSHOTS_DIR,
    _find_chrome_path,
    get_profile_dir,
    cleanup_profile_cache,
    is_port_open,
)
from .tokens import (
    _ZERO_PRICE_RE,
    PREFIX,
    TOK_MONTHLY,
    TOK_P1M,
    TOK_PRO5TB_6M,
    TOK_PRO5TB_6M_2,
    TOK_PRO5TB_6M_SS,
    TOK_PRO5TB_12M,
    YT_FREE_TOK,
)
from .hooks import HOOK_JS
from .totp import get_totp
from .proxy import (
    LocalHttpProxyAuthBridge,
    parse_proxy_string,
    resolve_account_proxy,
)
from .automator import GoogleUltraAutomator

__all__ = [
    "BASE_DIR",
    "CHROME_PATH",
    "PROFILES_DIR",
    "SCREENSHOTS_DIR",
    "get_profile_dir",
    "cleanup_profile_cache",
    "is_port_open",
    "HOOK_JS",
    "get_totp",
    "parse_proxy_string",
    "resolve_account_proxy",
    "LocalHttpProxyAuthBridge",
    "GoogleUltraAutomator",
]
