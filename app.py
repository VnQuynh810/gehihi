import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import time

# Ensure emoji / non-cp1252 characters in logs don't crash print() on Windows consoles.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
from typing import Dict, Any, List, Optional
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from automation_engine import (
    GoogleUltraAutomator,
    LocalHttpProxyAuthBridge,
    get_totp,
    get_profile_dir,
    cleanup_profile_cache,
    resolve_account_proxy,
    parse_proxy_string,
    CHROME_PATH,
    BASE_DIR,
    PROFILES_DIR,
    SCREENSHOTS_DIR,
)

CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")
os.makedirs(TEMPLATES_DIR, exist_ok=True)
os.makedirs(SCREENSHOTS_DIR, exist_ok=True)

app = FastAPI(title="Google AI Pro (5 TB) 2-Year Zero-Cost Automation Console")
app.mount("/screenshots", StaticFiles(directory=SCREENSHOTS_DIR), name="screenshots")

# Global runtime state
LOGS: List[str] = [
    f"[{time.strftime('%H:%M:%S')}] [SYSTEM] 🚀 Google AI Pro (5 TB) 2-Year Zero-Cost (US$0.00 -> 24 Months) Automation Console is ready!"
]
ACTIVE_TASKS: Dict[str, GoogleUltraAutomator] = {}
BATCH_RUNNING: bool = False
STOP_BATCH: bool = False


DEFAULT_CONFIG: Dict[str, Any] = {
    "target_region": "US",
    "target_plan": "pro_2year_zero",
    "auto_cancel": True,
    "proxy_template": "",
    "card": {
        "number": "",
        "exp_month": "",
        "exp_year": "",
        "cvc": "",
        "holder": "",
        "address_line1": "700 SW 5th Ave",
        "postal_code": "97201",
        "locality": "Portland",
        "province": "OR",
    },
    "accounts": [],
}


def load_config() -> Dict[str, Any]:
    if not os.path.exists(CONFIG_PATH):
        cfg = json.loads(json.dumps(DEFAULT_CONFIG))
        save_config(cfg)
        return cfg
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        data = json.loads(json.dumps(DEFAULT_CONFIG))
        save_config(data)
        return data

    # Ensure required top-level and default tax-free address keys exist
    merged = json.loads(json.dumps(DEFAULT_CONFIG))
    for k in ("target_region", "target_plan", "auto_cancel", "proxy_template", "accounts"):
        if k in data:
            merged[k] = data[k]
    if isinstance(data.get("card"), dict):
        merged["card"].update(data["card"])
    return merged


def save_config(cfg: Dict[str, Any]):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


# Ensure config.json is generated on startup if missing
load_config()


def append_log(line: str):
    try:
        print(line, flush=True)
    except UnicodeEncodeError:
        enc = (getattr(sys.stdout, "encoding", None) or "utf-8")
        print(line.encode(enc, errors="replace").decode(enc, errors="replace"), flush=True)
    LOGS.append(line)
    if len(LOGS) > 500:
        del LOGS[: len(LOGS) - 500]


# Profile saving is disabled — remove any throwaway Chrome profiles left over from
# previous sessions so nothing accumulates on disk.
try:
    _removed_profiles = cleanup_profile_cache()
    if _removed_profiles:
        append_log(
            f"[{time.strftime('%H:%M:%S')}] [SYSTEM] 🧹 Cleaned up {_removed_profiles} leftover temporary Chrome profile(s)"
        )
except Exception:
    pass


class ConfigUpdateReq(BaseModel):
    target_region: str = "US"
    target_plan: str = "pro_2year_zero"
    auto_cancel: bool = True
    proxy_template: str = ""
    card: Dict[str, str]


class ImportAccountsReq(BaseModel):
    raw_text: str


class RunSingleReq(BaseModel):
    account_id: str
    start_step: int = 1
    manual_login: bool = False


class RunBatchReq(BaseModel):
    account_ids: Optional[List[str]] = None
    start_step: int = 1


class AccountActionReq(BaseModel):
    account_id: str


@app.get("/", response_class=HTMLResponse)
async def index():
    html_path = os.path.join(TEMPLATES_DIR, "index.html")
    with open(html_path, "r", encoding="utf-8") as f:
        return f.read()


