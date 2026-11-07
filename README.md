# Google AI Pro (5 TB) 24-Month Provisioning Console

**English** | [简体中文](README_ZH.md)

Local batch-automation console that provisions 24-month Google One AI Pro (5 TB) trial stacks across Google accounts using Playwright CDP and `Ngbm` checkout RPC interception.

> [!CAUTION]
> **Disclaimer & Account Risk Warning — Use at Your Own Risk**
>
> This software is provided strictly for educational and security research purposes. Automated interaction with Google authentication flows, Google Play billing endpoints, and promotional trial token stacking violates Google's Terms of Service and Google One promotional policies.
>
> Using this tool may result in:
> - Immediate revocation of promotional subscriptions or storage benefits
> - Google Payments profile holds or verification locks
> - Temporary or permanent suspension of your Google account(s)
>
> You are solely responsible for any accounts, payment methods, or network proxies you use with this software. The repository author assumes **no liability or responsibility** for banned accounts, revoked subscriptions, or billing issues.

---

## How the 5-Stage Pipeline Works

| Stage | Operation | Cumulative Duration | Checkout Guard |
| :--- | :--- | :--- | :--- |
| **Step 01** | Google session login, recovery email challenge, and 2FA TOTP verification | Auth | — |
| **Step 02** | Layer 1 base trial (`5 TB AI Pro 12M`) + inline Oregon (`97201`) tax-free card binding | `0M → 12M` | `US$0.00` required |
| **Step 03** | Layer 2 promotional token stack (`S25 +6M`, `ear8` mode `5` append) | `12M → 18M` | `US$0.00` required |
| **Step 04** | Layer 3 promotional token cap (`Pixel +6M`, `ear8` mode `5` cap) | `18M → 24M` | `US$0.00` required |
| **Step 05** | Google Play auto-renewal cancellation and verification screenshot capture | `24M locked` | Recurring billing disabled |

---

## Requirements

- **OS**: Windows 10 / 11
- **Browser**: Google Chrome installed at the default system path
- **Runtime**: Python 3.10+ (`Add Python to PATH` enabled)
- **Network**: United States (US) egress IP via system VPN/proxy, or an outbound HTTP/SOCKS5 proxy configured in the dashboard

---

## Quick Start

1. **First run (installs dependencies and starts the server)**:
   Double-click `setup.bat` (or run `pip install -r requirements.txt` followed by `python app.py`).
2. **Subsequent runs**:
   Double-click `start.bat`.
3. **Open the console**:
   Navigate to `http://127.0.0.1:8765`.

> [!NOTE]
> On first launch, `app.py` automatically generates a local `config.json` pre-filled with the default Oregon tax-free billing address (`700 SW 5th Ave, Portland, OR 97201`). `config.json`, `profiles/`, and `screenshots/` are excluded via `.gitignore` so your credentials and payment details remain local.

---

## Configuration & Usage

### 1. Billing & Network Configuration
1. Under **Billing & Network Configuration**, enter a valid Visa or Mastercard for `$0.00` trial authorization:
   - Card number (`16` digits)
   - Expiration (`MM` / `YY`)
   - Security code (`CVC`)
   - Cardholder name
2. Keep the default Oregon tax-free address (`700 SW 5th Ave`, `97201`, `OR`) so trial authorization remains strictly `$0.00` with `0%` sales tax.
3. Click **Save configuration**.
   - The checkout interceptor enforces a strict `$0.00` guard and aborts if a non-zero price appears on the checkout modal.

### 2. Account Import Formats
Paste one account per line under **Account Import**. Supported delimiters include `----`, `/`, `|`, comma, or whitespace:

```text
email@gmail.com----password----recovery@gmail.com
email@gmail.com----password----recovery@gmail.com----JBSWY3DPEHPK3PXP
email@gmail.com----password----JBSWY3DPEHPK3PXP
```

> **Note**: Base32 2FA TOTP secrets (`totp_secret`) should be pasted without spaces. Once imported, the **Account Queue** generates live 6-digit TOTP codes every 30 seconds.

### 3. Running & Resuming Steps
- Use **Run pending queue** in the top bar to process all non-completed accounts sequentially, or click **Run** on an individual account row.
- Use the step selector (`Step 01 · Full` through `Step 05 · Cancel`) to resume from a specific stage if a previous run was interrupted.
- Click **Browser** on any row to open that account's isolated Chrome profile window manually.
