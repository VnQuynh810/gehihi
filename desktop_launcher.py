"""
Google AI Pro (5 TB) 2 Năm Miễn Phí - Desktop Launcher (Giao Diện Cửa Sổ Desktop)
Sử dụng Tkinter chuẩn có sẵn trong Python, không yêu cầu cài thêm thư viện ngoài.
"""
import os
import sys
import time
import subprocess
import threading
import webbrowser
import tkinter as tk
from tkinter import ttk, messagebox

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SERVER_URL = "http://127.0.0.1:8765"
SCREENSHOTS_DIR = os.path.join(BASE_DIR, "screenshots")
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")


class DesktopApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Google AI Pro (5 TB) 2 Năm - Điều Khiển")
        self.root.geometry("540x480")
        self.root.minsize(500, 440)
        self.root.configure(bg="#0b1120")

        self.server_process = None
        self.server_thread = None

        self._build_ui()
        self.start_server()

        # Handle window close
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def _build_ui(self):
        # Header Box
        header_frame = tk.Frame(self.root, bg="#131d33", padx=16, pady=16)
        header_frame.pack(fill="x", padx=16, pady=(16, 10))

        title_lbl = tk.Label(
            header_frame,
            text="🚀 Google AI Pro (5 TB) 2 Năm",
            font=("Segoe UI", 16, "bold"),
            fg="#f8fafc",
            bg="#131d33",
        )
        title_lbl.pack(anchor="w")

        sub_lbl = tk.Label(
            header_frame,
            text="Bảng điều khiển tự động kích hoạt 24 tháng $0.00 & Huỷ gia hạn",
            font=("Segoe UI", 9),
            fg="#94a3b8",
            bg="#131d33",
        )
        sub_lbl.pack(anchor="w", pady=(3, 0))

        # Status Box
        status_frame = tk.Frame(self.root, bg="#0f172a", padx=14, pady=12, highlightthickness=1, highlightbackground="#1e293b")
        status_frame.pack(fill="x", padx=16, pady=6)

        self.status_dot = tk.Label(status_frame, text="●", font=("Segoe UI", 14), fg="#eab308", bg="#0f172a")
        self.status_dot.pack(side="left", padx=(0, 6))

        self.status_lbl = tk.Label(
            status_frame,
            text="Đang khởi động máy chủ Web UI (cổng 8765)...",
            font=("Segoe UI", 10, "bold"),
            fg="#cbd5e1",
            bg="#0f172a",
        )
        self.status_lbl.pack(side="left")

        # Main Actions Frame
        actions_frame = tk.Frame(self.root, bg="#0b1120", pady=10)
        actions_frame.pack(fill="both", expand=True, padx=16)

        btn_open = tk.Button(
            actions_frame,
            text="🌐 Mở Giao Diện Web Console (Khuyên Dùng)",
            font=("Segoe UI", 11, "bold"),
            bg="#2563eb",
            fg="#ffffff",
            activebackground="#1d4ed8",
            activeforeground="#ffffff",
            relief="flat",
            padx=12,
            pady=10,
            cursor="hand2",
            command=self.open_browser,
        )
        btn_open.pack(fill="x", pady=5)

        # Secondary Button Grid
        sec_grid = tk.Frame(actions_frame, bg="#0b1120")
        sec_grid.pack(fill="x", pady=6)

        btn_restart = tk.Button(
            sec_grid,
            text="🔄 Khởi Động Lại Server",
            font=("Segoe UI", 9, "bold"),
            bg="#1e293b",
            fg="#cbd5e1",
            activebackground="#334155",
            activeforeground="#ffffff",
            relief="flat",
            padx=10,
            pady=8,
            cursor="hand2",
            command=self.restart_server,
        )
        btn_restart.pack(side="left", fill="x", expand=True, padx=(0, 4))

        btn_screen = tk.Button(
            sec_grid,
            text="📁 Thư Mục Ảnh Chụp",
            font=("Segoe UI", 9, "bold"),
            bg="#1e293b",
            fg="#cbd5e1",
            activebackground="#334155",
            activeforeground="#ffffff",
            relief="flat",
            padx=10,
            pady=8,
            cursor="hand2",
            command=self.open_screenshots,
        )
        btn_screen.pack(side="right", fill="x", expand=True, padx=(4, 0))

        sec_grid2 = tk.Frame(actions_frame, bg="#0b1120")
        sec_grid2.pack(fill="x", pady=4)

        btn_cfg = tk.Button(
            sec_grid2,
            text="⚙️ Mở File Cấu Hình (config.json)",
            font=("Segoe UI", 9),
            bg="#1e293b",
            fg="#cbd5e1",
            activebackground="#334155",
            activeforeground="#ffffff",
            relief="flat",
            padx=10,
            pady=7,
            cursor="hand2",
            command=self.open_config,
        )
        btn_cfg.pack(side="left", fill="x", expand=True, padx=(0, 4))

        btn_guide = tk.Button(
            sec_grid2,
            text="📖 Hướng Dẫn Sử Dụng",
            font=("Segoe UI", 9),
            bg="#1e293b",
            fg="#cbd5e1",
            activebackground="#334155",
            activeforeground="#ffffff",
            relief="flat",
            padx=10,
            pady=7,
            cursor="hand2",
            command=self.open_guide,
        )
        btn_guide.pack(side="right", fill="x", expand=True, padx=(4, 0))

        # Bottom Tip
        tip_frame = tk.Frame(self.root, bg="#0b1120", pady=8)
        tip_frame.pack(fill="x", side="bottom", padx=16, pady=10)

        tip_lbl = tk.Label(
            tip_frame,
            text="💡 Gợi ý: Giao diện web hỗ trợ đầy đủ tiếng Việt, xem ảnh xác thực, copy 2FA,\nnhập hàng loạt tài khoản và theo dõi trực tiếp từng bước.",
            font=("Segoe UI", 8),
            fg="#64748b",
            bg="#0b1120",
            justify="center",
        )
        tip_lbl.pack()

    def start_server(self):
        if self.server_process:
            return

        def _run():
            python_exe = sys.executable
            app_script = os.path.join(BASE_DIR, "app.py")
            self.server_process = subprocess.Popen(
                [python_exe, app_script],
                cwd=BASE_DIR,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            time.sleep(1.2)
            self.root.after(0, self._on_server_ready)

            # Wait process
            self.server_process.wait()
            self.root.after(0, self._on_server_stopped)

        self.server_thread = threading.Thread(target=_run, daemon=True)
        self.server_thread.start()

    def _on_server_ready(self):
        self.status_dot.configure(text="●", fg="#10b981")
        self.status_lbl.configure(text="Máy chủ Web UI đang chạy tại http://127.0.0.1:8765", fg="#34d399")

    def _on_server_stopped(self):
        self.status_dot.configure(text="●", fg="#ef4444")
        self.status_lbl.configure(text="Máy chủ đã dừng", fg="#f87171")
        self.server_process = None

    def restart_server(self):
        if self.server_process:
            try:
                self.server_process.terminate()
            except Exception:
                pass
            self.server_process = None
        self.status_dot.configure(text="●", fg="#eab308")
        self.status_lbl.configure(text="Đang khởi động lại máy chủ...", fg="#cbd5e1")
        self.root.after(1000, self.start_server)

    def open_browser(self):
        webbrowser.open(SERVER_URL)

    def open_screenshots(self):
        os.makedirs(SCREENSHOTS_DIR, exist_ok=True)
        if os.name == "nt":
            os.startfile(SCREENSHOTS_DIR)

    def open_config(self):
        if os.path.exists(CONFIG_PATH) and os.name == "nt":
            os.startfile(CONFIG_PATH)

    def open_guide(self):
        guide_path = os.path.join(BASE_DIR, "HDSD_TIENG_VIET.md")
        if os.path.exists(guide_path) and os.name == "nt":
            os.startfile(guide_path)
        else:
            self.open_browser()

    def on_close(self):
        if self.server_process:
            try:
                self.server_process.terminate()
            except Exception:
                pass
        self.root.destroy()


def main():
    root = tk.Tk()
    app = DesktopApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()