@app.get("/api/state")
async def get_state():
    cfg = load_config()
    rem_sec = 30 - (int(time.time()) % 30)
    accounts_out = []
    for acc in cfg.get("accounts", []):
        item = dict(acc)
        try:
            item["totp_now"] = get_totp(acc.get("totp_secret", ""))
        except Exception:
            item["totp_now"] = "------"
        item["totp_rem"] = rem_sec
        item["is_running"] = acc["id"] in ACTIVE_TASKS
        accounts_out.append(item)

    return {
        "target_region": cfg.get("target_region", "US"),
        "target_plan": cfg.get("target_plan", "pro_2year_zero"),
        "auto_cancel": cfg.get("auto_cancel", True),
        "proxy_template": cfg.get("proxy_template", ""),
        "card": cfg.get("card", {}),
        "accounts": accounts_out,
        "logs": LOGS[-250:],
        "batch_running": BATCH_RUNNING,
        "active_count": len(ACTIVE_TASKS),
    }


@app.post("/api/config")
async def update_config(req: ConfigUpdateReq):
    cfg = load_config()
    cfg["target_region"] = req.target_region
    cfg["target_plan"] = req.target_plan
    cfg["auto_cancel"] = req.auto_cancel
    cfg["proxy_template"] = req.proxy_template.strip()
    cfg["card"] = req.card
    save_config(cfg)
    append_log(f"[{time.strftime('%H:%M:%S')}] [CONFIG] ✅ Configuration saved (Target Region: {req.target_region} | Proxy Template: {'Enabled' if req.proxy_template.strip() else 'System Default'})")
    return {"ok": True}


class ProxyTestReq(BaseModel):
    target_region: str = "TR"
    proxy_template: str = ""


@app.post("/api/proxy/test")
async def test_proxy(req: ProxyTestReq):
    cfg = load_config()
    tpl = (req.proxy_template or cfg.get("proxy_template") or "").strip()
    region = (req.target_region or cfg.get("target_region") or "TR").upper()
    resolved = resolve_account_proxy({"id": f"probe{int(time.time())%1000}", "email": "probe@gmail.com"}, region, tpl)
    if not resolved:
        return {"ok": False, "message": "No proxy template specified"}

    def _sync_probe():
        import urllib.request
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({"http": resolved, "https": resolved}))
        r = urllib.request.Request("https://ipwho.is/", headers={"User-Agent": "Mozilla/5.0"})
        data = json.loads(opener.open(r, timeout=15).read().decode("utf-8"))
        bal_gb = None
        api_key = cfg.get("floppy_api_key", "")
        if api_key:
            try:
                rb = urllib.request.Request(
                    "https://api.floppydata.net/v2/account/balances",
                    headers={"X-Api-Key": api_key, "User-Agent": "Mozilla/5.0", "Connection": "close"},
                )
                bdata = json.loads(urllib.request.urlopen(rb, timeout=10).read().decode("utf-8"))
                bal_gb = bdata.get("proxy", {}).get("rotating", {}).get("total", {}).get("traffic", {}).get("availableGb")
            except Exception:
                pass
        return data, bal_gb

    try:
        data, bal_gb = await asyncio.to_thread(_sync_probe)
        ip = data.get("ip", "Unknown")
        cc = data.get("country_code", "??")
        country = data.get("country", "")
        city = data.get("city", "")
        org = data.get("connection", {}).get("org", "")
        flag = "🇧🇴" if cc == "BO" else ("🇹🇷" if cc == "TR" else "🌐")
        bal_str = f" | FloppyData Remaining Traffic: {bal_gb} GB" if bal_gb is not None else ""
        msg = f"{flag} Connection successful! Egress IP: {ip} ({country} · {city} | {org}){bal_str}"
        append_log(f"[{time.strftime('%H:%M:%S')}] [PROXY] ✅ {msg}")
        return {"ok": True, "message": msg, "ip": ip, "country_code": cc, "balance_gb": bal_gb}
    except Exception as e:
        err = f"Proxy test failed: {e}"
        append_log(f"[{time.strftime('%H:%M:%S')}] [PROXY] ❌ {err}")
        return {"ok": False, "message": err}


