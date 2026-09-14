import json
import os
import platform
import uuid
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk

import httpx


BRAIN_URL = os.environ.get("RAILY_BRAIN_URL", "http://127.0.0.1:8765")

APP_DATA = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "RAILY"
APP_DATA.mkdir(parents=True, exist_ok=True)

WORKSTATION_FILE = APP_DATA / "workstation.json"


def load_workstation():
    if WORKSTATION_FILE.exists():
        try:
            return json.loads(WORKSTATION_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass

    info = {
        "id": str(uuid.uuid4()),
        "name": platform.node() or "RAILY-WORKSTATION",
    }

    WORKSTATION_FILE.write_text(
        json.dumps(info, indent=2),
        encoding="utf-8",
    )

    return info


WORKSTATION = load_workstation()


class RailyLogin(tk.Tk):
    def __init__(self):
        super().__init__()

        self.title("RAILY — Dispatch Brain Sign In")
        self._set_login_geometry()
        self.resizable(False, False)

        self.session_token = None
        self.username = None
        self.role = None

        self.configure(bg="#111820")

        self._build_ui()
        self.after(300, self.check_brain)

    def _build_ui(self):
        style = ttk.Style(self)
        style.theme_use("clam")

        style.configure(
            "RAILY.TFrame",
            background="#111820",
        )

        style.configure(
            "RAILY.TLabel",
            background="#111820",
            foreground="#f1f5f9",
            font=("Segoe UI", 10),
        )

        style.configure(
            "Title.TLabel",
            background="#111820",
            foreground="#ffffff",
            font=("Segoe UI Semibold", 20),
        )

        style.configure(
            "Sub.TLabel",
            background="#111820",
            foreground="#9fb1c3",
            font=("Segoe UI", 10),
        )

        style.configure(
            "RAILY.TButton",
            font=("Segoe UI Semibold", 10),
            padding=9,
        )

        outer = ttk.Frame(self, style="RAILY.TFrame", padding=28)
        outer.pack(fill="both", expand=True)

        ttk.Label(
            outer,
            text="RAILY",
            style="Title.TLabel",
        ).pack()

        ttk.Label(
            outer,
            text="DISPATCH BRAIN",
            style="Sub.TLabel",
        ).pack(pady=(0, 20))

        self.status_var = tk.StringVar(value="Checking Dispatch Brain...")
        self.status_label = tk.Label(
            outer,
            textvariable=self.status_var,
            bg="#17212b",
            fg="#f7c948",
            font=("Segoe UI Semibold", 10),
            padx=12,
            pady=8,
        )
        self.status_label.pack(fill="x", pady=(0, 22))

        ttk.Label(
            outer,
            text="Username",
            style="RAILY.TLabel",
        ).pack(anchor="w")

        self.username_entry = ttk.Entry(
            outer,
            font=("Segoe UI", 11),
        )
        self.username_entry.pack(fill="x", pady=(5, 14))
        self.username_entry.insert(0, "Admin")

        ttk.Label(
            outer,
            text="Password",
            style="RAILY.TLabel",
        ).pack(anchor="w")

        self.password_entry = ttk.Entry(
            outer,
            show="●",
            font=("Segoe UI", 11),
        )
        self.password_entry.pack(fill="x", pady=(5, 18))

        workstation_text = (
            f"Workstation: {WORKSTATION['name']}\n"
            f"Brain: {BRAIN_URL}"
        )

        ttk.Label(
            outer,
            text=workstation_text,
            style="Sub.TLabel",
            justify="center",
        ).pack(pady=(0, 18))

        self.login_button = ttk.Button(
            outer,
            text="Sign In",
            style="RAILY.TButton",
            command=self.login,
        )
        self.login_button.pack(fill="x")

        ttk.Button(
            outer,
            text="Cancel",
            command=self.destroy,
        ).pack(fill="x", pady=(8, 0))

        self.password_entry.bind("<Return>", lambda _e: self.login())

        self.after(500, self.password_entry.focus_set)

    def _set_login_geometry(self):
        try:
            scale = float(self.tk.call("tk", "scaling"))
        except Exception:
            scale = 1.0
        width = max(500, min(620, int(500 * max(1.0, scale / 1.25))))
        height = max(540, min(680, int(560 * max(1.0, scale / 1.25))))
        self.update_idletasks()
        x = max(0, (self.winfo_screenwidth() - width) // 2)
        y = max(0, (self.winfo_screenheight() - height) // 2)
        self.geometry(f"{width}x{height}+{x}+{y}")

    def set_brain_state(self, online: bool):
        if online:
            self.status_var.set("● DISPATCH TOWER CONNECTED")
            self.status_label.configure(
                fg="#55d187",
                bg="#15291e",
            )
            self.login_button.state(["!disabled"])
        else:
            self.status_var.set("● DISPATCH TOWER OFFLINE")
            self.status_label.configure(
                fg="#ff6b6b",
                bg="#301919",
            )
            self.login_button.state(["disabled"])

    def check_brain(self):
        try:
            with httpx.Client(timeout=2.5) as client:
                response = client.get(f"{BRAIN_URL}/health")
                response.raise_for_status()

            self.set_brain_state(True)

        except Exception:
            self.set_brain_state(False)

        self.after(5000, self.check_brain)

    def login(self):
        username = self.username_entry.get().strip()
        password = self.password_entry.get()

        if not username or not password:
            messagebox.showwarning(
                "RAILY",
                "Enter your username and password.",
            )
            return

        self.login_button.state(["disabled"])
        self.status_var.set("Authenticating...")

        try:
            payload = {
                "username": username,
                "password": password,
                "workstation_id": WORKSTATION["id"],
                "workstation_name": WORKSTATION["name"],
            }

            with httpx.Client(timeout=5.0) as client:
                response = client.post(
                    f"{BRAIN_URL}/login",
                    json=payload,
                )

            if response.status_code == 401:
                self.set_brain_state(True)
                messagebox.showerror(
                    "RAILY Sign In",
                    "Invalid username or password.",
                )
                self.password_entry.delete(0, tk.END)
                self.password_entry.focus_set()
                return

            response.raise_for_status()

            data = response.json()

            self.session_token = data["token"]
            self.username = data["username"]
            self.role = data["role"]

            self.password_entry.delete(0, tk.END)

            self.show_dispatch_home()

        except Exception as exc:
            self.set_brain_state(False)
            messagebox.showerror(
                "RAILY",
                f"Could not connect to the Dispatch Brain.\n\n{exc}",
            )

        finally:
            if self.winfo_exists():
                self.login_button.state(["!disabled"])

    def show_dispatch_home(self):
        for child in self.winfo_children():
            child.destroy()

        self.geometry("520x390")

        frame = ttk.Frame(self, style="RAILY.TFrame", padding=30)
        frame.pack(fill="both", expand=True)

        ttk.Label(
            frame,
            text="RAILY DISPATCH CENTER",
            style="Title.TLabel",
        ).pack(pady=(15, 5))

        tk.Label(
            frame,
            text="● ALL CLEAR",
            bg="#15291e",
            fg="#55d187",
            font=("Segoe UI Semibold", 11),
            padx=12,
            pady=8,
        ).pack(fill="x", pady=(10, 25))

        ttk.Label(
            frame,
            text=f"Signed in as: {self.username}",
            style="RAILY.TLabel",
        ).pack(pady=4)

        ttk.Label(
            frame,
            text=f"Role: {self.role}",
            style="RAILY.TLabel",
        ).pack(pady=4)

        ttk.Label(
            frame,
            text=f"Workstation: {WORKSTATION['name']}",
            style="Sub.TLabel",
        ).pack(pady=4)

        ttk.Label(
            frame,
            text="Dispatch Brain: Connected",
            style="Sub.TLabel",
        ).pack(pady=(4, 25))

        ttk.Button(
            frame,
            text="Enter Dispatch Center",
            style="RAILY.TButton",
            command=lambda: messagebox.showinfo(
                "RAILY",
                "Dispatch Center dashboard is the next module.",
            ),
        ).pack(fill="x")

        ttk.Button(
            frame,
            text="Sign Out",
            command=self.logout,
        ).pack(fill="x", pady=(8, 0))

    def logout(self):
        if self.session_token:
            try:
                with httpx.Client(timeout=3.0) as client:
                    client.post(
                        f"{BRAIN_URL}/logout",
                        headers={
                            "Authorization": f"Bearer {self.session_token}"
                        },
                    )
            except Exception:
                pass

        self.session_token = None
        self.destroy()


if __name__ == "__main__":
    app = RailyLogin()
    app.mainloop()
