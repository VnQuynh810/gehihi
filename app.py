import asyncio
import json
import os
import re
import shutil
import subprocess
import time
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
    resolve_account_proxy,
    parse_proxy_string,
    CHROME_PATH,
    BASE_DIR,
    SCREENSHOTS_DIR,
)

CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")
os.makedirs(TEMPLATES_DIR, exist_ok=True)
os.makedirs(SCREENSHOTS_DIR, exist_ok=True)

app = FastAPI(title="Google AI Pro (5 TB) Miễn Phí 2 Năm - Điều Khiển Tự Động")
app.mount("/screenshots", StaticFiles(directory=SCREENSHOTS_DIR), name="screenshots")

# Global runtime state
LOGS: List[str] = [
    f"[{time.strftime('%H:%M:%S')}] [SYSTEM] 🚀 Google AI Pro (5 TB) Miễn Phí 2 Năm (US$0.00 -> 2028-10) Bảng điều khiển đã sẵn sàng!"
]
ACTIVE_TASKS: Dict[str, GoogleUltraAutomator] = {}
BATCH_RUNNING: bool = False
STOP_BATCH: bool = False


def load_config() -> Dict[str, Any]:
    if not os.path.exists(CONFIG_PATH):
        return {"target_region": "US", "target_plan": "pro_2year_zero", "auto_cancel": True, "card": {}, "accounts": []}
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_config(cfg: Dict[str, Any]):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def append_log(line: str):
    print(line, flush=True)
    LOGS.append(line)
    if len(LOGS) > 500:
        del LOGS[: len(LOGS) - 500]


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


class RunBatchReq(BaseModel):
    account_ids: Optional[List[str]] = None
    start_step: int = 1


class AccountActionReq(BaseModel):
    account_id: str


class AccountUpdateReq(BaseModel):
    account_id: str
    email: Optional[str] = None
    password: Optional[str] = None
    recovery_email: Optional[str] = None
    totp_secret: Optional[str] = None
    proxy: Optional[str] = None


class BatchAccountsActionReq(BaseModel):
    account_ids: List[str]


@app.get("/", response_class=HTMLResponse)
async def index():
    html_path = os.path.join(TEMPLATES_DIR, "index.html")
    with open(html_path, "r", encoding="utf-8") as f:
        content = f.read()
    return HTMLResponse(
        content=content,
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


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
    append_log(f"[{time.strftime('%H:%M:%S')}] [CONFIG] ✅ Đã lưu cấu hình (Khu vực: {req.target_region} | Proxy: {'Đã bật' if req.proxy_template.strip() else 'Mặc định hệ thống'})")
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
        return {"ok": False, "message": "Chưa điền mẫu proxy"}

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
        bal_str = f" | FloppyData lưu lượng còn lại: {bal_gb} GB" if bal_gb is not None else ""
        msg = f"{flag} Kết nối thành công! IP đầu ra: {ip} ({country} · {city} | {org}){bal_str}"
        append_log(f"[{time.strftime('%H:%M:%S')}] [PROXY] ✅ {msg}")
        return {"ok": True, "message": msg, "ip": ip, "country_code": cc, "balance_gb": bal_gb}
    except Exception as e:
        err = f"Kiểm tra proxy thất bại: {e}"
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
            acc_id = f"Account_{len(existing) + 1}"
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
                "step_message": "Tài khoản mới nhập (chờ thực thi)",
                "expiry_date": "-",
                "last_screenshot": "",
            }
        )
        existing_emails.add(norm_email)
        added += 1

    cfg["accounts"] = existing
    save_config(cfg)
    append_log(f"[{time.strftime('%H:%M:%S')}] [IMPORT] ✅ Đã nhập/cập nhật {added} tài khoản (tổng cộng {len(existing)} tài khoản)")
    return {"ok": True, "added": added, "total": len(existing)}


@app.post("/api/accounts/delete")
async def delete_account(req: AccountActionReq):
    if req.account_id in ACTIVE_TASKS:
        raise HTTPException(status_code=400, detail="Tài khoản đang chạy, vui lòng dừng tác vụ trước")
    cfg = load_config()
    cfg["accounts"] = [a for a in cfg.get("accounts", []) if a["id"] != req.account_id]
    save_config(cfg)
    append_log(f"[{time.strftime('%H:%M:%S')}] [ACCOUNT] 🗑️ Đã xóa tài khoản {req.account_id}")
    return {"ok": True}


