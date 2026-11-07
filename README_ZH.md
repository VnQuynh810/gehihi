# Google AI Pro (5 TB) 24个月零成本自动化控制台

[English](README.md) | **简体中文**

基于 Playwright CDP 与 `Ngbm` 结账 RPC 拦截的本地批量自动化控制台，用于按序执行 Google One AI Pro (5 TB) 24 个月免费试用叠加与 Google Play 自动取消续订。

> [!CAUTION]
> **免责声明与账号风险提示（请仔细阅读）**
>
> 本项目仅供技术交流与安全研究使用，一切操作风险由使用者自行承担。通过自动化脚本操作 Google 登录流、Google Play 账单接口以及叠加促销试用令牌违反 Google 服务条款（Terms of Service）及 Google One 促销政策。
>
> 使用本工具可能导致包括但不限于以下后果：
> - 促销订阅权益或云端硬盘空间被随时收回
> - Google Payments 付款资料被风控锁死或要求额外身份验证
> - Google 账号被临时限制或永久封禁
>
> 仓库维护者不对任何账号封禁、权益收回或账单异常承担任何责任。

---

## 五步流水线说明

| 阶段 | 执行内容 | 累计时长 | 扣费保护 |
| :--- | :--- | :--- | :--- |
| **Step 01** | Google 登录、辅助邮箱验证及 2FA TOTP 动态码自动填写 | 登录验证 | — |
| **Step 02** | 首层 12 个月 5 TB 试用 + 内联免税州（OR `97201`）绑卡 | `0M → 12M` | 严格校验 `US$0.00` |
| **Step 03** | 二层 6 个月促销包叠加（`S25 +6M`，`ear8` 模式 `5` 追加） | `12M → 18M` | 严格校验 `US$0.00` |
| **Step 04** | 三层 6 个月促销包封顶（`Pixel +6M`，`ear8` 模式 `5` 封顶） | `18M → 24M` | 严格校验 `US$0.00` |
| **Step 05** | 前往 Google Play 取消自动续订并截取最终核验截图 | `24M 锁定` | 关闭到期自动扣款 |

---

## 运行环境要求

- **操作系统**：Windows 10 / 11
- **浏览器**：已安装 Google Chrome（默认路径即可）
- **Python 环境**：Python 3.10 或更高版本（安装时勾选 `Add Python to PATH`）
- **网络环境**：本机开启美区（US）网络代理，或在控制台配置出口代理模板

---

## 快速启动

1. **首次运行（自动安装依赖并启动）**：
   双击运行 `setup.bat`（或手动执行 `pip install -r requirements.txt` 后运行 `python app.py`）。
2. **日常启动**：
   双击运行 `start.bat`。
3. **访问控制台**：
   浏览器打开 `http://127.0.0.1:8765`。

> [!NOTE]
> 首次启动时，程序会自动在本地生成 `config.json` 并填入美国俄勒冈免税州默认地址（`700 SW 5th Ave, Portland, OR 97201`）。`config.json`、`profiles/` 与 `screenshots/` 已加入 `.gitignore`，不会随 Git 提交泄露本地数据。

---

## 配置与使用说明

### 1. 填写支付卡与网络配置
1. 在左侧 **Billing & Network Configuration** 填入用于 `$0.00` 试用验证的 Visa 或 Mastercard：
   - 卡号（16 位数字）
   - 有效期（`MM` / `YY`）
   - 安全码（3 位 `CVC`）
   - 持卡人英文姓名
2. 保持默认免税邮编 `97201`（`OR`），点击 **Save configuration** 保存。
3. 脚本内置 `$0.00` 结账校验保护，仅在弹窗明确显示 `US$0.00` 时才会触发订阅。

### 2. 批量导入账号
在右侧 **Account Import** 每行粘贴一个账号，支持 `----`、`/`、`|`、逗号或空格分隔：

```text
email@gmail.com----password----recovery@gmail.com
email@gmail.com----password----recovery@gmail.com----JBSWY3DPEHPK3PXP
email@gmail.com----password----JBSWY3DPEHPK3PXP
```

> **提示**：2FA 密钥（`totp_secret`）请勿包含空格。导入后列表会每 30 秒自动刷新当前 6 位验证码。

### 3. 执行与单步重跑
- 点击顶部 **Run pending queue** 可按队列顺序批量执行所有未完成账号，或点击单行右侧 **Run** 单独执行。
- 可通过每行的步骤下拉框（`Step 01 · Full` 至 `Step 05 · Cancel`）从指定阶段继续执行。
- 点击单行 **Browser** 可手动打开该账号对应的独立 Chrome 浏览器窗口。