@app.post("/api/accounts/import")
async def import_accounts(req: ImportAccountsReq):
    cfg = load_config()
    existing = cfg.get("accounts", [])
    existing_emails = {a["email"].lower().replace(".", "") for a in existing}

    lines = [l.strip() for l in req.raw_text.splitlines() if l.strip()]
    added = 0
    for line in lines:
        # Support delimiters: ----, —, |, /, \t, comma, or whitespace
        if "----" in line:
            parts = [p.strip() for p in line.split("----") if p.strip()]
        elif "—" in line:
            parts = [p.strip() for p in line.split("—") if p.strip()]
        elif "|" in line:
            parts = [p.strip() for p in line.split("|") if p.strip()]
        elif "/" in line and "@" in line and "http://" not in line and "https://" not in line:
            parts = [p.strip() for p in line.split("/") if p.strip()]
        elif "\t" in line:
            parts = [p.strip() for p in line.split("\t") if p.strip()]
        elif "," in line:
            parts = [p.strip() for p in line.split(",") if p.strip()]
        else:
            parts = [p.strip() for p in re.split(r"\s+", line) if p.strip()]

        if len(parts) < 2:
            continue

        # Detect if first part is a label (no '@') or email (has '@')
        if "@" in parts[0]:
            email = parts[0]
            password = parts[1]
            rest = parts[2:]
            used_ids = {a.get("id") for a in existing}
            next_num = 1
            while f"Account_{next_num}" in used_ids:
                next_num += 1
            acc_id = f"Account_{next_num}"
        elif len(parts) >= 3 and "@" in parts[1]:
            acc_id = parts[0]
            email = parts[1]
            password = parts[2]
            rest = parts[3:]
        else:
            continue

        recovery_email = ""
        totp_secret = ""
        proxy_str = ""
        for token in rest:
            t_clean = token.replace(" ", "")
            if "@" in token and not token.lower().startswith(("http://", "https://", "socks5://")):
                if token.lower() != email.lower() and not recovery_email:
                    recovery_email = token.strip()
            elif token.lower().startswith(("http://", "https://", "socks5://")) or re.match(r"^\d+\.\d+\.\d+\.\d+:\d+", token):
                proxy_str = token.strip()
            elif len(t_clean) >= 16 and re.match(r"^[A-Za-z2-7]+=*$", t_clean) and not totp_secret:
                totp_secret = t_clean

        norm_email = email.lower().replace(".", "")
        if norm_email in existing_emails:
            for a in existing:
                if a["email"].lower().replace(".", "") == norm_email:
                    a["password"] = password
                    if recovery_email:
                        a["recovery_email"] = recovery_email
                    if totp_secret:
                        a["totp_secret"] = totp_secret
                    if proxy_str:
                        a["proxy"] = proxy_str
            continue

        existing.append(
            {
                "id": acc_id,
                "email": email,
                "password": password,
                "recovery_email": recovery_email,
                "totp_secret": totp_secret,
                "proxy": proxy_str,
                "ip_info": "",
                "status": "pending",
                "current_step": 0,
                "step_message": "Newly imported account (Pending)",
                "expiry_date": "-",
                "last_screenshot": "",
            }
        )
        existing_emails.add(norm_email)
        added += 1

    cfg["accounts"] = existing
    save_config(cfg)
    append_log(f"[{time.strftime('%H:%M:%S')}] [IMPORT] ✅ Successfully imported/updated {added} accounts (Total: {len(existing)})")
    return {"ok": True, "added": added, "total": len(existing)}


@app.post("/api/accounts/delete")
async def delete_account(req: AccountActionReq):
    if req.account_id in ACTIVE_TASKS:
        raise HTTPException(status_code=400, detail="This account is currently running. Please stop the task first.")
    cfg = load_config()
    accounts = cfg.get("accounts", [])
    target_acc = next((a for a in accounts if a.get("id") == req.account_id), None)
    cfg["accounts"] = [a for a in accounts if a.get("id") != req.account_id]
    save_config(cfg)

    # Stop any manual browser proxy bridge for this account
    old_bridge = MANUAL_BRIDGES.pop(req.account_id, None)
    if old_bridge:
        try:
            await old_bridge.stop()
        except Exception:
            pass

    removed_profile = False
    removed_shots = 0
    acc_email = (target_acc.get("email") if target_acc else "") or ""
    safe_email = re.sub(r"[^a-zA-Z0-9_.-]", "_", acc_email or req.account_id)

    # Remove only this account's isolated Chrome profile folder
    candidate_dirs = {
        os.path.join(PROFILES_DIR, f"profile_{safe_email}"),
        os.path.join(PROFILES_DIR, f"profile_{req.account_id}"),
    }
    for pdir in candidate_dirs:
        if os.path.isdir(pdir):
            lockfile_path = os.path.join(pdir, "lockfile")
            if os.path.exists(lockfile_path):
                try:
                    os.remove(lockfile_path)
                except OSError:
                    profile_folder = os.path.basename(os.path.normpath(pdir))
                    try:
                        ps_cmd = (
                            f"Get-CimInstance Win32_Process -Filter \"Name = 'chrome.exe'\" | "
                            f"Where-Object {{ $_.CommandLine -like '*{profile_folder}*' }} | "
                            f"ForEach-Object {{ Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }}"
                        )
                        subprocess.run(
                            ["powershell", "-NoProfile", "-Command", ps_cmd],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            timeout=6,
                        )
                        await asyncio.sleep(0.5)
                    except Exception:
                        pass
            shutil.rmtree(pdir, ignore_errors=True)
            if not os.path.exists(pdir):
                removed_profile = True

    # Remove only this account's screenshots
    last_shot = (target_acc.get("last_screenshot") if target_acc else "") or ""
    if os.path.isdir(SCREENSHOTS_DIR):
        for fname in os.listdir(SCREENSHOTS_DIR):
            if (
                fname.startswith(f"{req.account_id}_")
                or (safe_email and f"_{safe_email}_" in fname)
                or (safe_email and fname.startswith(f"{safe_email}_"))
                or (last_shot and fname == last_shot)
            ):
                try:
                    os.remove(os.path.join(SCREENSHOTS_DIR, fname))
                    removed_shots += 1
                except OSError:
                    pass

    label = f"{req.account_id} ({acc_email})" if acc_email else req.account_id
    append_log(
        f"[{time.strftime('%H:%M:%S')}] [ACCOUNT] 🗑️ Removed {label} "
        f"(profile deleted: {'yes' if removed_profile else 'none'}, screenshots deleted: {removed_shots})"
    )
    return {"ok": True, "removed_profile": removed_profile, "removed_screenshots": removed_shots}