@app.post("/api/accounts/reset")
async def reset_account(req: AccountActionReq):
    cfg = load_config()
    for a in cfg.get("accounts", []):
        if a["id"] == req.account_id:
            a["status"] = "pending"
            a["current_step"] = 0
            a["step_message"] = "Đã đặt lại trạng thái (chờ thực thi)"
            break
    save_config(cfg)
    append_log(f"[{time.strftime('%H:%M:%S')}] [ACCOUNT] 🔄 Đã đặt lại tài khoản {req.account_id} về trạng thái chờ thực thi")
    return {"ok": True}


@app.post("/api/accounts/edit")
async def edit_account(req: AccountUpdateReq):
    cfg = load_config()
    target = next((a for a in cfg.get("accounts", []) if a["id"] == req.account_id), None)
    if not target:
        raise HTTPException(status_code=404, detail="Không tìm thấy tài khoản")
    if req.email is not None:
        target["email"] = req.email.strip()
    if req.password is not None:
        target["password"] = req.password.strip()
    if req.recovery_email is not None:
        target["recovery_email"] = req.recovery_email.strip()
    if req.totp_secret is not None:
        target["totp_secret"] = req.totp_secret.strip()
    if req.proxy is not None:
        target["proxy"] = req.proxy.strip()
    save_config(cfg)
    append_log(f"[{time.strftime('%H:%M:%S')}] [ACCOUNT] ✏️ Đã cập nhật tài khoản {req.account_id} ({target['email']})")
    return {"ok": True}


@app.post("/api/accounts/batch_delete")
async def batch_delete_accounts(req: BatchAccountsActionReq):
    running = [aid for aid in req.account_ids if aid in ACTIVE_TASKS]
    if running:
        raise HTTPException(status_code=400, detail=f"Không thể xoá tài khoản đang chạy: {', '.join(running)}")
    cfg = load_config()
    target_set = set(req.account_ids)
    cfg["accounts"] = [a for a in cfg.get("accounts", []) if a["id"] not in target_set]
    save_config(cfg)
    append_log(f"[{time.strftime('%H:%M:%S')}] [ACCOUNT] 🗑️ Đã xoá {len(target_set)} tài khoản đã chọn")
    return {"ok": True, "count": len(target_set)}


@app.post("/api/accounts/batch_reset")
async def batch_reset_accounts(req: BatchAccountsActionReq):
    cfg = load_config()
    target_set = set(req.account_ids)
    count = 0
    for a in cfg.get("accounts", []):
        if a["id"] in target_set:
            a["status"] = "pending"
            a["current_step"] = 0
            a["step_message"] = "Đã đặt lại trạng thái (Chờ thực hiện)"
            count += 1
    save_config(cfg)
    append_log(f"[{time.strftime('%H:%M:%S')}] [ACCOUNT] 🔄 Đã đặt lại {count} tài khoản đã chọn")
    return {"ok": True, "count": count}


@app.post("/api/accounts/clear_all")
async def clear_all_accounts():
    if ACTIVE_TASKS:
        raise HTTPException(status_code=400, detail="Vui lòng dừng tất cả tác vụ đang chạy trước khi xoá toàn bộ danh sách")
    cfg = load_config()
    count = len(cfg.get("accounts", []))
    cfg["accounts"] = []
    save_config(cfg)
    append_log(f"[{time.strftime('%H:%M:%S')}] [ACCOUNT] 🧹 Đã xoá toàn bộ danh sách ({count} tài khoản)")
    return {"ok": True, "count": count}


@app.post("/api/logs/clear")
async def clear_logs():
    global LOGS
    LOGS.clear()
    LOGS.append(f"[{time.strftime('%H:%M:%S')}] [SYSTEM] 🚀 Nhật ký console đã được làm sạch")
    return {"ok": True}


