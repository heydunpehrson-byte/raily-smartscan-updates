import json
import os
import platform
import threading
import uuid
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

import httpx


BRAIN_URL = "http://127.0.0.1:8765"

APP_DATA = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "RAILY"
APP_DATA.mkdir(parents=True, exist_ok=True)

WORKSTATION_FILE = APP_DATA / "workstation.json"


def load_workstation():
    if WORKSTATION_FILE.exists():
        try:
            return json.loads(WORKSTATION_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass

    data = {
        "id": str(uuid.uuid4()),
        "name": platform.node() or "RAILY-WORKSTATION",
    }

    WORKSTATION_FILE.write_text(
        json.dumps(data, indent=2),
        encoding="utf-8",
    )

    return data


WORKSTATION = load_workstation()


class RailyWorkstation(tk.Tk):
    def __init__(self):
        super().__init__()

        self.title("RAILY — Dispatch Brain")
        self.geometry("480x440")
        self.minsize(480, 440)

        self.configure(bg="#101820")

        self.session_token = None
        self.username = None
        self.role = None
        self.refresh_job = None
        self._view_generation = 0

        self.setup_styles()
        self.show_login()

    def setup_styles(self):
        style = ttk.Style(self)
        style.theme_use("clam")

        style.configure(
            "Dark.TFrame",
            background="#101820",
        )

        style.configure(
            "Dark.TLabel",
            background="#101820",
            foreground="#f3f6f8",
            font=("Segoe UI", 10),
        )

        style.configure(
            "Muted.TLabel",
            background="#101820",
            foreground="#9ca9b3",
            font=("Segoe UI", 9),
        )

        style.configure(
            "Title.TLabel",
            background="#101820",
            foreground="white",
            font=("Segoe UI Semibold", 20),
        )

        style.configure(
            "Card.TFrame",
            background="#18242d",
        )

        style.configure(
            "CardTitle.TLabel",
            background="#18242d",
            foreground="#9fb0bd",
            font=("Segoe UI", 9),
        )

        style.configure(
            "CardValue.TLabel",
            background="#18242d",
            foreground="white",
            font=("Segoe UI Semibold", 20),
        )

        style.configure(
            "Raily.TButton",
            font=("Segoe UI Semibold", 10),
            padding=9,
        )

    def clear(self):
        self._view_generation += 1
        if self.refresh_job:
            try:
                self.after_cancel(self.refresh_job)
            except Exception:
                pass
            self.refresh_job = None

        for child in self.winfo_children():
            child.destroy()

    def show_login(self):
        self.clear()

        self.geometry("480x440")
        self.resizable(False, False)

        outer = ttk.Frame(self, style="Dark.TFrame", padding=30)
        outer.pack(fill="both", expand=True)

        ttk.Label(
            outer,
            text="RAILY",
            style="Title.TLabel",
        ).pack()

        ttk.Label(
            outer,
            text="DISPATCH BRAIN",
            style="Muted.TLabel",
        ).pack(pady=(0, 22))

        self.status_var = tk.StringVar(value="Checking Dispatch Tower...")

        self.status_box = tk.Label(
            outer,
            textvariable=self.status_var,
            bg="#30201d",
            fg="#ff7b72",
            font=("Segoe UI Semibold", 10),
            padx=12,
            pady=9,
        )
        self.status_box.pack(fill="x", pady=(0, 20))

        ttk.Label(
            outer,
            text="Username",
            style="Dark.TLabel",
        ).pack(anchor="w")

        self.user_entry = ttk.Entry(
            outer,
            font=("Segoe UI", 11),
        )
        self.user_entry.pack(fill="x", pady=(4, 14))
        self.user_entry.insert(0, "Admin")

        ttk.Label(
            outer,
            text="Password",
            style="Dark.TLabel",
        ).pack(anchor="w")

        self.password_entry = ttk.Entry(
            outer,
            show="●",
            font=("Segoe UI", 11),
        )
        self.password_entry.pack(fill="x", pady=(4, 16))

        ttk.Label(
            outer,
            text=(
                f"Workstation: {WORKSTATION['name']}\n"
                f"Brain: {BRAIN_URL}"
            ),
            style="Muted.TLabel",
            justify="center",
        ).pack(pady=(0, 16))

        self.signin_button = ttk.Button(
            outer,
            text="Sign In",
            style="Raily.TButton",
            command=self.login,
        )
        self.signin_button.pack(fill="x")

        ttk.Button(
            outer,
            text="Exit",
            command=self.destroy,
        ).pack(fill="x", pady=(8, 0))

        self.password_entry.bind(
            "<Return>",
            lambda _event: self.login()
        )

        self.after(200, self.check_brain)
        self.after(400, self.password_entry.focus_set)

    def check_brain(self):
        generation = self._view_generation
        def worker():
            online = False

            try:
                response = httpx.get(
                    f"{BRAIN_URL}/health",
                    timeout=2.0,
                )
                online = response.status_code == 200
            except Exception:
                pass

            self.after(
                0,
                lambda: self.update_brain_status(online, generation)
            )

        threading.Thread(
            target=worker,
            daemon=True,
        ).start()

    def update_brain_status(self, online, generation=None):
        if generation is not None and generation != self._view_generation:
            return
        try:
            if not self.winfo_exists() or not self.status_box.winfo_exists():
                return
        except (tk.TclError, AttributeError):
            return

        if online:
            try:
                self.status_var.set("● DISPATCH TOWER CONNECTED")
            except tk.TclError:
                return
            try:
                self.status_box.configure(bg="#153222", fg="#5ce18b")
                self.signin_button.state(["!disabled"])
            except tk.TclError:
                return

        else:
            try:
                self.status_var.set("● DISPATCH TOWER OFFLINE")
            except tk.TclError:
                return
            try:
                self.status_box.configure(bg="#30201d", fg="#ff7b72")
                self.signin_button.state(["disabled"])
            except tk.TclError:
                return

        try:
            self.after(5000, self.check_brain)
        except tk.TclError:
            pass

    def login(self):
        username = self.user_entry.get().strip()
        password = self.password_entry.get()

        if not username or not password:
            messagebox.showwarning(
                "RAILY",
                "Enter your username and password.",
            )
            return

        try:
            response = httpx.post(
                f"{BRAIN_URL}/login",
                json={
                    "username": username,
                    "password": password,
                    "workstation_id": WORKSTATION["id"],
                    "workstation_name": WORKSTATION["name"],
                },
                timeout=5.0,
            )

            if response.status_code == 401:
                messagebox.showerror(
                    "RAILY",
                    "Invalid username or password.",
                )
                self.password_entry.delete(0, tk.END)
                return

            response.raise_for_status()

            data = response.json()

            self.session_token = data["token"]
            self.username = data["username"]
            self.role = data["role"]

            self.password_entry.delete(0, tk.END)

            self.show_dashboard()

        except Exception as exc:
            messagebox.showerror(
                "RAILY",
                f"Could not connect to Dispatch Brain.\n\n{exc}",
            )

    def auth_headers(self):
        return {
            "Authorization": f"Bearer {self.session_token}"
        }

    def show_dashboard(self):
        self.clear()

        self.geometry("1050x700")
        self.minsize(900, 620)
        self.resizable(True, True)

        header = ttk.Frame(
            self,
            style="Dark.TFrame",
            padding=(24, 18),
        )
        header.pack(fill="x")

        left = ttk.Frame(header, style="Dark.TFrame")
        left.pack(side="left")

        ttk.Label(
            left,
            text="RAILY NETWORK DISPATCH CENTER",
            style="Title.TLabel",
        ).pack(anchor="w")

        ttk.Label(
            left,
            text=f"{self.username} • {self.role} • {WORKSTATION['name']}",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(4, 0))

        self.signal_label = tk.Label(
            header,
            text="● CONNECTED",
            bg="#153222",
            fg="#5ce18b",
            font=("Segoe UI Semibold", 10),
            padx=14,
            pady=8,
        )
        self.signal_label.pack(side="right")

        content = ttk.Frame(
            self,
            style="Dark.TFrame",
            padding=(24, 0, 24, 20),
        )
        content.pack(fill="both", expand=True)

        cards = ttk.Frame(content, style="Dark.TFrame")
        cards.pack(fill="x", pady=(6, 18))

        self.card_vars = {}

        card_info = [
            ("incoming", "ARRIVING"),
            ("queued", "QUEUED"),
            ("processing", "PROCESSING"),
            ("review", "CONDUCTOR REVIEW"),
            ("duplicates", "DUPLICATE SIDING"),
            ("filed_today", "FILED TODAY"),
        ]

        for index, (key, title) in enumerate(card_info):
            card = ttk.Frame(
                cards,
                style="Card.TFrame",
                padding=14,
            )
            card.grid(
                row=0,
                column=index,
                padx=4,
                sticky="nsew",
            )

            cards.columnconfigure(index, weight=1)

            ttk.Label(
                card,
                text=title,
                style="CardTitle.TLabel",
            ).pack()

            var = tk.StringVar(value="0")
            self.card_vars[key] = var

            ttk.Label(
                card,
                textvariable=var,
                style="CardValue.TLabel",
            ).pack(pady=(4, 0))

            if key == "review" and self.role in {"Administrator", "Conductor / Reviewer"}:
                card.bind("<Button-1>", lambda _e: self.show_review_queue())

        middle = ttk.Frame(content, style="Dark.TFrame")
        middle.pack(fill="both", expand=True)

        middle.columnconfigure(0, weight=1)
        middle.columnconfigure(1, weight=1)
        middle.rowconfigure(0, weight=1)

        workstation_frame = ttk.Frame(
            middle,
            style="Card.TFrame",
            padding=12,
        )
        workstation_frame.grid(
            row=0,
            column=0,
            padx=(0, 8),
            sticky="nsew",
        )

        ttk.Label(
            workstation_frame,
            text="WORKSTATIONS / ACTIVE USERS",
            style="CardTitle.TLabel",
        ).pack(anchor="w", pady=(0, 8))

        self.session_tree = ttk.Treeview(
            workstation_frame,
            columns=("user", "role", "station", "seen"),
            show="headings",
            height=13,
        )

        for col, text, width in [
            ("user", "User", 110),
            ("role", "Role", 130),
            ("station", "Workstation", 170),
            ("seen", "Last Seen", 150),
        ]:
            self.session_tree.heading(col, text=text)
            self.session_tree.column(col, width=width)

        self.session_tree.pack(fill="both", expand=True)

        audit_frame = ttk.Frame(
            middle,
            style="Card.TFrame",
            padding=12,
        )
        audit_frame.grid(
            row=0,
            column=1,
            padx=(8, 0),
            sticky="nsew",
        )

        ttk.Label(
            audit_frame,
            text="RECENT DISPATCH ACTIVITY",
            style="CardTitle.TLabel",
        ).pack(anchor="w", pady=(0, 8))

        self.audit_tree = ttk.Treeview(
            audit_frame,
            columns=("user", "action", "details"),
            show="headings",
            height=13,
        )

        self.audit_tree.heading("user", text="User")
        self.audit_tree.heading("action", text="Action")
        self.audit_tree.heading("details", text="Details")

        self.audit_tree.column("user", width=100)
        self.audit_tree.column("action", width=145)
        self.audit_tree.column("details", width=280)

        self.audit_tree.pack(fill="both", expand=True)

        footer = ttk.Frame(
            content,
            style="Dark.TFrame",
        )
        footer.pack(fill="x", pady=(16, 0))

        self.brain_info = tk.StringVar(
            value="Loading Dispatch Brain..."
        )

        ttk.Label(
            footer,
            textvariable=self.brain_info,
            style="Muted.TLabel",
        ).pack(side="left")

        ttk.Button(
            footer,
            text="Submit Document",
            command=self.submit_document,
        ).pack(side="right", padx=(8, 0))

        ttk.Button(
            footer,
            text="Refresh",
            command=self.refresh_dashboard,
        ).pack(side="right", padx=(8, 0))

        if self.role == "Administrator":
            ttk.Button(
                footer,
                text="Users",
                command=self.show_users,
            ).pack(side="right", padx=(8, 0))

        if self.role in {"Administrator", "Conductor / Reviewer"}:
            ttk.Button(
                footer,
                text="Retry Job",
                command=self.retry_job,
            ).pack(side="right", padx=(8, 0))

        ttk.Button(
            footer,
            text="Sign Out",
            command=self.logout,
        ).pack(side="right")

        self.refresh_dashboard()

    def submit_document(self):
        path = filedialog.askopenfilename(filetypes=[("Documents", "*.pdf *.png *.jpg *.jpeg *.tif *.tiff"), ("All files", "*.*")])
        if not path or not self.session_token:
            return
        try:
            with open(path, "rb") as handle:
                response = httpx.post(f"{BRAIN_URL}/documents/upload", headers=self.auth_headers(), files={"file": (os.path.basename(path), handle)}, timeout=30.0)
            response.raise_for_status()
            messagebox.showinfo("RAILY", f"Document queued: {response.json().get('job_id')}")
            self.refresh_dashboard()
        except Exception as exc:
            messagebox.showerror("RAILY", f"Upload failed.\n\n{exc}")

    def retry_job(self):
        job_id = simpledialog.askinteger("RAILY Retry", "Existing Job ID to retry:", parent=self, minvalue=1)
        if job_id is None or not self.session_token:
            return
        if not messagebox.askyesno("Confirm retry", f"Requeue existing Job {job_id} for processing?", parent=self):
            return
        try:
            response = httpx.post(f"{BRAIN_URL}/jobs/{job_id}/retry", headers=self.auth_headers(), timeout=5.0)
            response.raise_for_status()
            messagebox.showinfo("RAILY", f"Job {job_id} is queued for retry.", parent=self)
            self.refresh_dashboard()
        except Exception as exc:
            messagebox.showerror("RAILY", f"Retry failed.\n\n{exc}", parent=self)

    def show_review_queue(self):
        try:
            response = httpx.get(f"{BRAIN_URL}/review", headers=self.auth_headers(), timeout=5.0)
            response.raise_for_status(); jobs = response.json()
            if not jobs:
                messagebox.showinfo("Conductor Review", "No jobs are awaiting review.", parent=self); return
            job = jobs[0]
            ocr = job.get("ocr") or {}
            window = tk.Toplevel(self); window.title(f"Conductor Review — Job {job['id']}"); window.geometry("760x620"); window.transient(self)
            ttk.Label(window, text=f"File: {job.get('original_name') or job.get('document_name')}\nReview reason: {job.get('review_reason') or job.get('error_message') or 'Low confidence / missing metadata'}", justify="left").pack(anchor="w", padx=12, pady=8)
            ttk.Label(window, text=f"OCR context:\n{(ocr.get('text') or '')[:1400]}", justify="left", wraplength=720).pack(anchor="w", padx=12)
            form = ttk.Frame(window); form.pack(fill="x", padx=12, pady=8)
            values = {"railroad": ocr.get("railroad") or "", "location": ocr.get("location") or "", "document_type": ocr.get("category") or "", "date": ocr.get("date") or "", "name": ocr.get("name") or ""}
            entries = {}
            for row, (key, label) in enumerate((("railroad","Railroad"),("location","Location"),("document_type","Document Type/Category"),("date","Document Date"),("name","Person/Name (optional)"))):
                ttk.Label(form, text=label).grid(row=row, column=0, sticky="w", pady=3); var=tk.StringVar(value=values[key]); entry=ttk.Entry(form, textvariable=var, width=58); entry.grid(row=row, column=1, sticky="ew", pady=3); entries[key]=(var, entry)
            proposed = ttk.Label(window, text=f"Proposed filename: {job.get('proposed_filename')}\nProposed destination: {job.get('proposed_destination')}", justify="left"); proposed.pack(anchor="w", padx=12, pady=6)
            approve = ttk.Button(window, text="Approve / File", state="disabled")
            approve.pack(side="right", padx=12, pady=10)
            teach = tk.BooleanVar(value=False)
            ttk.Checkbutton(window, text="Teach RAILY this confirmed correction", variable=teach).pack(anchor="w", padx=12)
            ttk.Button(window, text="Close", command=window.destroy).pack(side="right", pady=10)
            def validate(*_):
                missing = [k for k in ("railroad", "location", "document_type", "date") if not entries[k][0].get().strip()]
                for key, (_, entry) in entries.items(): entry.configure(style="Missing.TEntry" if key in missing else "TEntry")
                approve.state(["!disabled"] if not missing else ["disabled"])
            for var, _ in entries.values(): var.trace_add("write", validate)
            def submit():
                payload={k: v[0].get().strip() for k,v in entries.items()}; payload["teach"] = teach.get()
                if payload["teach"] and not messagebox.askyesno("Confirm learning", "Save this correction as a learned rule for similar documents?", parent=window): return
                if payload["teach"]: payload["pattern"] = simpledialog.askstring("Learned rule", "Pattern or layout cue to recognize:", parent=window) or ""
                if not messagebox.askyesno("Confirm approval", "Approve and file this existing job?", parent=window): return
                try:
                    result=httpx.post(f"{BRAIN_URL}/review/{job['id']}", headers=self.auth_headers(), json=payload, timeout=5.0); result.raise_for_status(); window.destroy(); messagebox.showinfo("Conductor Review", "Job approved and filed.", parent=self); self.refresh_dashboard()
                except Exception as exc: messagebox.showerror("Conductor Review", str(exc), parent=window)
            approve.configure(command=submit); validate()
        except Exception as exc:
            messagebox.showerror("Conductor Review", str(exc), parent=self)

    def refresh_dashboard(self):
        if not self.session_token:
            return

        def worker():
            try:
                response = httpx.get(
                    f"{BRAIN_URL}/dashboard",
                    headers=self.auth_headers(),
                    timeout=4.0,
                )

                response.raise_for_status()
                data = response.json()

                self.after(
                    0,
                    lambda: self.apply_dashboard(data)
                )

            except Exception:
                self.after(
                    0,
                    self.dashboard_offline
                )

        threading.Thread(
            target=worker,
            daemon=True,
        ).start()

    def apply_dashboard(self, data):
        yard = data.get("yard", {})

        for key, var in self.card_vars.items():
            var.set(str(yard.get(key, 0)))

        brain = data.get("brain", {})

        uptime = int(brain.get("uptime_seconds", 0))

        hours = uptime // 3600
        minutes = (uptime % 3600) // 60

        self.brain_info.set(
            f"Brain: {brain.get('name', '?')} • "
            f"v{brain.get('version', '?')} • "
            f"Uptime {hours}h {minutes}m • "
            f"Free Storage {brain.get('storage_free_gb', '?')} GB"
        )

        self.signal_label.configure(
            text="● ALL CLEAR",
            bg="#153222",
            fg="#5ce18b",
        )

        for item in self.session_tree.get_children():
            self.session_tree.delete(item)

        for session in data.get("active_sessions", []):
            self.session_tree.insert(
                "",
                "end",
                values=(
                    session.get("username", ""),
                    session.get("role", ""),
                    session.get("workstation_name", ""),
                    session.get("last_seen", "")[:19],
                ),
            )

        for item in self.audit_tree.get_children():
            self.audit_tree.delete(item)

        for event in data.get("recent_audit", []):
            self.audit_tree.insert(
                "",
                "end",
                values=(
                    event.get("username", ""),
                    event.get("action", ""),
                    event.get("details", ""),
                ),
            )

        self.refresh_job = self.after(
            5000,
            self.refresh_dashboard,
        )

    def dashboard_offline(self):
        self.signal_label.configure(
            text="● DISPATCH TOWER OFFLINE",
            bg="#30201d",
            fg="#ff7b72",
        )

        self.refresh_job = self.after(
            5000,
            self.refresh_dashboard,
        )

    def show_users(self):
        try:
            users = httpx.get(
                f"{BRAIN_URL}/admin/users",
                headers=self.auth_headers(),
                timeout=4.0,
            ).json()

            text = "\n".join(
                f"{u['username']} — {u['role']} — "
                f"{'Enabled' if u['enabled'] else 'Disabled'}"
                for u in users
            )

            messagebox.showinfo(
                "RAILY Users",
                text or "No users found.",
            )

        except Exception as exc:
            messagebox.showerror(
                "RAILY",
                str(exc),
            )

    def logout(self):
        if self.session_token:
            try:
                httpx.post(
                    f"{BRAIN_URL}/logout",
                    headers=self.auth_headers(),
                    timeout=3.0,
                )
            except Exception:
                pass

        self.session_token = None
        self.username = None
        self.role = None

        self.show_login()


if __name__ == "__main__":
    app = RailyWorkstation()
    app.mainloop()
