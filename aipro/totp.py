import base64
import hashlib
import hmac
import re
import struct
import time

def get_totp(secret: str) -> str:
    if not secret:
        return ""
    secret = re.sub(r"[^A-Za-z2-7]", "", secret.strip()).upper()
    if not secret:
        return ""
    missing_padding = len(secret) % 8
    if missing_padding != 0:
        secret += "=" * (8 - missing_padding)
    key = base64.b32decode(secret, casefold=True)
    counter = int(time.time()) // 30
    msg = struct.pack(">Q", counter)
    digest = hmac.new(key, msg, hashlib.sha1).digest()
    offset = digest[19] & 0xF
    code = (struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF) % 1000000
    return f"{code:06d}"