async def _run_account_worker(account_id: str, start_step: int = 1, port: int = 9320):
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
    ACTIVE_TASKS[account_id] = automator
    try:
        await automator.run_pipeline(start_step=start_step)
    finally:
        ACTIVE_TASKS.pop(account_id, None)


@app.post("/api/run/single")
async def run_single(req: RunSingleReq):
    if req.account_id in ACTIVE_TASKS:
        raise HTTPException(status_code=400, detail="Tài khoản này đang được chạy")
    port = 9320 + (len(ACTIVE_TASKS) % 10)
    asyncio.create_task(_run_account_worker(req.account_id, start_step=req.start_step, port=port))
    return {"ok": True}


@app.post("/api/run/batch")
async def run_batch(req: RunBatchReq):
    global BATCH_RUNNING, STOP_BATCH
    if BATCH_RUNNING:
        raise HTTPException(status_code=400, detail="Hàng đợi chạy hàng loạt đang hoạt động")

    cfg = load_config()
    if req.account_ids:
        queue_ids = req.account_ids
    else:
        queue_ids = [a["id"] for a in cfg.get("accounts", []) if a.get("status") != "completed"]

    if not queue_ids:
        raise HTTPException(status_code=400, detail="Không có tài khoản nào chờ thực thi (hãy bấm Đặt lại nếu muốn chạy lại)")

    async def _batch_runner():
        global BATCH_RUNNING, STOP_BATCH
        BATCH_RUNNING = True
        STOP_BATCH = False
        append_log(f"[{time.strftime('%H:%M:%S')}] [BATCH] 🚀 Bắt đầu thực thi hàng loạt trọn gói: {queue_ids} (Bước bắt đầu: Step {req.start_step})")
        try:
            for idx, aid in enumerate(queue_ids):
                if STOP_BATCH:
                    append_log(f"[{time.strftime('%H:%M:%S')}] [BATCH] ⏹ Hàng đợi hàng loạt đã dừng theo yêu cầu người dùng")
                    break
                await _run_account_worker(aid, start_step=req.start_step, port=9330 + (idx % 5))
                await asyncio.sleep(2)
        finally:
            BATCH_RUNNING = False
            append_log(f"[{time.strftime('%H:%M:%S')}] [BATCH] 🏁 Hàng đợi thực thi hàng loạt đã hoàn tất")

    asyncio.create_task(_batch_runner())
    return {"ok": True, "queued": queue_ids}


@app.post("/api/stop")
async def stop_all(req: Optional[AccountActionReq] = None):
    global STOP_BATCH
    STOP_BATCH = True
    if req and req.account_id and req.account_id in ACTIVE_TASKS:
        ACTIVE_TASKS[req.account_id].stop_requested = True
        append_log(f"[{time.strftime('%H:%M:%S')}] [STOP] ⏹ Đang dừng tài khoản {req.account_id}...")
    else:
        for aid, task in list(ACTIVE_TASKS.items()):
            task.stop_requested = True
        append_log(f"[{time.strftime('%H:%M:%S')}] [STOP] ⏹ Đã gửi lệnh dừng toàn bộ")
    return {"ok": True}


MANUAL_BRIDGES: Dict[str, LocalHttpProxyAuthBridge] = {}


