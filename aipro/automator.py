import asyncio
import json
import os
import re
import subprocess
import time
from typing import Any, Callable, Dict, Optional
from playwright.async_api import async_playwright

from .hooks import HOOK_JS
from .tokens import (
    _ZERO_PRICE_RE,
    PREFIX,
    TOK_PRO5TB_12M,
    TOK_PRO5TB_6M,
    TOK_PRO5TB_6M_2,
    TOK_PRO5TB_6M_SS,
    YT_FREE_TOK,
)
from .settings import (
    CHROME_PATH,
    SCREENSHOTS_DIR,
    get_profile_dir,
    is_port_open,
)
from .proxy import (
    LocalHttpProxyAuthBridge,
    parse_proxy_string,
    resolve_account_proxy,
)
from .totp import get_totp


class GoogleUltraAutomator:
    def __init__(
        self,
        account: Dict[str, Any],
        card: Dict[str, str],
        target_region: str = "TR",
        target_plan: str = "ultra_20tb_5x",
        auto_cancel: bool = True,
        proxy_template: str = "",
        port: int = 9320,
        on_log: Optional[Callable[[str], None]] = None,
        on_state: Optional[Callable[[Dict[str, Any]], None]] = None,
    ):
        self.account = account
        self.card = card
        self.target_region = (target_region or "TR").upper()
        self.target_plan = target_plan
        self.auto_cancel = auto_cancel
        self.proxy_template = proxy_template
        self.port = port
        self.on_log = on_log or (lambda msg: print(msg))
        self.on_state = on_state or (lambda st: None)
        self.stop_requested = False
        # Manual-login pause support: when manual_login is True, Step 1 does NOT
        # auto-fill credentials. Instead it opens the Google sign-in page in this
        # same automation browser and waits until the user signs in by hand and
        # clicks "Resume" (which sets resume_requested=True).
        self.manual_login = False
        self.resume_requested = False
        self.proc: Optional[subprocess.Popen] = None
        self.proxy_bridge: Optional[LocalHttpProxyAuthBridge] = None

    def log(self, msg: str):
        ts = time.strftime("%H:%M:%S")
        line = f"[{ts}] [{self.account['id']}] {msg}"
        self.on_log(line)

    def update_state(
        self,
        status: Optional[str] = None,
        current_step: Optional[int] = None,
        step_message: Optional[str] = None,
        expiry_date: Optional[str] = None,
        last_screenshot: Optional[str] = None,
        ip_info: Optional[str] = None,
    ):
        patch: Dict[str, Any] = {}
        if status is not None:
            self.account["status"] = status
            patch["status"] = status
        if current_step is not None:
            self.account["current_step"] = current_step
            patch["current_step"] = current_step
        if step_message is not None:
            self.account["step_message"] = step_message
            patch["step_message"] = step_message
        if expiry_date is not None:
            self.account["expiry_date"] = expiry_date
            patch["expiry_date"] = expiry_date
        if last_screenshot is not None:
            self.account["last_screenshot"] = last_screenshot
            patch["last_screenshot"] = last_screenshot
        if ip_info is not None:
            self.account["ip_info"] = ip_info
            patch["ip_info"] = ip_info
        self.on_state(patch)

    async def save_screenshot(self, page, step_tag: str) -> str:
        safe_email = re.sub(r"[^a-zA-Z0-9_.-]", "_", self.account.get("email") or self.account["id"])
        fname = f"{self.account['id']}_{safe_email}_{step_tag}_{int(time.time())}.png"
        fpath = os.path.join(SCREENSHOTS_DIR, fname)
        try:
            await page.screenshot(path=fpath)
            self.update_state(last_screenshot=fname)
        except Exception as e:
            self.log(f"Screenshot save note: {e}")
        return fname

    def check_stop(self):
        if self.stop_requested:
            raise RuntimeError("Task stopped manually by user")

    async def safe_goto(self, page, url: str, timeout: int = 40000):
        """Navigate with automatic retry and residential proxy session rotation on transient network errors."""
        last_err = None
        for attempt in range(4):
            self.check_stop()
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=timeout)
                return
            except Exception as e:
                last_err = e
                err_short = str(e).splitlines()[0][:80]
                if attempt < 3:
                    if self.proxy_bridge:
                        self.proxy_bridge.rotate_session()
                    self.log(f"⚠️ Residential proxy fluctuation ({err_short}), rotating proxy session and retrying ({attempt + 1}/3)...")
                    await asyncio.sleep(2.5)
        raise last_err

    async def ensure_totp_fresh(self) -> str:
        secret = self.account.get("totp_secret", "")
        if not secret:
            return ""
        rem = 30 - (int(time.time()) % 30)
        if rem <= 3:
            self.log(f"2FA TOTP code refreshing soon, waiting {rem + 1}s...")
            await asyncio.sleep(rem + 1)
        return get_totp(secret)

    async def pass_reauth_on_page(self, page_obj):
        """Automatically handle Google email + password + 2FA TOTP re-authentication prompts."""
        email = self.account.get("email", "")
        password = self.account.get("password", "")
        for _ in range(16):
            self.check_stop()
            if page_obj.is_closed():
                return
            try:
                body = await page_obj.inner_text("body", timeout=3000)
            except Exception:
                await asyncio.sleep(1.5)
                continue

            if "gds.google.com" in page_obj.url or "recoveryoptions" in page_obj.url or "确保您始终都能登录" in body or "Make sure you can always" in body:
                self.log("Detected gds.google.com prompt page, clicking [Cancel / Skip]...")
                cancel_btn = page_obj.locator('button:has-text("取消"), button:has-text("Cancel"), button:has-text("跳过"), button:has-text("Skip"), [role="button"]:has-text("取消"), [role="button"]:has-text("Cancel"), a:has-text("取消"), a:has-text("Cancel"), span:has-text("取消"), span:has-text("Cancel")').first
                if await cancel_btn.count() > 0:
                    try:
                        await cancel_btn.click(timeout=3000)
                    except Exception:
                        pass
                await page_obj.evaluate(
                    """() => {
                    const els = Array.from(document.querySelectorAll('button, a, [role="button"], span, div'));
                    for (const el of els) {
                        const t = (el.innerText || '').trim();
                        if (t === '取消' || t === 'Cancel' || t === '跳过' || t === 'Skip' || t === '以后再说' || t === 'Not now') {
                            el.click();
                        }
                    }
                }"""
                )
                await asyncio.sleep(4.5)
                continue

            if "confirmidentifier" in page_obj.url:
                self.log("Detected confirmidentifier page, clicking [Next]...")
                nxt_btn = page_obj.locator('#identifierNext button, button:has-text("下一步"), button:has-text("Next"), #identifierNext').first
                if await nxt_btn.count() > 0:
                    try:
                        await nxt_btn.click(timeout=3000)
                    except Exception:
                        try:
                            await nxt_btn.click(force=True, timeout=3000)
                        except Exception:
                            pass
                await page_obj.evaluate(
                    """() => {
                    const targets = Array.from(document.querySelectorAll('button, #identifierNext, [role="button"]'));
                    for (const el of targets) {
                        const t = (el.innerText || '').trim();
                        if (t === '下一步' || t === 'Next' || t === 'Continue' || el.id === 'identifierNext') {
                            el.click();
                        }
                    }
                }"""
                )
                await asyncio.sleep(4)
                continue

            email_inp = page_obj.locator('input#identifierId, input[name="identifier"]')
            if email and await email_inp.count() > 0 and await email_inp.first.is_visible():
                pwd_vis = page_obj.locator('input[type="password"], input[name="Passwd"]')
                if await pwd_vis.count() == 0 or not await pwd_vis.first.is_visible():
                    self.log(f"Detected login email field, filling email and clicking Next: {email}")
                    await email_inp.first.fill(email)
                    await email_inp.first.press("Enter")
                    await asyncio.sleep(4.5)
                    continue

            if "Verify it's you" in body or "验证您的身份" in body:
                btn = page_obj.locator('button:has-text("Verify it\'s you"), button:has-text("验证您的身份"), div[role="button"]:has-text("验证您的身份"), div[role="button"]:has-text("Verify it\'s you")').first
                if await btn.count() > 0 and await btn.is_visible():
                    self.log("Detected security challenge, clicking [Verify it's you]...")
                    await btn.click()
                    await asyncio.sleep(4)
                    continue

            pwd = page_obj.locator('input[type="password"], input[name="Passwd"]')
            if await pwd.count() > 0 and await pwd.first.is_visible():
                self.log("Auto-entering password for authentication...")
                try:
                    await pwd.first.fill(password)
                    next_btn = page_obj.locator('#passwordNext, button:has-text("下一步"), button:has-text("Next")').first
                    if await next_btn.count() > 0 and await next_btn.is_visible():
                        await next_btn.click()
                    else:
                        await pwd.first.press("Enter")
                except Exception:
                    return
                await asyncio.sleep(4.5)
                continue

            if "challenge/recaptcha" in page_obj.url or "证明您不是自动程序" in body or "not a robot" in body:
                for fr in page_obj.frames:
                    if "recaptcha" in (fr.url or "") and "anchor" in (fr.url or ""):
                        try:
                            cb = fr.locator("#recaptcha-anchor")
                            if await cb.count() > 0:
                                self.log("Detected login challenge (challenge/recaptcha), auto-checking...")
                                await cb.click()
                                await asyncio.sleep(4.5)
                                break
                        except Exception:
                            pass
                continue

            # Check if an open dialog modal (VfPpkd-Sx9Kwc-OWXEXe-FNFY6c) is shown on top of the page
            open_dialog_btn = page_obj.locator('div.VfPpkd-Sx9Kwc-OWXEXe-FNFY6c button, div.VfPpkd-Sx9Kwc-OWXEXe-FNFY6c [role="button"], div[role="dialog"] button, div[role="dialog"] [role="button"]')
            if await open_dialog_btn.count() > 0:
                for idx in range(await open_dialog_btn.count()):
                    b = open_dialog_btn.nth(idx)
                    if await b.is_visible():
                        btxt = (await b.inner_text()).strip()
                        if any(k in btxt for k in ("以后再说", "跳过", "取消", "不用了", "Not now", "Skip", "Cancel", "Google Authenticator", "身份验证器")):
                            self.log(f"Detected modal option [{btxt}], clicking...")
                            await b.click(force=True)
                            await asyncio.sleep(3.5)
                            break

            if "Google Authenticator" in body or "身份验证器" in body:
                totp_vis = page_obj.locator('input[type="tel"], input#totpPin, input[name="totpPin"]')
                if await totp_vis.count() == 0 or not await totp_vis.first.is_visible():
                    opt = page_obj.locator("text=/Google Authenticator|身份验证器/i").first
                    if await opt.count() > 0 and await opt.is_visible():
                        self.log("Selecting Google Authenticator verification...")
                        await opt.click(force=True)
                        await asyncio.sleep(3.5)
                        continue
            elif ("challenge/dp" in page_obj.url or "challenge/az" in page_obj.url or "challenge/pk" in page_obj.url or "扫描二维码" in body) and "challenge/totp" not in page_obj.url:
                totp_vis = page_obj.locator('input[type="tel"], input#totpPin, input[name="totpPin"]')
                if await totp_vis.count() == 0 or not await totp_vis.first.is_visible():
                    try_other = page_obj.locator("text=/试试其他方式|尝试其他方式|Try another way/i").first
                    if await try_other.count() > 0 and await try_other.is_visible():
                        self.log("Detected alternative challenge page (QR/Prompt/Key), clicking [Try another way] for 2FA...")
                        try:
                            await try_other.click(timeout=4000)
                        except Exception:
                            pass
                        await asyncio.sleep(3.5)
                        continue

            if "确认您的辅助邮箱" in body or "Confirm your recovery email" in body:
                rec_email = (self.account.get("recovery_email") or "").strip()
                kpe_inp = page_obj.locator('input#knowledge-preregistered-email-response:visible, input[name="knowledgePreregisteredEmailResponse"]:visible, input[type="email"]:visible')
                if rec_email and await kpe_inp.count() > 0:
                    self.log(f"Auto-entering recovery email for verification: {rec_email}")
                    await kpe_inp.first.fill(rec_email)
                    await kpe_inp.first.press("Enter")
                    await asyncio.sleep(4.5)
                    continue
                opt = page_obj.locator("text=/确认您的辅助邮箱|Confirm your recovery email/i").first
                if await opt.count() > 0 and await opt.is_visible():
                    self.log("Selecting [Confirm your recovery email] option...")
                    await opt.click(force=True)
                    await asyncio.sleep(3.5)
                    continue

            if "Add 2-Step Verification" in body or "We couldn’t verify it’s you" in body or "We couldn't verify it's you" in body or "signin/rejected" in page_obj.url:
                self.log("ℹ️ Detected new device security interception (Add 2-Step Verification / signin/rejected), skipping...")
                return

            totp = page_obj.locator('input[type="tel"], input#totpPin, input[name="totpPin"]')
            if await totp.count() > 0 and await totp.first.is_visible():
                code = await self.ensure_totp_fresh()
                if code:
                    self.log("Auto-entering 2FA TOTP code: [******]")
                    try:
                        await totp.first.fill(code)
                        next_btn = page_obj.locator('#totpNext, button:has-text("下一步"), button:has-text("Next")').first
                        if await next_btn.count() > 0 and await next_btn.is_visible():
                            await next_btn.click()
                        else:
                            await totp.first.press("Enter")
                    except Exception:
                        return
                    await asyncio.sleep(5)
                    continue

            if "accounts.google.com" in page_obj.url or "gds.google.com" in page_obj.url or page_obj.url == "about:blank":
                await asyncio.sleep(2)
                continue

            break

    async def step1_manual_pause(self, page):
        """Manual-login mode: open the Google sign-in page in this same automation
        browser window and pause. The user signs in by hand (handling 2FA / CAPTCHA
        themselves), then clicks the 'Resume' button, which sets resume_requested."""
        self.update_state(
            status="waiting_human",
            current_step=1,
            step_message="⏸ Paused: sign in to Google in the opened browser window, then click 'Resume'",
        )
        self.log("⏸ Manual login mode: opening the Google sign-in page in this browser.")
        self.log("👉 Please sign in by hand in the opened Chrome window, then click the [Resume] button to continue.")
        try:
            await self.safe_goto(
                page,
                "https://accounts.google.com/ServiceLogin?hl=en&continue=https%3A%2F%2Fone.google.com%2Fplans%3Fhl%3Den",
                timeout=40000,
            )
        except Exception as e:
            self.log(f"Note: could not preload the sign-in page ({str(e).splitlines()[0][:80]}); you can navigate manually.")

        self.resume_requested = False
        waited = 0.0
        max_wait = 1800.0  # 30 minutes safety cap
        while not self.resume_requested:
            self.check_stop()
            await asyncio.sleep(1.0)
            waited += 1.0
            if waited >= max_wait:
                self.log("⚠️ Manual login wait timed out after 30 minutes; continuing anyway.")
                break

        self.resume_requested = False
        self.log("▶ Resume received — continuing the pipeline with your signed-in session.")
        try:
            await self.save_screenshot(page, "step1_manual_login")
        except Exception:
            pass
        self.update_state(status="running", current_step=1, step_message="✅ Manual login confirmed, continuing...")
        self.log(f"✅ Step 1 (manual login) completed. Current page: {page.url[:80]}")

    async def step1_login(self, page):
        self.update_state(status="running", current_step=1, step_message="Step 1/5: Logging in and handling verification (Recovery / 2FA)...")
        self.log("=== Step 1/5: Checking Google login & passing Recovery / 2FA ===")
        email = self.account["email"]
        password = self.account["password"]

        await self.safe_goto(
            page,
            "https://accounts.google.com/ServiceLogin?hl=zh-CN&continue=https%3A%2F%2Fone.google.com%2Fplans%3Fhl%3Dzh-CN",
            timeout=40000,
        )
        await asyncio.sleep(3.5)
        await self.save_screenshot(page, "step1_init")

        for login_round in range(3):
            if "one.google.com" in page.url and "accounts.google.com" not in page.url and "gds.google.com" not in page.url:
                break
            try:
                body_txt = await page.inner_text("body", timeout=3000)
            except Exception:
                body_txt = ""

            if "gds.google.com" in page.url or "recoveryoptions" in page.url or "homeaddress" in page.url or "确保您始终都能登录" in body_txt or "Make sure you can always" in body_txt or "设置住址" in body_txt:
                await page.evaluate(
                    """() => {
                    const els = Array.from(document.querySelectorAll('button, a, [role="button"], span'));
                    for (const el of els) {
                        const t = (el.innerText || '').trim();
                        if (['取消', 'Cancel', '跳过', 'Skip', '以后再说', 'Not now'].includes(t)) el.click();
                    }
                }"""
                )
                await asyncio.sleep(4.0)
                if "one.google.com" in page.url and "accounts.google.com" not in page.url:
                    break

            # Account chooser check
            email_lower = email.lower()
            email_clean = email_lower.replace(".", "")
            chooser_items = page.locator("[data-identifier]")
            clicked_chooser = False
            for i in range(await chooser_items.count()):
                ident = (await chooser_items.nth(i).get_attribute("data-identifier") or "").lower()
                if ident == email_lower or ident.replace(".", "") == email_clean:
                    self.log(f"Selected account in chooser: {ident}")
                    await chooser_items.nth(i).click()
                    clicked_chooser = True
                    await asyncio.sleep(3.5)
                    break

            if not clicked_chooser:
                use_other = page.locator("text=/使用其他账号|Use another account/i")
                email_loc = page.locator('input[type="email"], input#identifierId')
                if (await email_loc.count() == 0 or not await email_loc.first.is_visible()) and await use_other.count() > 0:
                    await use_other.first.click()
                    await asyncio.sleep(2.5)

                email_loc = page.locator('input[type="email"], input#identifierId')
                if await email_loc.count() > 0 and await email_loc.first.is_visible() and "challenge/kpe" not in page.url:
                    self.log(f"Entering email: {email}")
                    await email_loc.first.fill(email)
                    next_btn = page.locator('#identifierNext, button:has-text("下一步"), button:has-text("Next")').first
                    if await next_btn.count() > 0 and await next_btn.is_visible():
                        await next_btn.click()
                    else:
                        await email_loc.first.press("Enter")
                    await asyncio.sleep(4.5)

            # Password + Recovery Email + 2FA
            await self.pass_reauth_on_page(page)

            # Dismiss post-login prompts
            for _ in range(4):
                if "one.google.com" in page.url and "gds.google.com" not in page.url:
                    break
                btn = page.locator(
                    'button:has-text("Not now"), button:has-text("Skip"), button:has-text("以后再说"), button:has-text("跳过"), button:has-text("取消"), button:has-text("Cancel"), button:has-text("我同意"), button:has-text("继续")'
                )
                if await btn.count() > 0 and await btn.first.is_visible():
                    await btn.first.click(force=True)
                    await asyncio.sleep(3.5)
                else:
                    break

            if "signin/identifier" in page.url or "ServiceLogin" in page.url:
                if self.proxy_bridge:
                    self.proxy_bridge.rotate_session()
                self.log("⚠️ Login page slow to respond, retrying login submission...")
                await self.safe_goto(
                    page,
                    "https://accounts.google.com/ServiceLogin?hl=zh-CN&continue=https%3A%2F%2Fone.google.com%2Fplans%3Fhl%3Dzh-CN",
                    timeout=40000,
                )
                await asyncio.sleep(3.5)

        await self.save_screenshot(page, "step1_done")
        self.log(f"✅ Step 1 Completed. Current page: {page.url[:80]}")

    async def get_current_sub_id(self, page) -> Optional[str]:
        for url in (
            "https://one.google.com/settings?expand=upgrade&hl=zh-CN",
            "https://one.google.com/plans?hl=zh-CN",
            "https://one.google.com/ai-nye?g1_landing_page=0&hl=zh-CN",
        ):
            await self.safe_goto(page, url, timeout=40000)
            for _ in range(6):
                await asyncio.sleep(1.8)
                html = await page.content()
                ids = list(set(re.findall(r'\"([a-z]{24})\.AO-', html)))
                if not ids:
                    ids = list(set(re.findall(r'([a-z]{24})\.AO-J1O', html)))
                if not ids:
                    # Match 24-char lowercase subscription token inside AF_initDataCallback arrays
                    for blk in re.findall(r'AF_initDataCallback\(\{.*?\}\);', html, flags=re.S):
                        if "subscriptions.red" in blk or "g1." in blk:
                            cand = re.findall(r'\"([a-z]{24})\"', blk)
                            if cand:
                                ids = cand
                                break
                if ids:
                    sub_id = ids[0]
                    self.log(f"Current detected 24-character subscription ID: {sub_id}")
                    return sub_id
        self.log("⚠️ Unable to extract 24-character subscription ID from HTML; Ngbm interceptor will preserve original ID dynamically")
        return None

    async def inspect_play_subscriptions(self, page) -> str:
        await self.safe_goto(page, "https://play.google.com/store/account/subscriptions?hl=zh-CN", timeout=40000)
        await asyncio.sleep(3)
        if "accounts.google.com" in page.url or "gds.google.com" in page.url:
            await self.pass_reauth_on_page(page)
            await asyncio.sleep(3)
        txt = await page.inner_text("body")
        clean = " | ".join([l.strip() for l in txt.splitlines() if l.strip()])
        return clean

    async def ensure_subscription_active(self, page):
        """If the account has a cancelled subscription ('Resubscribe'), click Resubscribe so Mode 5 stacking works."""
        await self.safe_goto(page, "https://play.google.com/store/account/subscriptions?hl=zh-CN", timeout=40000)
        await asyncio.sleep(3.5)
        if "accounts.google.com" in page.url or "gds.google.com" in page.url:
            await self.pass_reauth_on_page(page)
            await asyncio.sleep(3.0)
        try:
            body = await page.inner_text("body", timeout=3000)
        except Exception:
            body = ""
        if "重新订阅" in body or "Resubscribe" in body:
            self.log("Detected subscription is cancelled, clicking [Resubscribe] to reactivate for stacking...")
            rbtn = page.locator("text=/^重新订阅$|^Resubscribe$/i").first
            if await rbtn.count() > 0:
                await rbtn.click(force=True)
                await asyncio.sleep(3.5)
                cbtn = page.locator("text=/^重新订阅$|^Resubscribe$/i").last
                if await cbtn.count() > 0 and await cbtn.is_visible():
                    await cbtn.click(force=True)
                    await asyncio.sleep(5.0)
                ok_btn = page.locator("text=/^确定$|^OK$|^Confirm$/i").first
                if await ok_btn.count() > 0 and await ok_btn.is_visible():
                    await ok_btn.click(force=True)
                    await asyncio.sleep(2.0)

    async def recheck_coverage_complete(self, page, milestones: list, step_label: str) -> bool:
        """
        After repeated checkout errors, Google sometimes HAS already applied the
        subscription on the backend even though the UI showed "Something went wrong".
        Re-read the Play subscriptions page and, if any target milestone date is
        present, treat this step as already satisfied (avoids a false failure and a
        duplicate purchase).
        """
        self.log(f"[{step_label}] Too many checkout errors — re-checking Play subscriptions to see if coverage is already applied...")
        for attempt in range(2):
            try:
                play_txt = await self.inspect_play_subscriptions(page)
            except Exception as e:
                self.log(f"[{step_label}] Could not read subscriptions on recheck ({str(e)[:80]}), retrying...")
                await asyncio.sleep(5.0)
                continue
            hit = next((m for m in milestones if m in play_txt), None)
            if hit:
                self.log(f"✅ [{step_label}] Coverage already present (matched '{hit}') despite the checkout error — treating this step as complete.")
                if "重新订阅" in play_txt or "Resubscribe" in play_txt:
                    try:
                        await self.ensure_subscription_active(page)
                    except Exception:
                        pass
                return True
            if attempt == 0:
                self.log(f"[{step_label}] No target milestone yet, waiting 20s for backend settlement before final recheck...")
                await asyncio.sleep(20.0)
        self.log(f"⚠️ [{step_label}] Recheck found no target coverage — the stack is NOT complete for this step.")
        return False

    async def _poll_eacquire_modal(self, page, max_loops: int = 55, step_tag: str = ""):
        last_confirm_click_ts = 0.0
        loops_left = max_loops
        inline_card_saved = False
        error_retries = 3  # transient "Something went wrong, please try again" retries inside the modal
        while loops_left > 0:
            loops_left -= 1
            self.check_stop()
            if "accounts.google.com" in page.url or "gds.google.com" in page.url:
                self.log(f"[{step_tag or 'modal'}] Detected redirect to authentication ({page.url[:60]}), auto-completing...")
                await self.pass_reauth_on_page(page)
                return None, ""
            frames_to_check = []
            try:
                for p in page.context.pages:
                    frames_to_check.extend(p.frames)
            except Exception:
                frames_to_check = page.frames

            for fr in frames_to_check:
                fr_url = fr.url or ""
                if "eacquire" in fr_url:
                    try:
                        state_info = await fr.evaluate(
                            """() => {
                            if (!document.body) return {ready: false, text: ''};
                            const clone = document.body.cloneNode(true);
                            clone.querySelectorAll('script, style, noscript').forEach(el => el.remove());
                            const text = (clone.innerText || '').trim();
                            const needsCard = text.includes('添加付款方式') || text.includes('首先添加付款方式') || text.includes('添加信用卡或借记卡') || text.includes('添加卡') || text.includes('Add a payment method') || text.includes('Add credit or debit card') || text.includes('Add card');
                            const btns = Array.from(document.querySelectorAll('button, [role="button"]'));
                            const hasSubBtn = btns.some(b => {
                                const bt = (b.innerText || '').trim().toLowerCase();
                                const r = b.getBoundingClientRect();
                                return (bt === '订阅' || bt === 'subscribe') && r.width > 10 && r.height > 10;
                            });
                            const hardCode = /OR-[A-Z0-9]|BX-[A-Z0-9]|无法更改|unable to change/i.test(text);
                            const transientErr = text.includes('Something went wrong') || text.includes('Please try again') || text.includes('出了点问题') || text.includes('请重试') || text.includes('请再试一次');
                            const hasErr = hardCode || transientErr;
                            const retryBtn = btns.find(b => {
                                const bt = (b.innerText || '').trim().toLowerCase();
                                const r = b.getBoundingClientRect();
                                return r.width > 10 && r.height > 10 && (bt === 'try again' || bt === 'retry' || bt === '重试' || bt === '再试一次' || bt === '再试');
                            });
                            return {ready: hasSubBtn || hasErr, text: text, hasSub: hasSubBtn, hasErr: hasErr, hardCode: hardCode, transientErr: transientErr && !hardCode, hasRetryBtn: !!retryBtn};
                        }"""
                        )
                        if state_info and state_info.get("hasSub"):
                            raw_t = state_info.get("text", "")
                            clean_txt = " | ".join([l.strip() for l in raw_t.splitlines() if l.strip()])
                            return fr, clean_txt
                        if state_info and state_info.get("hasErr"):
                            raw_t = state_info.get("text", "")
                            clean_txt = " | ".join([l.strip() for l in raw_t.splitlines() if l.strip()])
                            # Hard decline (OR-/BX- codes) -> let caller surface the error immediately
                            if state_info.get("hardCode"):
                                return fr, clean_txt
                            # Transient "Something went wrong, please try again":
                            # if there is an in-modal [Try again] button, click it in place
                            # (bounded by error_retries); otherwise hand control back to the
                            # caller to reopen the plans page (the caller caps total reopens).
                            clicked_retry = False
                            if state_info.get("hasRetryBtn") and error_retries > 0:
                                error_retries -= 1
                                self.log(f"⚠️ [{step_tag or 'modal'}] Transient checkout error, clicking [Try again] in place (remaining {error_retries})...")
                                clicked_retry = await fr.evaluate(
                                    """() => {
                                    const btns = Array.from(document.querySelectorAll('button, [role="button"]'));
                                    const b = btns.find(x => {
                                        const t = (x.innerText || '').trim().toLowerCase();
                                        return t === 'try again' || t === 'retry' || t === '重试' || t === '再试一次' || t === '再试';
                                    });
                                    if (b) { b.click(); return true; }
                                    return false;
                                }"""
                                )
                            if clicked_retry:
                                self.log(f"[{step_tag or 'modal'}] Clicked in-modal [Try again], waiting for checkout to reload...")
                                await asyncio.sleep(4.0)
                                loops_left = max(loops_left, 30)
                                break  # restart the frame scan
                            else:
                                # No in-modal retry button (only 'Got it') -> dismiss and reopen plans page
                                await fr.evaluate(
                                    """() => {
                                    const btns = Array.from(document.querySelectorAll('button, [role="button"]'));
                                    const b = btns.find(x => {
                                        const t = (x.innerText || '').trim().toLowerCase();
                                        return t === 'got it' || t === 'ok' || t === '知道了' || t === '好的';
                                    });
                                    if (b) b.click();
                                }"""
                                )
                                self.log(f"[{step_tag or 'modal'}] Dismissed transient error, will reopen plans page for a fresh checkout...")
                                await asyncio.sleep(1.5)
                                return None, "TRANSIENT_REOPEN"
                        elif state_info and not inline_card_saved:
                            raw_t = state_info.get("text", "")
                            if any(k in raw_t for k in ("添加付款方式", "首先添加付款方式", "添加信用卡或借记卡", "添加卡", "Add a payment method", "Add credit or debit card", "Add card")):
                                add_btn = fr.locator("text=/添加信用卡或借记卡|添加卡|Add credit or debit card|Add card/i").last
                                if await add_btn.count() > 0 and await add_btn.is_visible():
                                    self.log(f"[{step_tag or 'modal'}] Modal requires payment method, binding tax-free card inline (ZIP 97201)...")
                                    await self.save_screenshot(page, f"{step_tag or 'modal'}_needs_card")
                                    try:
                                        await add_btn.click(force=True)
                                    except Exception:
                                        pass
                                    await fr.evaluate(
                                        """() => {
                                        const els = Array.from(document.querySelectorAll('button, [role="button"], div, span'));
                                        for (let i = els.length - 1; i >= 0; i--) {
                                            const t = (els[i].innerText || '').trim();
                                            if (t === '添加卡' || t === '添加信用卡或借记卡' || t === 'Add card' || t === 'Add credit or debit card') {
                                                els[i].click();
                                                break;
                                            }
                                        }
                                    }"""
                                    )
                                    for _ in range(20):
                                        await asyncio.sleep(1.0)
                                        all_frames = list(page.frames)
                                        for tf in all_frames:
                                            try:
                                                vis = tf.locator("input:visible")
                                                cnt = await vis.count()
                                                if cnt >= 4:
                                                    cnum = (self.card.get("number") or "").replace(" ", "")
                                                    expm = (self.card.get("exp_month") or "").zfill(2)
                                                    expy = (self.card.get("exp_year") or "")[-2:]
                                                    cvc_v = (self.card.get("cvc") or "").strip()
                                                    holder_v = (self.card.get("holder") or "CARD HOLDER").strip()
                                                    zip_v = (self.card.get("postal_code") or "97201").strip()
                                                    num_field = vis.nth(0)
                                                    for _nt in range(3):
                                                        await num_field.click(force=True)
                                                        await asyncio.sleep(0.2)
                                                        try:
                                                            await num_field.fill("")
                                                        except Exception:
                                                            pass
                                                        await asyncio.sleep(0.15)
                                                        await num_field.press_sequentially(cnum, delay=60)
                                                        await asyncio.sleep(0.3)
                                                        try:
                                                            cur_num = re.sub(r"\s+", "", await num_field.input_value(timeout=1000))
                                                        except Exception:
                                                            cur_num = ""
                                                        if cur_num == cnum:
                                                            break
                                                        self.log(f"[{step_tag or 'modal'}] ⚠️ Card number mismatch (len {len(cur_num)} vs {len(cnum)}), retrying...")
                                                    await vis.nth(1).click(force=True)
                                                    await asyncio.sleep(0.2)
                                                    await vis.nth(1).press_sequentially(f"{expm}{expy}", delay=60)
                                                    await asyncio.sleep(0.3)
                                                    await vis.nth(2).click(force=True)
                                                    await asyncio.sleep(0.2)
                                                    await vis.nth(2).press_sequentially(cvc_v, delay=60)
                                                    await asyncio.sleep(0.3)
                                                    if holder_v and holder_v != "CARD HOLDER":
                                                        name_field = vis.nth(3)
                                                        for _ht in range(3):
                                                            try:
                                                                await name_field.click(force=True)
                                                                await asyncio.sleep(0.15)
                                                                await name_field.fill("")
                                                                await asyncio.sleep(0.15)
                                                                await name_field.fill(holder_v)
                                                                await asyncio.sleep(0.2)
                                                                cur_name = (await name_field.input_value(timeout=1000)).strip()
                                                            except Exception:
                                                                cur_name = ""
                                                            if cur_name == holder_v:
                                                                break
                                                            self.log(f"[{step_tag or 'modal'}] ⚠️ Cardholder name mismatch (got '{cur_name}', expected '{holder_v}'), retrying...")
                                                    else:
                                                        try:
                                                            cur_name = (await vis.nth(3).input_value(timeout=1000)).strip()
                                                            if not cur_name:
                                                                await vis.nth(3).fill(holder_v)
                                                                await asyncio.sleep(0.2)
                                                        except Exception:
                                                            pass
                                                    if cnt >= 5:
                                                        zip_field = vis.nth(4)
                                                        zip_ok = False
                                                        for _zt in range(3):
                                                            await zip_field.click(force=True)
                                                            await asyncio.sleep(0.2)
                                                            try:
                                                                await zip_field.fill("")
                                                            except Exception:
                                                                pass
                                                            await asyncio.sleep(0.15)
                                                            await zip_field.press_sequentially(zip_v, delay=60)
                                                            await asyncio.sleep(0.3)
                                                            try:
                                                                cur_zip = (await zip_field.input_value(timeout=1000)).strip()
                                                            except Exception:
                                                                cur_zip = ""
                                                            if cur_zip == zip_v:
                                                                zip_ok = True
                                                                break
                                                            self.log(f"[{step_tag or 'modal'}] ⚠️ ZIP mismatch (got '{cur_zip}', expected '{zip_v}'), retrying...")
                                                            try:
                                                                await zip_field.fill(zip_v)
                                                                await asyncio.sleep(0.2)
                                                                cur_zip = (await zip_field.input_value(timeout=1000)).strip()
                                                                if cur_zip == zip_v:
                                                                    zip_ok = True
                                                                    break
                                                            except Exception:
                                                                pass
                                                        if not zip_ok:
                                                            self.log(f"[{step_tag or 'modal'}] ⚠️ ZIP could not be verified after retries; proceeding with current value.")
                                                    sbtn = tf.locator('button:has-text("保存卡"), button:has-text("保存"), button:has-text("Save card"), button:has-text("Save"), [role="button"]:has-text("保存")').first
                                                    if await sbtn.count() > 0:
                                                        await sbtn.click(force=True)
                                                    else:
                                                        await tf.evaluate(
                                                            """() => {
                                                            const btns = Array.from(document.querySelectorAll('button, [role="button"]'));
                                                            const b = btns.find(e => (e.innerText || '').includes('保存') || (e.innerText || '').includes('Save'));
                                                            if (b) b.click();
                                                        }"""
                                                        )
                                                    self.log(f"[{step_tag or 'modal'}] ✅ Clicked [Save card], awaiting Google Play confirmation...")
                                                    for _w in range(22):
                                                        await asyncio.sleep(1.0)
                                                        still_im = any("instrumentmanager" in (f.url or "") for f in page.frames)
                                                        if not still_im:
                                                            self.log(f"[{step_tag or 'modal'}] ✅ Inline card binding completed!")
                                                            break
                                                        if _w == 7 and await sbtn.count() > 0:
                                                            try:
                                                                await sbtn.click(force=True)
                                                            except Exception:
                                                                pass
                                                    await asyncio.sleep(2.0)
                                                    inline_card_saved = True
                                                    loops_left = 15
                                                    break
                                            except Exception:
                                                pass
                                        if inline_card_saved:
                                            break
                    except Exception:
                        pass

            # Also check for open intermediate confirm dialog ("How it works -> Continue to subscribe" / "Yes, continue") during the entire wait
            has_eacquire_frame = any("eacquire" in (f.url or "") for f in frames_to_check)
            if not has_eacquire_frame and (time.time() - last_confirm_click_ts) >= 2.5:
                try:
                    confirm_btn = await page.evaluate(
                        """() => {
                        const targets = ["继续订阅", "是的，继续", "换用此方案", "重新订阅", "继续", "同意", "我同意", "前往结账", "获取 Google AI Ultra", "Switch to this plan", "Resubscribe", "Continue to subscribe", "Yes, continue", "Continue", "Agree"];
                        const isTrulyOpenAndTop = (el) => {
                            const selfCs = window.getComputedStyle(el);
                            if (selfCs.visibility === 'hidden' || selfCs.display === 'none' || selfCs.pointerEvents === 'none') return false;
                            let cur = el;
                            while (cur && cur !== document.body && cur !== document.documentElement) {
                                if (cur.getAttribute && cur.getAttribute('aria-hidden') === 'true') return false;
                                const cs = window.getComputedStyle(cur);
                                if (cs.display === 'none' || parseFloat(cs.opacity) < 0.85) return false;
                                cur = cur.parentElement;
                            }
                            const r = el.getBoundingClientRect();
                            const cx = r.left + r.width / 2;
                            const cy = r.top + r.height / 2;
                            if (cx <= 0 || cy <= 0 || cx >= window.innerWidth || cy >= window.innerHeight) return false;
                            const topEl = document.elementFromPoint(cx, cy);
                            if (!topEl) return false;
                            return el.contains(topEl) || topEl.contains(el);
                        };
                        const btns = Array.from(document.querySelectorAll('button, [role="button"]'));
                        for (const b of btns) {
                            const r = b.getBoundingClientRect();
                            if (r.width <= 15 || r.height <= 15) continue;
                            if (!isTrulyOpenAndTop(b)) continue;
                            const t = (b.innerText || '').trim();
                            if (targets.some(lbl => t === lbl || t.includes(lbl))) {
                                b.click();
                                return {text: t, x: r.left + r.width / 2, y: r.top + r.height / 2};
                            }
                        }
                        return null;
                    }"""
                    )
                    if confirm_btn:
                        last_confirm_click_ts = time.time()
                        self.log(f"[{step_tag or 'modal'}] Clicked transition confirmation button: {confirm_btn['text']}")
                        loops_left = max(loops_left, 60)
                except Exception:
                    pass

            await asyncio.sleep(1.0)
        if inline_card_saved:
            return None, "INLINE_CARD_SAVED_RETRY"
        return None, ""

    async def open_and_click_subscribe(self, page, cfg: Dict[str, Any], step_tag: str, expect_keywords: Optional[list] = None) -> bool:
        self.last_subscribe_error = ""
        target_fr = None
        clean_txt = ""

        # Primary trigger: one.google.com/plans?hl=zh-CN clicking the LAST action button (proven 100% reliable across L1, L2, L3)
        transient_reopens = 0
        MAX_TRANSIENT_REOPENS = 2
        for attempt_url in ("https://one.google.com/plans?hl=zh-CN",) * 5:
            if target_fr:
                break
            self.log(f"[{step_tag}] Opening plans page to trigger Ngbm checkout modal: {attempt_url}")
            await self.safe_goto(page, attempt_url, timeout=40000)
            await asyncio.sleep(3.5)
            if "accounts.google.com" in page.url or "gds.google.com" in page.url:
                self.log(f"[{step_tag}] Page triggered authentication redirect, handling verification...")
                await self.pass_reauth_on_page(page)
                await self.safe_goto(page, attempt_url, timeout=40000)
                await asyncio.sleep(3.5)

            await page.evaluate(HOOK_JS)
            await page.evaluate(f"if (typeof window.__setDirectTest === 'function') window.__setDirectTest({json.dumps(cfg)}); else window.__currentTest = {json.dumps(cfg)};")

            fb_btn = None
            for _ in range(10):
                await asyncio.sleep(1.2)
                await page.evaluate(HOOK_JS)
                await page.evaluate(f"if (typeof window.__setDirectTest === 'function') window.__setDirectTest({json.dumps(cfg)}); else window.__currentTest = {json.dumps(cfg)};")
                fb_btn = await page.evaluate(
                    """() => {
                    const els = Array.from(document.querySelectorAll('button, a, [role="button"]'));
                    const btns = els.filter(e => {
                        const t = (e.innerText || '').trim();
                        const r = e.getBoundingClientRect();
                        return r.width > 15 && r.height > 15 && r.left >= 240 && (
                            t.includes('开始使用') ||
                            t.includes('获取优惠') ||
                            t.includes('获取折扣') ||
                            t.includes('换用此方案') ||
                            t === '升级' ||
                            t === 'Upgrade' ||
                            t.includes('获取 Google AI Ultra') ||
                            t.includes('Get started') ||
                            t.includes('Get offer')
                        );
                    });
                    if (btns.length === 0) return null;

                    // Read the plan-card text around a button (for logging only).
                    const cardText = (el) => {
                        let n = el, depth = 0, best = '';
                        while (n && depth < 6) {
                            const tx = (n.innerText || '');
                            if (tx.length > best.length) best = tx;
                            n = n.parentElement; depth++;
                        }
                        return best;
                    };

                    // Original proven behavior on the hl=zh-CN plans page: click the LAST
                    // matching action button (reliable across Layer 1/2/3 stacking).
                    const btn = btns[btns.length - 1];
                    btn.scrollIntoView({block: 'center'});
                    const r = btn.getBoundingClientRect();
                    btn.click();
                    return {x: r.left + r.width / 2, y: r.top + r.height / 2, text: (btn.innerText || '').trim(), card: cardText(btn).replace(/\\s+/g, ' ').slice(0, 70)};
                }"""
                )
                if fb_btn:
                    self.log(f"[{step_tag}] Clicked [{fb_btn.get('text', '')}] trigger button (card: {fb_btn.get('card', '')}), waiting for Google Play checkout modal...")
                    break

            if fb_btn:
                target_fr, clean_txt = await self._poll_eacquire_modal(page, max_loops=60, step_tag=step_tag)
                if clean_txt == "INLINE_CARD_SAVED_RETRY":
                    self.log(f"[{step_tag}] Inline card binding completed, reopening plans page for $0 checkout modal...")
                    continue
                if clean_txt == "TRANSIENT_REOPEN":
                    transient_reopens += 1
                    if transient_reopens > MAX_TRANSIENT_REOPENS:
                        self.last_subscribe_error = (
                            "Google checkout kept returning 'Something went wrong, please try again' "
                            f"after {MAX_TRANSIENT_REOPENS} reopens — likely the account/region/card is not "
                            "eligible for this offer right now, or the IP/VPN region does not match the target region."
                        )
                        self.log(f"❌ [{step_tag}] {self.last_subscribe_error}")
                        break
                    self.log(f"[{step_tag}] Transient checkout error, reopened plans page ({transient_reopens}/{MAX_TRANSIENT_REOPENS})...")
                    continue

        await self.save_screenshot(page, f"{step_tag}_modal")

        if not target_fr:
            self.log(f"⚠️ [{step_tag}] Google Play checkout modal not detected (eacquire/multiline)")
            return False

        self.log(f"[{step_tag}] Checkout modal content: {clean_txt[:350]}")
        if "错误" in clean_txt or "Something went wrong" in clean_txt or "unable to change" in clean_txt or "出了点问题" in clean_txt:
            self.log(f"⚠️ [{step_tag}] Modal returned error notice: {clean_txt[:200]}")
            return False

        # Ensure the configured card (last4) is selected in the eacquire modal if multiple cards exist
        card_num_clean = re.sub(r"\s+", "", self.card.get("number", ""))
        target_last4 = card_num_clean[-4:] if len(card_num_clean) >= 4 else ""
        if target_last4 and target_last4 not in clean_txt and ("Mastercard-" in clean_txt or "Visa-" in clean_txt or "PaysafeCard" in clean_txt):
            self.log(f"[{step_tag}] Default card does not end in {target_last4}, switching to {target_last4}...")
            try:
                pm_loc = target_fr.locator('text=/Mastercard-|Visa-|PaysafeCard/i').first
                if await pm_loc.count() > 0:
                    await pm_loc.click(timeout=5000)
                    await asyncio.sleep(2.5)
                    await self.save_screenshot(page, f"{step_tag}_pm_list")
                    card_loc = target_fr.locator(f'text=/{target_last4}/').first
                    if await card_loc.count() > 0:
                        await card_loc.click(timeout=5000)
                        self.log(f"✅ [{step_tag}] Successfully switched to card ending in {target_last4} in modal!")
                        await asyncio.sleep(3.0)
                        refreshed_fr, refreshed_txt = await self._poll_eacquire_modal(page, max_loops=10)
                        if refreshed_fr:
                            target_fr, clean_txt = refreshed_fr, refreshed_txt
                            self.log(f"[{step_tag}] Modal content after card switch: {clean_txt[:350]}")
                        await self.save_screenshot(page, f"{step_tag}_modal_switched_{target_last4}")
                    else:
                        self.log(f"⚠️ [{step_tag}] Card ending in {target_last4} not found in payment methods list")
            except Exception as pm_err:
                self.log(f"[{step_tag}] Note on switching card: {pm_err}")

        # Strict zero-cost protection when running 2-year Pro stacking (rejects $10.00 / $20.00 false matches)
        has_zero_trial = (
            bool(_ZERO_PRICE_RE.search(clean_txt))
            or "免费试用" in clean_txt
            or "Free trial" in clean_txt
            or "0.00/mo" in clean_txt
            or "0.00 per" in clean_txt
            or "0.00 for" in clean_txt
        )
        if "ultra" not in step_tag.lower() and not has_zero_trial:
            self.last_subscribe_error = f"Zero-cost protection: modal did not show 0.00 free trial ({clean_txt[:180]})"
            self.log(f"❌ [{step_tag}] {self.last_subscribe_error}")
            return False

        if expect_keywords:
            for kw in expect_keywords:
                if kw not in clean_txt:
                    self.log(f"⚠️ [{step_tag}] Modal missing expected keyword '{kw}', content: {clean_txt[:200]}")

        all_btns = target_fr.locator('button, [role="button"]')
        bcnt = await all_btns.count()
        clicked = False
        for i in range(bcnt):
            b = all_btns.nth(i)
            try:
                btxt = (await b.inner_text()).strip()
                if btxt in ("订阅", "Subscribe"):
                    self.log(f"--> Clicking [{btxt}] button for [{step_tag}]...")
                    try:
                        await b.scroll_into_view_if_needed(timeout=3000)
                        await b.click(timeout=5000)
                        clicked = True
                    except Exception:
                        await b.click(force=True, timeout=5000)
                        clicked = True
                    break
            except Exception as click_err:
                self.log(f"[{step_tag}] Standard click note ({click_err}), using native JS click...")

        if not clicked:
            js_clicked = await target_fr.evaluate(
                """() => {
                const btns = Array.from(document.querySelectorAll('button, [role="button"]'));
                const sub = btns.find(b => {
                    const bt = (b.innerText || '').trim();
                    return bt === '订阅' || bt === 'Subscribe';
                });
                if (sub) {
                    sub.dispatchEvent(new MouseEvent('mousedown', {bubbles: true, cancelable: true}));
                    sub.dispatchEvent(new MouseEvent('mouseup', {bubbles: true, cancelable: true}));
                    sub.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true}));
                    sub.click();
                    return true;
                }
                return false;
            }"""
            )
            if js_clicked:
                self.log(f"--> Triggered [Subscribe] button click via native JS for [{step_tag}]!")
                clicked = True

        if not clicked:
            self.log(f"⚠️ [{step_tag}] [Subscribe] button not found")
            return False

        # Monitor for CVC prompt, Google password re-auth, or post-click error/completion
        cvc_code = (self.card.get("cvc") or "").strip()
        acct_pwd = self.account.get("password", "")
        post_click_err = None
        for wait_i in range(35):
            await asyncio.sleep(2)
            if len(page.context.pages) > 1:
                await self.pass_reauth_on_page(page.context.pages[-1])

            eacquire_present = False
            reauth_spinning = False
            ordered_frames = [f for f in page.frames if f != page.main_frame] + [page.main_frame]
            for f2 in ordered_frames:
                f2_url = f2.url or ""
                if "eacquire" in f2_url:
                    eacquire_present = True
                try:
                    f2_txt = (await f2.inner_text("body", timeout=1500)).strip()
                except Exception:
                    f2_txt = ""

                if "验证是您本人" in f2_txt or "验证您的身份" in f2_txt or "Verify it's you" in f2_txt:
                    reauth_spinning = True

                if "eacquire" in f2_url or "instrument" in f2_url or "challenge" in f2_url or "accounts.google.com" in f2_url or "reauth" in f2_url:
                    try:
                        # 1. Check if there is a password input
                        pwd_inp = f2.locator('input[type="password"], input[name="Passwd"]')
                        if await pwd_inp.count() > 0 and await pwd_inp.first.is_visible():
                            is_cvc_box = ("CVC" in f2_txt or "安全码" in f2_txt or "security code" in f2_txt.lower())
                            if not is_cvc_box:
                                self.log("Detected Google password re-auth prompt, filling password...")
                                await pwd_inp.first.fill(acct_pwd)
                                await asyncio.sleep(0.4)
                                vbtns = f2.locator(
                                    'button:has-text("验证"), button:has-text("下一步"), button:has-text("确认"), button:has-text("继续"), button:has-text("Verify"), button:has-text("Next"), #passwordNext'
                                )
                                if await vbtns.count() > 0 and await vbtns.first.is_visible():
                                    await vbtns.first.click()
                                else:
                                    await page.keyboard.press("Enter")
                                await asyncio.sleep(3)
                                continue

                        # 2. Check if there is a 2FA TOTP input
                        totp_inp = f2.locator('input#totpPin, input[name="totpPin"]')
                        if await totp_inp.count() > 0 and await totp_inp.first.is_visible():
                            code = await self.ensure_totp_fresh()
                            if code:
                                self.log("Detected embedded 2FA prompt, entering code: [******]")
                                await totp_inp.first.fill(code)
                                await page.keyboard.press("Enter")
                                await asyncio.sleep(3)
                                continue

                        # 3. Check if there is a CVC input
                        cvc_inp = f2.locator('input[autocomplete="cc-csc"], input[name="cvc"]')
                        if await cvc_inp.count() == 0 and ("CVC" in f2_txt or "安全码" in f2_txt or "security code" in f2_txt.lower()):
                            cvc_inp = f2.locator('input[type="tel"], input[type="password"]')
                        if await cvc_inp.count() > 0 and await cvc_inp.first.is_visible():
                            self.log("Detected card security code (CVC) prompt, filling CVC: [***]")
                            await cvc_inp.first.fill(cvc_code)
                            await asyncio.sleep(0.5)
                            vbtns = f2.locator(
                                'button:has-text("验证"), button:has-text("确认"), button:has-text("继续"), button:has-text("订阅"), button:has-text("Verify"), button:has-text("Confirm")'
                            )
                            if await vbtns.count() > 0:
                                await vbtns.first.click()
                            else:
                                await page.keyboard.press("Enter")
                            await asyncio.sleep(3)
                            continue

                        # 4. Check if there is a "Verify it's you" / "Continue" / "Next" trigger button without visible inputs
                        if "验证是您本人" in f2_txt or "验证您的身份" in f2_txt or "Verify it's you" in f2_txt:
                            vbtn = f2.locator('button:has-text("验证"), button:has-text("继续"), button:has-text("下一步"), button:has-text("Verify"), button:has-text("Continue"), button:has-text("Next"), [role="button"]:has-text("验证"), [role="button"]:has-text("继续")').first
                            if await vbtn.count() > 0 and await vbtn.is_visible():
                                self.log(f"Detected [Verify it's you] confirmation button ({await vbtn.inner_text()}), clicking...")
                                await vbtn.click()
                                await asyncio.sleep(2.5)
                    except Exception:
                        pass

                if f2_txt:
                    try:
                        ft_after = f2_txt
                        clean_after = " | ".join([l.strip() for l in ft_after.splitlines() if l.strip()])
                        if any(
                            ok_kw in clean_after
                            for ok_kw in (
                                "已订阅",
                                "Subscribed",
                                "You're subscribed",
                                "感谢您订阅",
                                "通过此类购买活动获取",
                                "Google Play 积分",
                                "Play Points",
                                "Earn Google Play Points",
                            )
                        ):
                            self.log(f"✅ [{step_tag}] Detected subscription confirmation success message!")
                            await self.save_screenshot(page, f"{step_tag}_after_click")
                            try:
                                dismiss_btn = f2.locator('button:has-text("以后再说"), [role="button"]:has-text("以后再说"), button:has-text("Not now"), [role="button"]:has-text("Not now"), button:has-text("不用了"), button:has-text("知道了")').first
                                if await dismiss_btn.count() > 0 and await dismiss_btn.is_visible():
                                    self.log(f"[{step_tag}] Clicking [Not now] to close modal and sync subscription status...")
                                    try:
                                        await dismiss_btn.click(force=True, timeout=3000)
                                    except Exception:
                                        pass
                                    await f2.evaluate(
                                        """() => {
                                        const els = Array.from(document.querySelectorAll('button, [role="button"], span, div'));
                                        const b = els.find(e => {
                                            const t = (e.innerText || '').trim();
                                            return t === '以后再说' || t === 'Not now' || t === '不用了' || t === '知道了';
                                        });
                                        if (b) {
                                            b.dispatchEvent(new MouseEvent('mousedown', {bubbles: true, cancelable: true}));
                                            b.dispatchEvent(new MouseEvent('mouseup', {bubbles: true, cancelable: true}));
                                            b.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true}));
                                            b.click();
                                        }
                                    }"""
                                    )
                                    await asyncio.sleep(6.0)
                                else:
                                    await asyncio.sleep(5.0)
                            except Exception:
                                await asyncio.sleep(4.0)
                            return True
                        err_keywords = [
                            "出了点问题",
                            "无法完成",
                            "拒绝了您的付款",
                            "付款遭拒",
                            "支付遭拒",
                            "由于资金不足",
                            "[OR-",
                            "[OR_",
                            "OR_FGVEM",
                            "[BX-CPL-05]",
                            "您的订单仍在处理中",
                            "订单处理完成后",
                            "购买失败",
                            "Something went wrong",
                            "couldn't be completed",
                            "Check that you have the correct country",
                            "declined",
                            "insufficient funds",
                        ]
                        if any(err_kw in clean_after for err_kw in err_keywords):
                            err_lines = [line.strip() for line in ft_after.splitlines() if any(err_kw in line for err_kw in err_keywords)]
                            post_click_err = " | ".join(err_lines) if err_lines else clean_after[:300]
                            break
                    except Exception:
                        pass
            if post_click_err:
                break
            if wait_i >= 3 and not eacquire_present and not reauth_spinning:
                # Check main page for "Purchase failed" toast or "Welcome! Your new membership is active" before assuming modal closure result
                try:
                    main_body_txt = (await page.main_frame.inner_text("body", timeout=2000)).strip()
                except Exception:
                    main_body_txt = ""
                if "购买失败" in main_body_txt or "Purchase failed" in main_body_txt or "出了点问题" in main_body_txt or "[BX-CPL-05]" in main_body_txt:
                    post_click_err = "Detected error notice: Purchase failed or order settlement incomplete ([BX-CPL-05])"
                    break
                if "欢迎您" in main_body_txt or "新会员已生效" in main_body_txt or "Welcome" in main_body_txt:
                    self.log(f"✅ [{step_tag}] Detected Google One new membership active welcome page, subscribed successfully!")
                    await self.save_screenshot(page, f"{step_tag}_after_click")
                    await asyncio.sleep(4.0)
                    return True

                self.log(f"[{step_tag}] Modal closed. Checking Google Play subscriptions directly for confirmation...")
                try:
                    play_check = await self.inspect_play_subscriptions(page)
                    if "Google AI" in play_check or "Google One" in play_check or "5 TB" in play_check or "2027" in play_check or "2028" in play_check:
                        self.log(f"✅ [{step_tag}] Confirmed active subscription on Google Play!")
                        await self.save_screenshot(page, f"{step_tag}_after_click")
                        return True
                except Exception:
                    pass

                self.log(f"⚠️ [{step_tag}] Checkout modal closed; verifying backend subscription status...")
                await asyncio.sleep(5.0)
                await self.save_screenshot(page, f"{step_tag}_after_click")
                post_click_err = "Checkout modal closed without explicit confirmation flag, checking status..."
                break

        await self.save_screenshot(page, f"{step_tag}_after_click")
        if post_click_err:
            self.last_subscribe_error = post_click_err
            self.log(f"❌ [{step_tag}] Error returned after clicking subscribe: {post_click_err[:300]}")
            return False
        self.last_subscribe_error = "Timeout waiting for modal"
        self.log(f"⚠️ [{step_tag}] Timeout waiting for modal completion (reauth_spinning={reauth_spinning})")
        return False

    async def step2_layer1_12m(self, page):
        self.update_state(status="running", current_step=2, step_message="Step 2/5: Applying Layer 1 12-month trial (US$0.00)...")
        self.log("=== Step 2/5: Applying Layer 1 12-Month Trial + YouTube 1M Promo (US$0.00 Zero Cost) ===")

        play_txt = await self.inspect_play_subscriptions(page)
        if "2028" in play_txt:
            self.log("✅ Account already has coverage extending into 2028, skipping Step 2!")
            return
        if any(k in play_txt for k in ("2027年10月", "2027年11月", "2027年12月", "Oct 2027", "Nov 2027", "Dec 2027")):
            if "重新订阅" in play_txt or "Resubscribe" in play_txt:
                await self.ensure_subscription_active(page)
            self.log("✅ Account already completed Layer 1 12-month trial, skipping Step 2!")
            return

        if "重新订阅" in play_txt or "Resubscribe" in play_txt:
            await self.ensure_subscription_active(page)

        sub_id = None
        if "Google AI" in play_txt or "Google One" in play_txt:
            sub_id = await self.get_current_sub_id(page)

        cfg_l1 = {
            "name": "Step2_Layer1_12M_YTFree_ZeroCost",
            "full_items": [
                [[PREFIX + "g1.2tb.ai.subs_variant", 21], None, None, None, 1, [TOK_PRO5TB_12M]],
                [[PREFIX + "g1.youtube_premium_individual_add_on", 21], None, None, None, 1, [YT_FREE_TOK]],
            ],
            "ear8": [None, 5, None, None, None, [sub_id]] if sub_id else None,
        }
        ok = await self.open_and_click_subscribe(page, cfg_l1, "step2_layer1_12m")
        if not ok:
            self.log("⚠️ Step 2 first attempt did not complete checkout, waiting 3s to retry (2/2)...")
            await asyncio.sleep(3.0)
            ok = await self.open_and_click_subscribe(page, cfg_l1, "step2_layer1_12m")
        if not ok:
            step2_milestones = ["2028", "2027年10月", "2027年11月", "2027年12月", "Oct 2027", "Nov 2027", "Dec 2027"]
            if await self.recheck_coverage_complete(page, step2_milestones, "step2_layer1_12m"):
                return
            raise RuntimeError(f"Step 2 (Layer 1 12-Month Trial US$0.00) failed: {self.last_subscribe_error}")
        self.log("✅ Step 2 (Layer 1 12-Month Trial US$0.00) completed! Waiting 45s for backend settlement...")
        await asyncio.sleep(45)

    async def step3_layer2_6mss(self, page):
        self.update_state(status="running", current_step=3, step_message="Step 3/5: Applying Layer 2 6-month stack (US$0.00 -> 18 months)...")
        self.log("=== Step 3/5: Applying Layer 2 6-Month Promo (S25 Pro 6M + YT, US$0.00 -> 18 Months) ===")

        play_txt = await self.inspect_play_subscriptions(page)
        if any(k in play_txt for k in ("2028年4月", "2028年5月", "2028年6月", "2028年10月", "2028年11月", "2028年12月", "Apr 2028", "May 2028", "Oct 2028", "Nov 2028", "Dec 2028")):
            if ("重新订阅" in play_txt or "Resubscribe" in play_txt) and not any(k in play_txt for k in ("2028年10月", "2028年11月", "2028年12月", "Oct 2028", "Nov 2028", "Dec 2028")):
                await self.ensure_subscription_active(page)
            self.log("✅ Account already reaches 2028 (>=18 months), skipping Step 3!")
            return

        if "重新订阅" in play_txt or "Resubscribe" in play_txt:
            await self.ensure_subscription_active(page)

        sub_id = await self.get_current_sub_id(page)

        cfg_l2 = {
            "name": "Step3_Layer2_6MSS_YTFree_ZeroCost",
            "full_items": [
                [[PREFIX + "g1.2tb.ai.subs_variant", 21], None, None, None, 1, [TOK_PRO5TB_6M_SS]],
                [[PREFIX + "g1.youtube_premium_individual_add_on", 21], None, None, None, 1, [YT_FREE_TOK]],
            ],
            "ear8": [None, 5, None, None, None, [sub_id]] if sub_id else None,
        }
        ok = await self.open_and_click_subscribe(page, cfg_l2, "step3_layer2_6mss")
        if not ok:
            self.log("⚠️ Step 3 first attempt did not complete checkout, waiting 3s to retry (2/2)...")
            await asyncio.sleep(3.0)
            ok = await self.open_and_click_subscribe(page, cfg_l2, "step3_layer2_6mss")
        if not ok:
            step3_milestones = ["2028年4月", "2028年5月", "2028年6月", "2028年10月", "2028年11月", "2028年12月", "Apr 2028", "May 2028", "Oct 2028", "Nov 2028", "Dec 2028"]
            if await self.recheck_coverage_complete(page, step3_milestones, "step3_layer2_6mss"):
                return
            raise RuntimeError(f"Step 3 (Layer 2 6-Month Stack US$0.00) failed: {self.last_subscribe_error}")
        self.log("✅ Step 3 (Layer 2 6-Month Stack US$0.00 -> 18 Months) completed! Waiting 45s to sync...")
        await asyncio.sleep(45)

    async def step4_layer3_6m(self, page):
        self.update_state(status="running", current_step=4, step_message="Step 4/5: Applying Layer 3 6-month cap (US$0.00 -> 24 months / 2028)...")
        self.log("=== Step 4/5: Applying Layer 3 6-Month Promo Cap (Pixel 6M + YT, US$0.00 -> 24 Months) ===")

        play_txt = await self.inspect_play_subscriptions(page)
        if any(k in play_txt for k in ("2028年10月", "2028年11月", "2028年12月", "Oct 2028", "Nov 2028", "Dec 2028")):
            self.log("✅ Account already reached 24-month free period (Late 2028), skipping Step 4!")
            return

        if "重新订阅" in play_txt or "Resubscribe" in play_txt:
            await self.ensure_subscription_active(page)

        sub_id = await self.get_current_sub_id(page)

        ok = False
        for tag, tok in [("step4_layer3_6m", TOK_PRO5TB_6M), ("step4_layer3_6m2", TOK_PRO5TB_6M_2)]:
            cfg_l3 = {
                "name": tag,
                "full_items": [
                    [[PREFIX + "g1.2tb.ai.subs_variant", 21], None, None, None, 1, [tok]],
                    [[PREFIX + "g1.youtube_premium_individual_add_on", 21], None, None, None, 1, [YT_FREE_TOK]],
                ],
                "ear8": [None, 5, None, None, None, [sub_id]] if sub_id else None,
            }
            ok = await self.open_and_click_subscribe(page, cfg_l3, tag)
            if ok:
                break
            await asyncio.sleep(3.0)

        if not ok:
            step4_milestones = ["2028年10月", "2028年11月", "2028年12月", "Oct 2028", "Nov 2028", "Dec 2028"]
            if await self.recheck_coverage_complete(page, step4_milestones, "step4_layer3_6m"):
                return
            raise RuntimeError(f"Step 4 (Layer 3 6-Month Cap US$0.00) failed: {self.last_subscribe_error}")

        self.log("✅ Step 4 (Layer 3 6-Month Cap US$0.00 -> 24 Months) completed! Waiting 40s to sync final expiration...")
        await asyncio.sleep(40)
        play_after = await self.inspect_play_subscriptions(page)
        if "2028" not in play_after:
            raise RuntimeError(
                "Step 4 modal closed, but the Play subscription expiry did NOT advance to 2028 "
                f"(current Play status: {play_after[:120]})"
            )
        self.log("✅ Step 4 (US$0.00 2-year stack to 2028) verified on Play Store!")

    async def step5_cancel_and_verify(self, page):
        self.update_state(status="running", current_step=5, step_message="Step 5/5: Auto-canceling renewal to lock in 2-year expiration (US$0.00 proof)...")
        self.log("=== Step 5/5: Auto-canceling renewal & capturing proof screenshot ===")

        await self.safe_goto(page, "https://play.google.com/store/account/subscriptions?hl=zh-CN", timeout=40000)
        await asyncio.sleep(4)
        play_txt = await page.inner_text("body")

        if self.auto_cancel and not any(k in play_txt for k in ("取消日期", "重新订阅", "已取消", "Cancels on", "Resubscribe", "Cancelled", "Canceled")) :
            mbtn = page.locator("text=/^管理$|^Manage$/i").first
            for i in range(await mbtn.count()):
                if await mbtn.nth(i).is_visible():
                    await mbtn.nth(i).click()
                    break

            for _ in range(15):
                await asyncio.sleep(0.5)
                cbtn = page.locator("text=/^取消订阅$|^Cancel subscription$/i").first
                if await cbtn.count() > 0 and await cbtn.first.is_visible():
                    await cbtn.first.click()
                    break

            await asyncio.sleep(2.5)
            # Handle optional retention screen ("No thanks" / "Continue")
            no_thanks = page.locator('button:has-text("不，谢谢"), button:has-text("不用了"), button:has-text("No thanks"), [role="button"]:has-text("不，谢谢"), [role="button"]:has-text("不用了")')
            if await no_thanks.count() > 0 and await no_thanks.first.is_visible():
                await no_thanks.first.click()
                await asyncio.sleep(2.0)

            # Click visible [role="radio"] (and/or reason text) to enable the "Continue" button
            await page.evaluate(
                """() => {
                const radios = Array.from(document.querySelectorAll('[role="radio"], input[type="radio"]')).filter(el => {
                    const r = el.getBoundingClientRect();
                    return r.width > 0 && r.height > 0;
                });
                if (radios.length > 0) {
                    const target = radios[radios.length - 1];
                    target.click();
                    if (target.parentElement) target.parentElement.click();
                }
            }"""
            )
            await asyncio.sleep(0.5)
            vis_radios = page.locator('[role="radio"]:visible, input[type="radio"]:visible')
            if await vis_radios.count() > 0:
                try:
                    await vis_radios.last.click(force=True)
                except Exception:
                    pass
            reason = page.locator('text=/拒绝回答|不愿透露|其他原因|暂不需要|Decline to answer/i').first
            if await reason.count() > 0 and await reason.is_visible():
                try:
                    await reason.click(force=True)
                except Exception:
                    pass

            await asyncio.sleep(0.8)
            cnext = page.locator('button:has-text("Continue"):not([disabled]), button:has-text("继续"):not([disabled]), [role="button"]:has-text("Continue"):not([aria-disabled="true"]), [role="button"]:has-text("继续"):not([aria-disabled="true"])')
            for i in range(await cnext.count()):
                if await cnext.nth(i).is_visible():
                    await cnext.nth(i).click(timeout=8000)
                    break
            await asyncio.sleep(2.5)

            final_cbtn = page.locator("text=/^取消订阅$|^Cancel subscription$/i").first
            fcnt = await final_cbtn.count()
            for i in range(fcnt - 1, -1, -1):
                if await final_cbtn.nth(i).is_visible():
                    self.log("Clicking final [Cancel subscription] confirmation button...")
                    await final_cbtn.nth(i).click(timeout=8000)
                    break
            await asyncio.sleep(4)
            if len(page.context.pages) > 1:
                await self.pass_reauth_on_page(page.context.pages[-1])
            await asyncio.sleep(3)

        # Final verification on Play Subscriptions
        await self.safe_goto(page, "https://play.google.com/store/account/subscriptions?hl=zh-CN", timeout=40000)
        await asyncio.sleep(3.5)
        final_shot = await self.save_screenshot(page, "step5_final_verified")
        final_txt = " | ".join([l.strip() for l in (await page.inner_text("body")).splitlines() if l.strip()])
        self.log(f"Final Play subscriptions page status: {final_txt[:400]}")

        # Parse expiry date from final_txt
        m_date = re.search(r"(?:Cancels on|Renews on|Expires on|Next billing date:?|Cancel date:?|将于|取消日期[：:]\s*|续订日期[：:]\s*)([A-Za-z]+ \d{1,2}, \d{4}|\d{4}年\d{1,2}月\d{1,2}日|\d{1,2} [A-Za-z]+ \d{4})", final_txt, re.I)
        exp_str = m_date.group(1) if m_date else "Active (See screenshot)"
        plan_found = "Google AI Pro (5 TB)" if "5 TB" in final_txt else ("Google AI Ultra (20 TB)" if "20 TB" in final_txt else ("Google AI Ultra (30 TB)" if "30 TB" in final_txt else "Google AI Pro"))

        self.update_state(
            status="completed",
            current_step=5,
            step_message=f"✅ 2-Year Zero-Cost Complete! Plan: {plan_found} | Expiration: {exp_str}",
            expiry_date=exp_str,
            last_screenshot=final_shot,
        )
        self.log(f"🎉 Account {self.account['email']} 2-Year Zero-Cost Pro pipeline finished! Plan: {plan_found}, Expiration: {exp_str}")

    # Keep step7_cancel_and_verify alias for compatibility
    step7_cancel_and_verify = step5_cancel_and_verify

    async def verify_ip_environment(self, page):
        """Check current browser exit IP and country before touching Google."""
        for probe_url in ("https://ipinfo.io/json", "https://ipwho.is/"):
            try:
                await page.goto(probe_url, wait_until="domcontentloaded", timeout=16000)
                txt = await page.inner_text("body")
                data = json.loads(txt)
                if "ipwho" in probe_url and not data.get("success", True):
                    continue
                ip = data.get("ip", "Unknown")
                cc = (data.get("country_code") or data.get("country") or "??").upper()
                city = data.get("city", "")
                org = (data.get("connection", {}).get("org") if isinstance(data.get("connection"), dict) else data.get("org", "")) or ""
                flag = "🇧🇴" if cc == "BO" else ("🇹🇷" if cc == "TR" else ("🇺🇸" if cc == "US" else "🌐"))
                ip_badge = f"{flag} {ip[:22]} ({cc}{'·' + city if city else ''})"
                self.update_state(ip_info=ip_badge)
                self.log(f"🌐 Egress IP verified: {flag} {ip} ({cc} {city}) | Org: {org[:35]}")
                return
            except Exception:
                if self.proxy_bridge:
                    self.proxy_bridge.rotate_session()
                continue
        self.log("⚠️ IP check endpoint slow to respond, continuing...")

    async def run_pipeline(self, start_step: int = 1):
        cnum_check = re.sub(r"\s+", "", self.card.get("number") or "")
        expm_check = (self.card.get("exp_month") or "").strip()
        expy_check = (self.card.get("exp_year") or "").strip()
        cvc_check = (self.card.get("cvc") or "").strip()
        if start_step <= 4 and (len(cnum_check) < 12 or not expm_check or not expy_check or not cvc_check):
            err_card = "Please enter payment card info (Card number, Expiration MM/YY, CVC) under Global Configuration and save first!"
            self.log(f"❌ {err_card}")
            self.update_state(status="error", step_message=f"❌ {err_card}")
            return

        user_data_dir = get_profile_dir(self.account["id"], self.account["email"])
        resolved_proxy = resolve_account_proxy(self.account, self.target_region, self.proxy_template)
        parsed_proxy = parse_proxy_string(resolved_proxy)

        chrome_args = [
            CHROME_PATH,
            f"--remote-debugging-port={self.port}",
            f"--user-data-dir={user_data_dir}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-popup-blocking",
            "--disable-background-networking",
            "--disable-sync",
            "--disable-component-update",
            "--disable-domain-reliability",
            "--no-pings",
            "--webrtc-ip-handling-policy=disable_non_proxied_udp",
            "--enforce-webrtc-ip-permission-check",
            "--start-maximized",
        ]

        if parsed_proxy:
            if parsed_proxy.get("username"):
                bridge_port = self.port + 200
                self.proxy_bridge = LocalHttpProxyAuthBridge(bridge_port, parsed_proxy)
                await self.proxy_bridge.start()
                chrome_args.append(f"--proxy-server=http://127.0.0.1:{bridge_port}")
                self.log(
                    f"🛡️ Attached residential proxy tunnel for [{self.account['id']}] -> {parsed_proxy['host']}:{parsed_proxy['port']} (Proxy account: [***])"
                )
            else:
                p_url = f"{parsed_proxy['scheme']}://{parsed_proxy['host']}:{parsed_proxy['port']}"
                chrome_args.append(f"--proxy-server={p_url}")
                self.log(f"🛡️ Attached proxy for [{self.account['id']}] -> {p_url}")
        else:
            self.log(f"Starting isolated Chrome environment (Port: {self.port}, using system current network)...")

        chrome_args.append("about:blank")

        if True:
            # Cleanly terminate any lingering Chrome instance holding the same user_data_dir (e.g. from manual Browser button)
            lockfile_path = os.path.join(user_data_dir, "lockfile")
            if os.path.exists(lockfile_path):
                try:
                    os.remove(lockfile_path)
                except OSError:
                    self.log("Closing existing manual Chrome session for this profile before starting automation...")
                    profile_folder = os.path.basename(os.path.normpath(user_data_dir))
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
                    except Exception:
                        pass

            # Cleanly kill any lingering process listening on self.port
            try:
                net_out = subprocess.check_output(
                    ["netstat", "-ano"], stderr=subprocess.DEVNULL
                ).decode("gbk", errors="ignore")
                port_tag = f":{self.port}"
                for line in net_out.splitlines():
                    if "LISTENING" in line and port_tag in line:
                        old_pid = line.strip().split()[-1]
                        if old_pid.isdigit():
                            subprocess.run(
                                ["taskkill", "/F", "/T", "/PID", old_pid],
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL,
                            )
            except Exception:
                pass
            await asyncio.sleep(0.8)

        try:
            self.proc = subprocess.Popen(chrome_args)
            async with async_playwright() as p:
                browser = None
                last_cdp_err = None
                for _ in range(20):
                    await asyncio.sleep(0.7)
                    if self.proc.poll() is not None:
                        raise RuntimeError(
                            f"Chrome process exited prematurely (exit code {self.proc.returncode}). "
                            "Ensure no other Chrome window is locking this account profile."
                        )
                    try:
                        browser = await p.chromium.connect_over_cdp(f"http://127.0.0.1:{self.port}")
                        break
                    except Exception as cdp_err:
                        last_cdp_err = cdp_err
                if browser is None:
                    raise RuntimeError(f"Timed out connecting to Chrome CDP on port {self.port}: {last_cdp_err}")

                context = browser.contexts[0]
                await context.add_init_script("window.__INIT_TEST = null;\n" + HOOK_JS)

                page = context.pages[0] if context.pages else await context.new_page()
                await page.set_viewport_size({"width": 1400, "height": 950})

                await self.verify_ip_environment(page)

                step1_fn = self.step1_manual_pause if self.manual_login else self.step1_login
                steps = [
                    (1, lambda: step1_fn(page)),
                    (2, lambda: self.step2_layer1_12m(page)),
                    (3, lambda: self.step3_layer2_6mss(page)),
                    (4, lambda: self.step4_layer3_6m(page)),
                    (5, lambda: self.step5_cancel_and_verify(page)),
                ]

                for step_num, step_fn in steps:
                    self.check_stop()
                    if step_num < start_step:
                        continue
                    await step_fn()

                await browser.close()
        except Exception as e:
            err_msg = str(e)
            self.log(f"❌ Execution interrupted: {err_msg}")
            self.update_state(status="error", step_message=f"❌ Step {self.account.get('current_step', 1)} Error: {err_msg[:120]}")
        finally:
            if self.proxy_bridge:
                await self.proxy_bridge.stop()
            if self.proc:
                try:
                    subprocess.run(
                        ["taskkill", "/F", "/T", "/PID", str(self.proc.pid)],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                except Exception:
                    pass
