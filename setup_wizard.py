"""GUI setup wizard for a new machine — a technician tool, a thin front end
over setup.ps1 (same steps, same script, just paged and streamed into a
window instead of a terminal prompt).

Deliberately built on tkinter, not PySide6, even though every other GUI
tool in this project uses PySide6: this wizard has to run *before*
requirements.txt (which is what installs PySide6) exists on a fresh
machine, so it can only depend on the standard library. tkinter ships
with the standard python.org Windows installer. See DECISIONS.md's
"setup_wizard.py" entry.

Never point a student at this — same rule CLAUDE.md already states for
settings.py/preview.py. This is setup tooling, not the kiosk app.
"""

import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import ttk, scrolledtext

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
SETUP_PS1 = os.path.join(REPO_ROOT, "setup.ps1")
VENV_PYTHON = os.path.join(REPO_ROOT, ".venv", "Scripts", "python.exe")
SETTINGS_PY = os.path.join(REPO_ROOT, "settings.py")


class Page(ttk.Frame):
    """Base class for a wizard page. Subclasses override on_show()."""

    def on_show(self):
        pass


class WelcomePage(Page):
    def __init__(self, parent, wizard):
        super().__init__(parent)
        self.wizard = wizard

        ttk.Label(
            self, text="sidebyside Setup", font=("Segoe UI", 16, "bold")
        ).pack(anchor="w", pady=(0, 12))
        ttk.Label(
            self,
            wraplength=560,
            justify="left",
            text=(
                "This sets up the Python environment on this machine: "
                "creates a virtual environment, installs dependencies "
                "including the IDS peak Python bindings, and checks "
                "whether the IDS peak SDK runtime is installed. Every "
                "machine this wizard targets is assumed to be a real "
                "deployment running the prescribed slit lamp / BIO "
                "cameras, not a bare dev checkout.\n\n"
                "It does not install the IDS peak SDK itself (that's a "
                "separate, official installer with its own driver "
                "components — this wizard will tell you if and when you "
                "need to run it) and it does not assign camera roles "
                "(settings.py does that, afterward)."
            ),
        ).pack(anchor="w")

        nav = ttk.Frame(self)
        nav.pack(side="bottom", fill="x", pady=(24, 0))
        ttk.Button(nav, text="Next >", command=wizard.go_next).pack(side="right")


class RunPage(Page):
    def __init__(self, parent, wizard):
        super().__init__(parent)
        self.wizard = wizard
        self._started = False

        ttk.Label(
            self, text="Installing", font=("Segoe UI", 13, "bold")
        ).pack(anchor="w", pady=(0, 12))

        self.log = scrolledtext.ScrolledText(
            self, height=18, width=76, state="disabled",
            font=("Consolas", 9), background="#111", foreground="#ddd",
        )
        self.log.pack(fill="both", expand=True)

        self.status = ttk.Label(self, text="")
        self.status.pack(anchor="w", pady=(8, 0))

        nav = ttk.Frame(self)
        nav.pack(side="bottom", fill="x", pady=(12, 0))
        self.next_btn = ttk.Button(nav, text="Next >", command=wizard.go_next, state="disabled")
        self.next_btn.pack(side="right")
        self.retry_btn = ttk.Button(nav, text="Retry", command=self._start)
        self.back_btn = ttk.Button(nav, text="< Back", command=wizard.go_back)
        self.back_btn.pack(side="right", padx=(0, 8))

    def on_show(self):
        if not self._started:
            self._start()

    def _append(self, line):
        self.log.configure(state="normal")
        self.log.insert("end", line)
        self.log.see("end")
        self.log.configure(state="disabled")

    def _start(self):
        self._started = True
        self.retry_btn.pack_forget()
        self.next_btn.configure(state="disabled")
        self.status.configure(text="Running setup.ps1 ...", foreground="black")
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

        log_queue = queue.Queue()

        def worker():
            args = [
                "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-File", SETUP_PS1,
            ]
            try:
                proc = subprocess.Popen(
                    args, cwd=REPO_ROOT, stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT, text=True, bufsize=1,
                )
            except OSError as exc:
                log_queue.put(("line", f"Failed to launch setup.ps1: {exc}\n"))
                log_queue.put(("done", 1))
                return

            for line in proc.stdout:
                log_queue.put(("line", line))
            returncode = proc.wait()
            log_queue.put(("done", returncode))

        threading.Thread(target=worker, daemon=True).start()
        self.after(100, lambda: self._poll(log_queue))

    def _poll(self, log_queue):
        try:
            while True:
                kind, payload = log_queue.get_nowait()
                if kind == "line":
                    self._append(payload)
                elif kind == "done":
                    self._finish(payload)
                    return
        except queue.Empty:
            pass
        self.after(100, lambda: self._poll(log_queue))

    def _finish(self, returncode):
        if returncode == 0:
            self.wizard.setup_ok = True
            self.status.configure(text="Done.", foreground="dark green")
            self.next_btn.configure(state="normal")
        else:
            self.wizard.setup_ok = False
            self.status.configure(
                text=f"setup.ps1 exited with code {returncode} — see the log above.",
                foreground="dark red",
            )
            self.retry_btn.pack(side="right", padx=(0, 8))