def _launch_on_interactive_desktop(args: List[str]):
    """
    Launch a GUI process (e.g. Chrome) explicitly onto the user's interactive desktop (winsta0\\default)
    even if the backend server was started inside an isolated/background desktop session (e.g. exebox).
    """
    if os.name == "nt":
        try:
            import ctypes
            import ctypes.wintypes as wt

            class STARTUPINFOW(ctypes.Structure):
                _fields_ = [
                    ("cb", wt.DWORD),
                    ("lpReserved", wt.LPWSTR),
                    ("lpDesktop", wt.LPWSTR),
                    ("lpTitle", wt.LPWSTR),
                    ("dwX", wt.DWORD),
                    ("dwY", wt.DWORD),
                    ("dwXSize", wt.DWORD),
                    ("dwYSize", wt.DWORD),
                    ("dwXCountChars", wt.DWORD),
                    ("dwYCountChars", wt.DWORD),
                    ("dwFillAttribute", wt.DWORD),
                    ("dwFlags", wt.DWORD),
                    ("wShowWindow", wt.WORD),
                    ("cbReserved2", wt.WORD),
                    ("lpReserved2", ctypes.POINTER(wt.BYTE)),
                    ("hStdInput", wt.HANDLE),
                    ("hStdOutput", wt.HANDLE),
                    ("hStdError", wt.HANDLE),
                ]

            class PROCESS_INFORMATION(ctypes.Structure):
                _fields_ = [
                    ("hProcess", wt.HANDLE),
                    ("hThread", wt.HANDLE),
                    ("dwProcessId", wt.DWORD),
                    ("dwThreadId", wt.DWORD),
                ]

            si = STARTUPINFOW()
            si.cb = ctypes.sizeof(si)
            si.lpDesktop = "winsta0\\default"
            si.dwFlags = 1  # STARTF_USESHOWWINDOW
            si.wShowWindow = 3  # SW_MAXIMIZE
            pi = PROCESS_INFORMATION()
            cmd_str = subprocess.list2cmdline(args)
            cmd_buf = ctypes.create_unicode_buffer(cmd_str)
            ok = ctypes.windll.kernel32.CreateProcessW(
                None,
                cmd_buf,
                None,
                None,
                False,
                0x00000010,  # CREATE_NEW_CONSOLE
                None,
                None,
                ctypes.byref(si),
                ctypes.byref(pi),
            )
            if ok:
                ctypes.windll.kernel32.CloseHandle(pi.hThread)
                ctypes.windll.kernel32.CloseHandle(pi.hProcess)
                return
        except Exception:
            pass
    subprocess.Popen(args)


@app.post("/api/open_browser")
async def open_browser_manual(req: AccountActionReq):
    cfg = load_config()
    target_acc = next((a for a in cfg.get("accounts", []) if a["id"] == req.account_id), None)
    if not target_acc:
        raise HTTPException(status_code=404, detail="Tài khoản không tồn tại")

    user_data_dir = get_profile_dir(target_acc["id"], target_acc["email"])
    target_region = cfg.get("target_region", "US")
    proxy_template = cfg.get("proxy_template", "")
    resolved_proxy = resolve_account_proxy(target_acc, target_region, proxy_template)
    parsed_proxy = parse_proxy_string(resolved_proxy)

    chrome_args = [
        CHROME_PATH,
        f"--user-data-dir={user_data_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--webrtc-ip-handling-policy=disable_non_proxied_udp",
        "--enforce-webrtc-ip-permission-check",
        "--start-maximized",
    ]

    if parsed_proxy:
        if parsed_proxy.get("username"):
            if req.account_id in MANUAL_BRIDGES:
                try:
                    await MANUAL_BRIDGES[req.account_id].stop()
                except Exception:
                    pass
            acc_idx = sum(ord(c) for c in req.account_id) % 50
            bridge_port = 9600 + acc_idx
            bridge = LocalHttpProxyAuthBridge(bridge_port, parsed_proxy)
            await bridge.start()
            MANUAL_BRIDGES[req.account_id] = bridge
            chrome_args.append(f"--proxy-server=http://127.0.0.1:{bridge_port}")
        else:
            p_url = f"{parsed_proxy['scheme']}://{parsed_proxy['host']}:{parsed_proxy['port']}"
            chrome_args.append(f"--proxy-server={p_url}")

    chrome_args.extend(
        [
            "https://one.google.com/plans?hl=vi",
            "https://play.google.com/store/account/subscriptions?hl=vi",
            "https://play.google.com/store/account/orderhistory?hl=vi",
        ]
    )
    _launch_on_interactive_desktop(chrome_args)
    append_log(
        f"[{time.strftime('%H:%M:%S')}] [BROWSER] 🌐 Đã mở cửa sổ Chrome riêng cho [{target_acc['id']}] ({target_acc['email']})"
    )
    return {"ok": True}


if __name__ == "__main__":
    import uvicorn
    print("=" * 50)
    print("  Server dang khoi dong...")
    print("  Truy cap: http://127.0.0.1:8765")
    print("  Nhan Ctrl+C de dung server")
    print("=" * 50)
    uvicorn.run(app, host="127.0.0.1", port=8765, log_level="info")
