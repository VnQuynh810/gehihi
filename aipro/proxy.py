import asyncio
import base64
import hashlib
import json
import re
from typing import Any, Dict, Optional

def parse_proxy_string(raw_proxy: str) -> Optional[Dict[str, Any]]:
    """
    Parse various proxy formats into {scheme, host, port, username, password}:
    - http://user:pass@host:port
    - socks5://user:pass@host:port
    - host:port:user:pass
    - user:pass@host:port
    - host:port
    """
    s = (raw_proxy or "").strip()
    if not s:
        return None

    scheme = "http"
    if "://" in s:
        scheme, s = s.split("://", 1)
        scheme = scheme.lower()

    if "@" in s:
        auth_part, host_part = s.rsplit("@", 1)
        user, pwd = (auth_part.split(":", 1) + [""])[:2]
        host, port_str = (host_part.split(":", 1) + ["8080"])[:2]
        return {"scheme": scheme, "host": host, "port": int(port_str), "username": user, "password": pwd}

    parts = s.split(":")
    if len(parts) == 4:
        # host:port:user:pass
        return {
            "scheme": scheme,
            "host": parts[0],
            "port": int(parts[1]),
            "username": parts[2],
            "password": parts[3],
        }
    elif len(parts) == 2:
        return {
            "scheme": scheme,
            "host": parts[0],
            "port": int(parts[1]),
            "username": "",
            "password": "",
        }
    return None


_FLOPPY_CACHE: Dict[str, str] = {}


def resolve_account_proxy(account: Dict[str, Any], target_region: str, proxy_template: str) -> str:
    """
    Resolve the proxy string for an account:
    1. Account-specific proxy overrides global template
    2. If raw string is a FloppyData API Key (32-char alphanumeric without :/@), auto-fetch subuser credentials
    3. Global proxy template replaces {country}/{COUNTRY} and {session}/{SESSION} automatically
    """
    raw = (account.get("proxy") or "").strip() or (proxy_template or "").strip()
    if not raw:
        return ""
    if ":" not in raw and "@" not in raw and len(raw) >= 24:
        if raw in _FLOPPY_CACHE:
            raw = _FLOPPY_CACHE[raw]
        else:
            try:
                import urllib.request
                req = urllib.request.Request(
                    "https://api.floppydata.net/v2/proxy/rotating/subusers",
                    headers={"X-Api-Key": raw, "User-Agent": "Mozilla/5.0", "Connection": "close"},
                )
                resp = json.loads(urllib.request.urlopen(req, timeout=10).read().decode("utf-8"))
                items = resp.get("items") or []
                if items:
                    u = items[0]["username"]
                    p = items[0]["password"]
                    raw = f"http://{u}-type-residential-country-{{COUNTRY}}-session-{{session}}-rotation-0:{p}@geo.g-w.info:10080"
                    _FLOPPY_CACHE[proxy_template.strip()] = raw
            except Exception:
                pass
    cc_lower = (target_region or "TR").lower()
    cc_upper = cc_lower.upper()
    sess_seed = re.sub(r"[^a-zA-Z0-9]", "", account.get("id", "acc")) + hashlib.md5(
        (account.get("email", "") or account.get("id", "")).encode("utf-8")
    ).hexdigest()[:6]
    resolved = (
        raw.replace("{country}", cc_lower)
        .replace("{COUNTRY}", cc_upper)
        .replace("{session}", sess_seed)
        .replace("{SESSION}", sess_seed)
    )
    return resolved


_BLOCKED_PROXY_HOSTS = (
    b"mtalk.google.com",
    b"update.googleapis.com",
    b"safebrowsing.googleapis.com",
    b"google-ohttp-relay-safebrowsing",
    b"optimizationguide-pa.googleapis.com",
    b"gvt1.com",
    b"gvt2.com",
)