@app.post("/api/accounts/reset")
async def reset_account(req: AccountActionReq):
    cfg = load_config()
    for a in cfg.get("accounts", []):
        if a["id"] == req.account_id:
            a["status"] = "pending"
            a["current_step"] = 0
            a["step_message"] = "Status reset (Pending)"
            break
    save_config(cfg)
    append_log(f"[{time.strftime('%H:%M:%S')}] [ACCOUNT] 🔄 Reset status of account {req.account_id} to Pending")
    return {"ok": True}


async def _run_account_worker(account_id: str, start_step: int = 1, port: int = 9320, manual_login: bool = False):
    import importlib
    import automation_engine
    importlib.reload(automation_engine)
    cfg = load_config()
    target_acc = None
    for a in cfg.get("accounts", []):
        if a["id"] == account_id:
            target_acc = a
            break
    if not target_acc:
        return

    def on_state_patch(patch: Dict[str, Any]):
        c = load_config()
        for acc_item in c.get("accounts", []):
            if acc_item["id"] == account_id:
                acc_item.update(patch)
                break
        save_config(c)

    automator = automation_engine.GoogleUltraAutomator(
        account=target_acc,
        card=cfg.get("card", {}),
        target_region=cfg.get("target_region", "US"),
        target_plan=cfg.get("target_plan", "pro_2year_zero"),
        auto_cancel=cfg.get("auto_cancel", True),
        proxy_template=cfg.get("proxy_template", ""),
        port=port,
        on_log=append_log,
        on_state=on_state_patch,
    )
    automator.manual_login = manual_login
    ACTIVE_TASKS[account_id] = automator
    try:
        await automator.run_pipeline(start_step=start_step)
    finally:
        ACTIVE_TASKS.pop(account_id, None)


@app.post("/api/run/single")
async def run_single(req: RunSingleReq):
    if req.account_id in ACTIVE_TASKS:
        raise HTTPException(status_code=400, detail="This account is already running")
    port = 9320 + (len(ACTIVE_TASKS) % 10)
    asyncio.create_task(
        _run_account_worker(
            req.account_id,
            start_step=req.start_step,
            port=port,
            manual_login=req.manual_login,
        )
    )
    return {"ok": True}


@app.post("/api/resume")
async def resume_account(req: AccountActionReq):
    """Resume a task that is paused for manual login."""
    automator = ACTIVE_TASKS.get(req.account_id)
    if not automator:
        raise HTTPException(status_code=400, detail="This account is not running (nothing to resume)")
    automator.resume_requested = True
    append_log(f"[{time.strftime('%H:%M:%S')}] [RESUME] ▶ Resume requested for {req.account_id} — continuing after manual login")
    return {"ok": True}