class FinishPage(Page):
    def __init__(self, parent, wizard):
        super().__init__(parent)
        self.wizard = wizard

        self.title_label = ttk.Label(self, font=("Segoe UI", 16, "bold"))
        self.title_label.pack(anchor="w", pady=(0, 12))
        self.body_label = ttk.Label(self, wraplength=560, justify="left")
        self.body_label.pack(anchor="w")

        self.launch_btn = ttk.Button(
            self, text="Launch settings.py now", command=self._launch_settings
        )
        self.launch_btn.pack(anchor="w", pady=(16, 0))

        nav = ttk.Frame(self)
        nav.pack(side="bottom", fill="x", pady=(24, 0))
        ttk.Button(nav, text="Finish", command=wizard.destroy).pack(side="right")

    def on_show(self):
        if self.wizard.setup_ok:
            self.title_label.configure(text="Setup complete")
            self.body_label.configure(
                text=(
                    "The environment is ready. If the log mentioned the IDS "
                    "peak SDK runtime wasn't found, install it per SETUP.md "
                    "Sections 2-3 and re-run this wizard before relying on "
                    "the real cameras — the base app and SyntheticCamera "
                    "work either way.\n\n"
                    "Next: assign which physical camera fills each role."
                )
            )
            self.launch_btn.configure(state="normal")
        else:
            self.title_label.configure(text="Setup did not finish")
            self.body_label.configure(
                text="Go back and retry, or check the log for what failed."
            )
            self.launch_btn.configure(state="disabled")

    def _launch_settings(self):
        subprocess.Popen([VENV_PYTHON, SETTINGS_PY], cwd=REPO_ROOT)
        self.wizard.destroy()


class Wizard(tk.Tk):
    PAGES = (WelcomePage, RunPage, FinishPage)

    def __init__(self):
        super().__init__()
        self.title("sidebyside Setup")
        self.geometry("640x520")
        self.minsize(640, 520)

        self.setup_ok = False

        container = ttk.Frame(self, padding=16)
        container.pack(fill="both", expand=True)
        container.grid_rowconfigure(0, weight=1)
        container.grid_columnconfigure(0, weight=1)

        self.pages = {}
        for page_cls in self.PAGES:
            page = page_cls(container, self)
            self.pages[page_cls] = page
            page.grid(row=0, column=0, sticky="nsew")

        self.index = 0
        self._show_current()

    def _show_current(self):
        page_cls = self.PAGES[self.index]
        page = self.pages[page_cls]
        page.tkraise()
        page.on_show()

    def go_next(self):
        if self.index < len(self.PAGES) - 1:
            self.index += 1
            self._show_current()

    def go_back(self):
        if self.index > 0:
            self.index -= 1
            self._show_current()


if __name__ == "__main__":
    if not os.path.exists(SETUP_PS1):
        sys.exit(f"setup.ps1 not found at {SETUP_PS1} — run this from the repo.")
    Wizard().mainloop()