class LocalHttpProxyAuthBridge:
    """
    Lightweight local asyncio HTTP/HTTPS CONNECT proxy bridge that injects
    Proxy-Authorization Basic headers, blocks Chrome background telemetry bursts,
    verifies upstream CONNECT responses, and auto-rotates sticky residential sessions
    when a residential peer goes offline or drops connections.
    """

    def __init__(self, local_port: int, upstream: Dict[str, Any]):
        self.local_port = local_port
        self.upstream = upstream
        self.server: Optional[asyncio.AbstractServer] = None
        self.rotation_idx: int = 0
        self.fail_streak: int = 0

    def rotate_session(self):
        self.rotation_idx += 1
        self.fail_streak = 0

    def _current_username(self) -> str:
        base_u = self.upstream.get("username", "")
        if self.rotation_idx <= 0 or "-session-" not in base_u:
            return base_u
        return re.sub(r"(-session-[a-zA-Z0-9_]+)", rf"\1r{self.rotation_idx}", base_u, count=1)

    async def _pipe(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        try:
            while not reader.at_eof():
                data = await reader.read(16384)
                if not data:
                    break
                writer.write(data)
                await writer.drain()
        except Exception:
            pass
        finally:
            try:
                writer.close()
            except Exception:
                pass

    async def _handle_client(self, client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter):
        up_writer = None
        try:
            first_line = await asyncio.wait_for(client_reader.readline(), timeout=15)
            if not first_line:
                client_writer.close()
                return

            fl_lower = first_line.lower()
            if any(bh in fl_lower for bh in _BLOCKED_PROXY_HOSTS):
                client_writer.write(b"HTTP/1.1 502 Bad Gateway\r\nConnection: close\r\n\r\n")
                await client_writer.drain()
                client_writer.close()
                return

            headers = []
            while True:
                line = await asyncio.wait_for(client_reader.readline(), timeout=15)
                if not line or line in (b"\r\n", b"\n"):
                    break
                if not line.lower().startswith(b"proxy-authorization:"):
                    headers.append(line)

            is_connect = fl_lower.startswith(b"connect ")
            up_reader = None

            for attempt in range(3):
                try:
                    up_reader, up_writer = await asyncio.wait_for(
                        asyncio.open_connection(self.upstream["host"], self.upstream["port"]),
                        timeout=12,
                    )
                    auth_str = f"{self._current_username()}:{self.upstream['password']}"
                    b64_auth = base64.b64encode(auth_str.encode("utf-8")).decode("ascii")
                    auth_header = f"Proxy-Authorization: Basic {b64_auth}\r\n".encode("ascii")

                    up_writer.write(first_line)
                    for h in headers:
                        up_writer.write(h)
                    up_writer.write(auth_header)
                    up_writer.write(b"\r\n")
                    await up_writer.drain()

                    if is_connect:
                        resp_hdr = await asyncio.wait_for(up_reader.readuntil(b"\r\n\r\n"), timeout=12)
                        status_line = resp_hdr.split(b"\r\n", 1)[0]
                        if b" 200 " not in status_line:
                            raise RuntimeError(f"Upstream CONNECT non-200: {status_line!r}")
                        self.fail_streak = 0
                        client_writer.write(resp_hdr)
                        await client_writer.drain()
                    break
                except Exception:
                    if up_writer:
                        try:
                            up_writer.close()
                        except Exception:
                            pass
                        up_writer = None
                    self.fail_streak += 1
                    if self.fail_streak >= 3 or attempt >= 1:
                        self.rotate_session()
                    if attempt == 2:
                        raise
                    await asyncio.sleep(0.5)

            await asyncio.gather(
                self._pipe(client_reader, up_writer),
                self._pipe(up_reader, client_writer),
            )
        except Exception:
            try:
                client_writer.close()
            except Exception:
                pass
            if up_writer:
                try:
                    up_writer.close()
                except Exception:
                    pass

    async def start(self):
        self.server = await asyncio.start_server(self._handle_client, "127.0.0.1", self.local_port)

    async def stop(self):
        if self.server:
            self.server.close()
            try:
                await self.server.wait_closed()
            except Exception:
                pass


