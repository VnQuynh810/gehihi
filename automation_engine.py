"""
Backward-compatible facade.

The implementation now lives in the `aipro` package (see aipro/__init__.py).
This module re-exports the same public names so existing imports such as
`from automation_engine import GoogleUltraAutomator, ...` keep working.
"""

from aipro.settings import (
    BASE_DIR,
    CHROME_PATH,
    PROFILES_DIR,
    SCREENSHOTS_DIR,
    _find_chrome_path,
    get_profile_dir,
    is_port_open,
)
from aipro.tokens import (
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
from aipro.hooks import HOOK_JS
from aipro.totp import get_totp
from aipro.proxy import (
    LocalHttpProxyAuthBridge,
    parse_proxy_string,
    resolve_account_proxy,
)
from aipro.automator import GoogleUltraAutomator

__all__ = [
    "BASE_DIR",
    "CHROME_PATH",
    "PROFILES_DIR",
    "SCREENSHOTS_DIR",
    "_find_chrome_path",
    "get_profile_dir",
    "is_port_open",
    "HOOK_JS",
    "get_totp",
    "parse_proxy_string",
    "resolve_account_proxy",
    "LocalHttpProxyAuthBridge",
    "GoogleUltraAutomator",
]