@app.post("/api/run/batch")
async def run_batch(req: RunBatchReq):
    global BATCH_RUNNING, STOP_BATCH
    if BATCH_RUNNING:
        raise HTTPException(status_code=400, detail="Batch queue is already running")

    cfg = load_config()
    if req.account_ids:
        queue_ids = req.account_ids
    else:
        queue_ids = [a["id"] for a in cfg.get("accounts", []) if a.get("status") != "completed"]

    if not queue_ids:
        raise HTTPException(status_code=400, detail="No pending accounts to run (to rerun completed accounts, click Reset first)")

    async def _batch_runner():
        global BATCH_RUNNING, STOP_BATCH
        BATCH_RUNNING = True
        STOP_BATCH = False
        append_log(f"[{time.strftime('%H:%M:%S')}] [BATCH] 🚀 Starting automated batch pipeline: {queue_ids} (Starting Step: Step {req.start_step})")
        try:
            for idx, aid in enumerate(queue_ids):
                if STOP_BATCH:
                    append_log(f"[{time.strftime('%H:%M:%S')}] [BATCH] ⏹ Batch queue stopped by user request")
                    break
                await _run_account_worker(aid, start_step=req.start_step, port=9330 + (idx % 5))
                await asyncio.sleep(2)
        finally:
            BATCH_RUNNING = False
            append_log(f"[{time.strftime('%H:%M:%S')}] [BATCH] 🏁 Batch execution queue finished")

    asyncio.create_task(_batch_runner())
    return {"ok": True, "queued": queue_ids}


@app.post("/api/stop")
async def stop_all(req: Optional[AccountActionReq] = None):
    global STOP_BATCH
    STOP_BATCH = True
    if req and req.account_id and req.account_id in ACTIVE_TASKS:
        ACTIVE_TASKS[req.account_id].stop_requested = True
        append_log(f"[{time.strftime('%H:%M:%S')}] [STOP] ⏹ Stopping account {req.account_id}...")
    else:
        for aid, task in list(ACTIVE_TASKS.items()):
            task.stop_requested = True
        append_log(f"[{time.strftime('%H:%M:%S')}] [STOP] ⏹ Sent global stop command")
    return {"ok": True}


@app.post("/api/logs/clear")
async def clear_logs():
    LOGS.clear()
    return {"ok": True}


MANUAL_BRIDGES: Dict[str, LocalHttpProxyAuthBridge] = {}


def _launch_on_interactive_desktop(args: List[str]):
    """Launch a local browser process using standard subprocess.Popen."""
    subprocess.Popen(args)


async def _launch_manual_chrome(account_id: str, open_urls: List[str]) -> Dict[str, Any]:
    """Launch a dedicated (non-automated) Chrome window bound to the account's
    isolated profile and proxy, then open the given URLs. Shared by the manual
    Browser button and the manual Login button."""
    cfg = load_config()
    target_acc = next((a for a in cfg.get("accounts", []) if a["id"] == account_id), None)
    if not target_acc:
        raise HTTPException(status_code=404, detail="Account does not exist")

    user_data_dir = get_profile_dir(target_acc["id"], target_acc["email"])
    target_region = cfg.get("target_region", "US")
    proxy_template = cfg.get("proxy_template", "")
    resolved_proxy = resolve_account_proxy(target_acc, target_region, proxy_template)
    parsed_proxy = parse_proxy_string(resolved_proxy)

    import automation_engine
    chrome_exe = automation_engine._find_chrome_path()
    chrome_args = [
        chrome_exe,
        f"--user-data-dir={user_data_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--webrtc-ip-handling-policy=disable_non_proxied_udp",
        "--enforce-webrtc-ip-permission-check",
        "--start-maximized",
    ]

    if parsed_proxy:
        if parsed_proxy.get("username"):
            if account_id in MANUAL_BRIDGES:
                try:
                    await MANUAL_BRIDGES[account_id].stop()
                except Exception:
                    pass
            acc_idx = sum(ord(c) for c in account_id) % 50
            bridge_port = 9600 + acc_idx
            bridge = LocalHttpProxyAuthBridge(bridge_port, parsed_proxy)
            await bridge.start()
            MANUAL_BRIDGES[account_id] = bridge
            chrome_args.append(f"--proxy-server=http://127.0.0.1:{bridge_port}")
        else:
            p_url = f"{parsed_proxy['scheme']}://{parsed_proxy['host']}:{parsed_proxy['port']}"
            chrome_args.append(f"--proxy-server={p_url}")

    chrome_args.extend(open_urls)
    _launch_on_interactive_desktop(chrome_args)
    return {"account": target_acc}


@app.post("/api/open_browser")
async def open_browser_manual(req: AccountActionReq):
    res = await _launch_manual_chrome(
        req.account_id,
        [
            "https://one.google.com/plans?hl=en",
            "https://play.google.com/store/account/subscriptions?hl=en",
            "https://play.google.com/store/account/orderhistory?hl=en",
        ],
    )
    target_acc = res["account"]
    append_log(
        f"[{time.strftime('%H:%M:%S')}] [BROWSER] 🌐 Manually opened dedicated Chrome window for [{target_acc['id']}] ({target_acc['email']})"
    )
    return {"ok": True}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8765, log_level="warning")
