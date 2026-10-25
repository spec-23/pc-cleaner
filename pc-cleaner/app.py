"""app.py - Drive Cleaner, a small Tkinter utility for Windows.

Tab 1 cleans known junk locations (always previewed first, sent to the
Recycle Bin when possible).
Tab 2 lists installed applications so you can uninstall the ones you never use.
"""

from __future__ import annotations

import ctypes
import os
import queue
import sys
import threading
import tkinter as tk
import tkinter.font as tkfont
from tkinter import messagebox, ttk

import apps as appreg
import scanner

# --------------------------------------------------------------------------- #
# palette
# --------------------------------------------------------------------------- #

BG = "#f3f4f6"
CARD = "#ffffff"
TEXT = "#16181d"
MUTED = "#6b7280"
BORDER = "#e2e4e9"
ACCENT = "#2563eb"
ACCENT_DARK = "#1d4ed8"
DANGER = "#dc2626"
DANGER_DARK = "#b91c1c"
WARN = "#b45309"
SOFT_BLUE = "#eff4ff"

CHECK_ON = "☑"
CHECK_OFF = "☐"


def _style() -> None:
    style = ttk.Style()
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    style.configure(".", background=BG, foreground=TEXT, font=("Segoe UI", 9))
    style.configure("TFrame", background=BG)
    style.configure("Card.TFrame", background=CARD)
    style.configure("TLabel", background=BG, foreground=TEXT)
    style.configure("Card.TLabel", background=CARD, foreground=TEXT)
    style.configure("Title.TLabel", background=BG, foreground=TEXT, font=("Segoe UI Semibold", 17))
    style.configure("Sub.TLabel", background=BG, foreground=MUTED, font=("Segoe UI", 9))
    style.configure("CardSub.TLabel", background=CARD, foreground=MUTED, font=("Segoe UI", 8))
    style.configure("CardTitle.TLabel", background=CARD, foreground=TEXT, font=("Segoe UI Semibold", 10))
    style.configure("Big.TLabel", background=CARD, foreground=TEXT, font=("Segoe UI Semibold", 13))
    style.configure("Muted.TLabel", background=BG, foreground=MUTED)
    style.configure("Warn.TLabel", background=BG, foreground=WARN)
    style.configure("Danger.TLabel", background=BG, foreground=DANGER)

    style.configure("TNotebook", background=BG, borderwidth=0, tabmargins=(0, 6, 0, 0))
    style.configure(
        "TNotebook.Tab",
        background=BG,
        foreground=MUTED,
        padding=(18, 8),
        borderwidth=0,
        font=("Segoe UI", 10),
    )
    style.map(
        "TNotebook.Tab",
        background=[("selected", CARD), ("active", BG)],
        foreground=[("selected", ACCENT), ("active", TEXT)],
        expand=[("selected", (0, 0, 0, 0))],
    )

    style.configure("TButton", padding=(12, 6), borderwidth=1, focusthickness=0, background=CARD)
    style.map("TButton", background=[("active", "#f0f1f4"), ("disabled", "#f7f7f9")],
              foreground=[("disabled", "#a1a1aa")])

    style.configure("Accent.TButton", background=ACCENT, foreground="#ffffff", borderwidth=0,
                    padding=(16, 7), font=("Segoe UI", 9, "bold"))
    style.map("Accent.TButton", background=[("active", ACCENT_DARK), ("disabled", "#9dbcf7")],
              foreground=[("disabled", "#ffffff")])

    style.configure("Danger.TButton", background=DANGER, foreground="#ffffff", borderwidth=0,
                    padding=(16, 7), font=("Segoe UI", 9, "bold"))
    style.map("Danger.TButton", background=[("active", DANGER_DARK), ("disabled", "#f0a6a6")],
              foreground=[("disabled", "#ffffff")])

    style.configure("Treeview", background=CARD, fieldbackground=CARD, foreground=TEXT,
                    borderwidth=0, rowheight=28, font=("Segoe UI", 9))
    style.configure("Treeview.Heading", background="#f7f8fa", foreground=MUTED, relief="flat",
                    borderwidth=0, padding=(8, 7), font=("Segoe UI Semibold", 9))
    style.map("Treeview.Heading", background=[("active", "#eef0f4")])
    style.map("Treeview", background=[("selected", SOFT_BLUE)], foreground=[("selected", TEXT)])
    style.layout("Treeview", [("Treeview.treearea", {"sticky": "nswe"})])

    style.configure("TEntry", fieldbackground=CARD, borderwidth=1, padding=(8, 6))
    style.configure("TCombobox", fieldbackground=CARD, padding=(8, 5))
    style.configure("TScrollbar", background="#d7dae0", troughcolor=CARD, borderwidth=0,
                    arrowsize=13)
    style.map("TScrollbar", background=[("active", "#b9bec8")])
    style.configure("TCheckbutton", background=BG)
    style.configure("Card.TCheckbutton", background=CARD)
    style.configure("Horizontal.TProgressbar", background=ACCENT, troughcolor="#e6e8ed",
                    borderwidth=0, thickness=6)


def _card(master, **kw) -> tk.Frame:
    return tk.Frame(master, bg=CARD, highlightbackground=BORDER, highlightthickness=1, **kw)


# --------------------------------------------------------------------------- #
# drive card
# --------------------------------------------------------------------------- #

class DriveCard(tk.Frame):
    def __init__(self, master, info: dict):
        super().__init__(master, bg=CARD, highlightbackground=BORDER, highlightthickness=1,
                         padx=14, pady=10)
        total = max(info["total"], 1)
        used = info["used"]
        free = info["free"]
        pct = used / total

        head = tk.Frame(self, bg=CARD)
        head.pack(fill="x")
        tk.Label(head, text=f"{info['letter']}:", bg=CARD, fg=TEXT,
                 font=("Segoe UI Semibold", 11)).pack(side="left")
        tk.Label(head, text=("removable" if info["removable"] else "local disk"), bg=CARD, fg=MUTED,
                 font=("Segoe UI", 8)).pack(side="left", padx=(6, 0))

        self.canvas = tk.Canvas(self, height=7, bg=CARD, highlightthickness=0)
        self.canvas.pack(fill="x", pady=(8, 6))
        self.canvas.bind("<Configure>", lambda _e: self._draw(pct))
        self._pct = pct

        tk.Label(self, text=f"{scanner.human_size(free)} free of {scanner.human_size(total)}",
                 bg=CARD, fg=MUTED, font=("Segoe UI", 8)).pack(anchor="w")

    def _draw(self, pct: float) -> None:
        width = self.canvas.winfo_width()
        if width <= 1:
            return
        self.canvas.delete("all")
        self.canvas.create_rectangle(0, 0, width, 7, fill="#e9ebef", outline="")
        color = ACCENT if pct < 0.8 else ("#f59e0b" if pct < 0.92 else DANGER)
        filled = max(2, int(width * min(pct, 1.0)))
        self.canvas.create_rectangle(0, 0, filled, 7, fill=color, outline="")


# --------------------------------------------------------------------------- #
# cleanup tab
# --------------------------------------------------------------------------- #

class CleanupTab(ttk.Frame):
    def __init__(self, master, app: "DriveCleaner"):
        super().__init__(master, padding=(0, 12))
        self.app = app
        self.tree = None
        self.log_box = None
        self.summary = None
        self.progress = None
        self.scan_btn = None
        self.clean_btn = None
        self._build()

    def _build(self) -> None:
        bar = ttk.Frame(self)
        bar.pack(fill="x", pady=(0, 10))

        self.scan_btn = ttk.Button(bar, text="Scan drives", style="Accent.TButton",
                                   command=self.app.start_scan)
        self.scan_btn.pack(side="left")

        self.clean_btn = ttk.Button(bar, text="Clean selected", command=self.clean_selected)
        self.clean_btn.pack(side="left", padx=(8, 0))

        ttk.Button(bar, text="Select all", command=lambda: self.set_all(True)).pack(side="left", padx=(16, 0))
        ttk.Button(bar, text="Select none", command=lambda: self.set_all(False)).pack(side="left", padx=(6, 0))

        self.progress = ttk.Progressbar(bar, mode="indeterminate", length=140,
                                        style="Horizontal.TProgressbar")
        self.progress.pack(side="right", padx=(10, 0))
        self.summary = ttk.Label(bar, text="Not scanned yet", style="Muted.TLabel")
        self.summary.pack(side="right")

        card = _card(self)
        card.pack(fill="both", expand=True)

        inner = tk.Frame(card, bg=CARD)
        inner.pack(fill="both", expand=True, padx=1, pady=1)

        columns = ("files", "size", "note")
        self.tree = ttk.Treeview(inner, columns=columns, show="tree headings", selectmode="browse")
        self.tree.heading("#0", text="Category")
        self.tree.heading("files", text="Files")
        self.tree.heading("size", text="Size")
        self.tree.heading("note", text="What gets removed")
        self.tree.column("#0", width=300, minwidth=220, stretch=False)
        self.tree.column("files", width=80, anchor="e", stretch=False)
        self.tree.column("size", width=90, anchor="e", stretch=False)
        self.tree.column("note", width=460, anchor="w")
        self.tree.tag_configure("caution", foreground=WARN)
        self.tree.tag_configure("permanent", foreground=DANGER)
        self.tree.tag_configure("admin", foreground=MUTED)

        scroll = ttk.Scrollbar(inner, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        inner.rowconfigure(0, weight=1)
        inner.columnconfigure(0, weight=1)

        self.tree.bind("<Button-1>", self._on_click)
        self.tree.bind("<space>", self._on_space)

        log_head = ttk.Frame(self)
        log_head.pack(fill="x", pady=(12, 4))
        ttk.Label(log_head, text="Activity", style="Muted.TLabel").pack(side="left")

        log_card = _card(self)
        log_card.pack(fill="x")
        self.log_box = tk.Text(log_card, height=6, bg=CARD, fg=TEXT, relief="flat",
                               font=("Consolas", 8), padx=10, pady=8, wrap="none",
                               insertbackground=TEXT, state="disabled")
        self.log_box.pack(fill="x")

        self.reload()

    # -- rendering ---------------------------------------------------------- #

    def reload(self) -> None:
        self.tree.delete(*self.tree.get_children())
        for cat in self.app.categories:
            result = self.app.results.get(cat.key)
            checked = cat.key in self.app.checked
            size = result.size_text if result else "—"
            files = f"{result.files:,}" if result else "—"
            note = cat.description
            if cat.needs_admin and not scanner.is_admin():
                note += "  (needs admin)"
            if cat.permanent:
                note += "  · permanent"
            tag = "permanent" if cat.permanent else ("caution" if cat.caution else "")
            if tag == "" and cat.needs_admin and not scanner.is_admin():
                tag = "admin"
            self.tree.insert(
                "", "end",
                iid=cat.key,
                text=f"{CHECK_ON if checked else CHECK_OFF}  {cat.label}",
                values=(files, size, note),
                tags=(tag,) if tag else (),
            )
        self._update_summary()

    def _update_summary(self) -> None:
        total = sum(self.app.results[c.key].bytes for c in self.app.categories if c.key in self.app.checked
                    and c.key in self.app.results)
        selectable = sum(1 for c in self.app.categories if c.key in self.app.checked)
        if not self.app.results:
            self.summary.configure(text="Not scanned yet")
        else:
            self.summary.configure(text=f"{scanner.human_size(total)} selected · {selectable} categories")

    def set_all(self, value: bool) -> None:
        self.app.checked = {c.key for c in self.app.categories} if value else set()
        self.reload()

    def _toggle(self, key: str) -> None:
        if key in self.app.checked:
            self.app.checked.discard(key)
        else:
            self.app.checked.add(key)
        self.reload()

    def _on_click(self, event) -> None:
        if self.tree.identify_column(event.x) != "#0":
            return
        row = self.tree.identify_row(event.y)
        if row:
            self.after_idle(lambda: self._toggle(row))

    def _on_space(self, _event) -> None:
        for row in self.tree.selection():
            self._toggle(row)

    def log(self, text: str) -> None:
        self.log_box.configure(state="normal")
        self.log_box.insert("end", text.rstrip() + "\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def clear_log(self) -> None:
        self.log_box.configure(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.configure(state="disabled")

    def set_busy(self, busy: bool, label: str = "") -> None:
        state = "disabled" if busy else "normal"
        self.scan_btn.configure(state=state)
        self.clean_btn.configure(state=state)
        if busy:
            self.progress.start(14)
            if label:
                self.summary.configure(text=label)
        else:
            self.progress.stop()

    # -- actions ------------------------------------------------------------ #

    def clean_selected(self) -> None:
        chosen = [c for c in self.app.categories if c.key in self.app.checked]
        if not chosen:
            messagebox.showinfo("Nothing selected", "Tick at least one category to clean.", parent=self)
            return

        total = sum(self.app.results[c.key].bytes for c in chosen if c.key in self.app.results)
        lines = [f"• {c.label} — {self.app.results[c.key].size_text}"
                 for c in chosen if c.key in self.app.results]
        permanent = [c.label for c in chosen if c.permanent]
        missing = [c.label for c in chosen if c.key not in self.app.results]

        message = (
            f"About to permanently remove {scanner.human_size(total)} "
            f"from {len(chosen)} locations.\n\n"
            + "\n".join(lines)
            + "\n\nEverything else is moved to the Recycle Bin, so it can be restored."
        )
        if permanent:
            message += "\n\n⚠ " + ", ".join(permanent) + " CANNOT be restored."
        if missing:
            message += "\n\nNot scanned yet (will be skipped): " + ", ".join(missing)

        if not messagebox.askyesno("Clean these locations?", message, icon="warning", parent=self):
            return
        if permanent and not messagebox.askyesno(
            "Confirm permanent deletion",
            "You are about to permanently delete:\n\n" + "\n".join(permanent)
            + "\n\nThis bypasses the Recycle Bin and cannot be undone. Continue?",
            icon="warning", parent=self,
        ):
            return
        self.app.start_clean(chosen)


# --------------------------------------------------------------------------- #
# applications tab
# --------------------------------------------------------------------------- #

class AppsTab(ttk.Frame):
    COLUMNS = ("name", "publisher", "version", "size", "installed", "source")
    HEADINGS = {
        "name": "Application",
        "publisher": "Publisher",
        "version": "Version",
        "size": "Size",
        "installed": "Installed",
        "source": "Type",
    }

    def __init__(self, master, app: "DriveCleaner"):
        super().__init__(master, padding=(0, 12))
        self.app = app
        self.all_apps: list[appreg.App] = []
        self.rows: list[appreg.App] = []
        self.sort_key = "name"
        self.sort_reverse = False
        self.search = tk.StringVar()
        self.source_filter = tk.StringVar(value="All")
        self.silent = tk.BooleanVar(value=False)
        self.hide_system = tk.BooleanVar(value=True)
        self._build()

    def _build(self) -> None:
        bar = ttk.Frame(self)
        bar.pack(fill="x", pady=(0, 10))

        self.refresh_btn = ttk.Button(bar, text="Refresh list", style="Accent.TButton",
                                      command=self.app.start_app_load)
        self.refresh_btn.pack(side="left")

        ttk.Label(bar, text="Search", style="Muted.TLabel").pack(side="left", padx=(16, 6))
        entry = ttk.Entry(bar, textvariable=self.search, width=26)
        entry.pack(side="left")
        entry.bind("<Escape>", lambda _e: self.search.set(""))
        self.search.trace_add("write", lambda *_: self._render())

        ttk.Label(bar, text="Type", style="Muted.TLabel").pack(side="left", padx=(14, 6))
        combo = ttk.Combobox(bar, textvariable=self.source_filter, width=10, state="readonly",
                             values=["All", "64-bit", "32-bit", "User", "Store"])
        combo.pack(side="left")
        combo.bind("<<ComboboxSelected>>", lambda _e: self._render())

        ttk.Checkbutton(bar, text="Hide Windows components", variable=self.hide_system,
                        command=self._render).pack(side="left", padx=(14, 0))

        self.count_label = ttk.Label(bar, text="Loading installed apps…", style="Muted.TLabel")
        self.count_label.pack(side="right")

        card = _card(self)
        card.pack(fill="both", expand=True)
        inner = tk.Frame(card, bg=CARD)
        inner.pack(fill="both", expand=True, padx=1, pady=1)

        self.tree = ttk.Treeview(inner, columns=self.COLUMNS, show="headings", selectmode="extended")
        widths = {"name": 330, "publisher": 230, "version": 110, "size": 80,
                  "installed": 95, "source": 80}
        anchors = {"size": "e", "installed": "center", "source": "center"}
        for col in self.COLUMNS:
            self.tree.heading(col, text=self.HEADINGS[col],
                              command=lambda c=col: self._sort_by(c))
            self.tree.column(col, width=widths[col], anchor=anchors.get(col, "w"),
                             stretch=(col in ("name", "publisher")))
        self.tree.tag_configure("store", foreground="#4b5563")

        scroll = ttk.Scrollbar(inner, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        inner.rowconfigure(0, weight=1)
        inner.columnconfigure(0, weight=1)
        self.tree.bind("<Double-1>", lambda _e: self.open_location())

        actions = ttk.Frame(self)
        actions.pack(fill="x", pady=(10, 0))

        self.uninstall_btn = ttk.Button(actions, text="Uninstall selected", style="Danger.TButton",
                                        command=self.uninstall_selected)
        self.uninstall_btn.pack(side="left")
        ttk.Checkbutton(actions, text="Use silent uninstall when available",
                        variable=self.silent).pack(side="left", padx=(14, 0))
        ttk.Button(actions, text="Open install folder", command=self.open_location).pack(side="left", padx=(14, 0))
        ttk.Button(actions, text="Copy command", command=self.copy_command).pack(side="left", padx=(6, 0))

        self.status_label = ttk.Label(actions, text="", style="Muted.TLabel")
        self.status_label.pack(side="right")

    # -- data --------------------------------------------------------------- #

    def load(self, apps: list[appreg.App]) -> None:
        self.all_apps = apps
        self._render()

    def _filtered(self) -> list[appreg.App]:
        query = self.search.get().strip().lower()
        source = self.source_filter.get()
        rows = self.all_apps
        if source != "All":
            rows = [a for a in rows if a.source == source]
        if self.hide_system.get():
            rows = [a for a in rows if not a.system]
        if query:
            rows = [a for a in rows if query in a.name.lower() or query in a.publisher.lower()]
        return rows

    def _sort_by(self, column: str) -> None:
        if self.sort_key == column:
            self.sort_reverse = not self.sort_reverse
        else:
            self.sort_key = column
            self.sort_reverse = column in ("size", "installed")
        self._render()

    def _render(self) -> None:
        rows = self._filtered()
        key = self.sort_key
        if key == "size":
            rows.sort(key=lambda a: a.size_kb, reverse=self.sort_reverse)
        else:
            rows.sort(key=lambda a: str(getattr(a, key, "")).lower(), reverse=self.sort_reverse)

        self.rows = rows
        self.tree.delete(*self.tree.get_children())
        arrow = " ↓" if self.sort_reverse else " ↑"
        for col in self.COLUMNS:
            self.tree.heading(col, text=self.HEADINGS[col] + (arrow if col == key else ""))

        for i, row in enumerate(rows):
            self.tree.insert(
                "", "end", iid=str(i),
                values=(
                    row.name,
                    row.publisher or "—",
                    row.version or "—",
                    scanner.human_size(row.size_kb * 1024) if row.size_kb else "—",
                    row.install_date or "—",
                    row.source or "—",
                ),
                tags=("store",) if row.is_store else (),
            )
        total_size = sum(a.size_kb for a in rows) * 1024
        self.count_label.configure(
            text=f"{len(rows):,} of {len(self.all_apps):,} apps · {scanner.human_size(total_size)}"
        )

    def _selected(self) -> list[appreg.App]:
        out = []
        for iid in self.tree.selection():
            try:
                out.append(self.rows[int(iid)])
            except (ValueError, IndexError):
                continue
        return out

    # -- actions ------------------------------------------------------------ #

    def uninstall_selected(self) -> None:
        selected = self._selected()
        if not selected:
            messagebox.showinfo("No selection",
                                "Select one or more applications in the list first.",
                                parent=self)
            return

        names = "\n".join(f"• {a.name}" for a in selected[:12])
        if len(selected) > 12:
            names += f"\n… and {len(selected) - 12} more"
        if not messagebox.askyesno(
            "Run uninstallers?",
            f"This starts the uninstaller for {len(selected)} application(s):\n\n{names}\n\n"
            "Each uninstaller runs on its own — some will ask for administrator approval "
            "and some will require you to click through their wizard.",
            parent=self,
        ):
            return

        self.status_label.configure(text="Starting uninstallers…")
        self.update_idletasks()

        ok = 0
        problems = []
        for app in selected:
            success, info = appreg.launch_uninstall(app, silent=self.silent.get())
            if success:
                ok += 1
            else:
                problems.append(f"{app.name}: {info}")
            self.update_idletasks()

        if problems:
            messagebox.showwarning(
                "Some uninstallers could not start",
                f"Started {ok} of {len(selected)}.\n\n" + "\n".join(problems[:10]),
                parent=self,
            )
        self.status_label.configure(text=f"Started {ok} uninstaller(s). Refresh the list when done.")

    def open_location(self) -> None:
        selected = self._selected()
        if not selected:
            messagebox.showinfo("No selection", "Select an application first.", parent=self)
            return
        ok, info = appreg.open_install_location(selected[0])
        if not ok:
            messagebox.showwarning("Cannot open folder", info, parent=self)

    def copy_command(self) -> None:
        selected = self._selected()
        if not selected:
            return
        command = appreg.uninstall_command(selected[0], silent=self.silent.get())
        self.clipboard_clear()
        self.clipboard_append(command)
        self.status_label.configure(text="Uninstall command copied to clipboard.")


# --------------------------------------------------------------------------- #
# main window
# --------------------------------------------------------------------------- #

class DriveCleaner(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Drive Cleaner")
        self.geometry("1160x800")
        self.minsize(980, 660)
        self.configure(bg=BG)
        _style()
        self._tune_fonts()

        self.queue: queue.Queue = queue.Queue()
        self.scan_stop = threading.Event()
        self.clean_stop = threading.Event()
        self.categories = scanner.build_categories()
        self.results: dict[str, scanner.ScanResult] = {}
        self.checked = {c.key for c in self.categories if c.default}
        self.busy = False

        self._build_header()
        self._build_body()

        self.after(80, self._poll)
        self.refresh_drives()
        self.after(400, self.start_scan)
        self.after(250, self.start_app_load)

    # -- chrome ------------------------------------------------------------- #

    def _tune_fonts(self) -> None:
        for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont"):
            try:
                tkfont.nametofont(name).configure(family="Segoe UI", size=9)
            except tk.TclError:
                pass

    def _build_header(self) -> None:
        header = ttk.Frame(self, padding=(22, 16, 22, 0))
        header.pack(fill="x")

        left = ttk.Frame(header)
        left.pack(side="left")
        ttk.Label(left, text="Drive Cleaner", style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            left,
            text="Review what will be removed, then clean. Deleted files go to the Recycle Bin unless marked permanent.",
            style="Sub.TLabel",
        ).pack(anchor="w", pady=(2, 0))

        if not scanner.is_admin():
            badge = tk.Label(
                header,
                text="Restart as administrator",
                bg=SOFT_BLUE, fg=ACCENT, cursor="hand2", padx=12, pady=6,
                font=("Segoe UI", 9, "bold"),
            )
            badge.pack(side="right")
            badge.bind("<Button-1>", lambda _e: self.relaunch_as_admin())
            tk.Label(header, text="Some system folders are skipped without admin rights.",
                     bg=BG, fg=MUTED, font=("Segoe UI", 8)).pack(side="right", padx=(0, 10))

        self.drive_strip = tk.Frame(self, bg=BG)
        self.drive_strip.pack(fill="x", padx=16, pady=(14, 0))

    def _build_body(self) -> None:
        body = ttk.Frame(self, padding=(22, 6, 22, 16))
        body.pack(fill="both", expand=True)

        notebook = ttk.Notebook(body)
        notebook.pack(fill="both", expand=True)
        self.notebook = notebook
        self.cleanup_tab = CleanupTab(notebook, self)
        self.apps_tab = AppsTab(notebook, self)
        notebook.add(self.cleanup_tab, text="  Clean up disks  ")
        notebook.add(self.apps_tab, text="  Installed apps  ")

    # -- drives ------------------------------------------------------------- #

    def refresh_drives(self) -> None:
        threading.Thread(target=lambda: self.queue.put(("drives", scanner.drive_stats())),
                         daemon=True).start()

    def _render_drives(self, drives: list[dict]) -> None:
        for widget in self.drive_strip.winfo_children():
            widget.destroy()
        for i, info in enumerate(drives[:4]):
            card = DriveCard(self.drive_strip, info)
            card.grid(row=0, column=i, padx=(6 if i else 0, 6), sticky="nsew")
            self.drive_strip.columnconfigure(i, weight=1, uniform="drives")

    # -- scanning ----------------------------------------------------------- #

    def start_scan(self) -> None:
        if self.busy:
            return
        self.busy = True
        self.scan_stop.clear()
        self.results = {}
        self.cleanup_tab.set_busy(True, "Scanning…")
        self.cleanup_tab.reload()
        threading.Thread(target=self._scan_worker, daemon=True).start()

    def _scan_worker(self) -> None:
        for cat in self.categories:
            if self.scan_stop.is_set():
                break
            self.queue.put(("scan_start", cat.label))
            data, files, locations, errors, truncated = scanner.scan_targets(
                cat.targets(), stop=self.scan_stop.is_set
            )
            result = scanner.ScanResult(
                key=cat.key, label=cat.label, bytes=data, files=files,
                locations=locations, truncated=truncated, errors=errors,
            )
            self.queue.put(("scan_done", result))
        self.queue.put(("scan_finished",))

    def start_clean(self, chosen: list[scanner.Category]) -> None:
        if self.busy:
            return
        self.busy = True
        self.clean_stop.clear()
        self.cleanup_tab.set_busy(True, "Cleaning…")
        self.cleanup_tab.clear_log()
        threading.Thread(target=self._clean_worker, args=(chosen,), daemon=True).start()

    def _clean_worker(self, chosen: list[scanner.Category]) -> None:
        result = scanner.clean(
            chosen,
            log=lambda text: self.queue.put(("log", text)),
            stop=self.clean_stop.is_set,
        )
        self.queue.put(("clean_done", result))

    def start_app_load(self) -> None:
        self.apps_tab.count_label.configure(text="Loading installed apps…")
        self.apps_tab.refresh_btn.configure(state="disabled")
        threading.Thread(target=self._app_worker, daemon=True).start()

    def _app_worker(self) -> None:
        try:
            apps = appreg.list_apps(include_store=True)
            self.queue.put(("apps_loaded", apps))
        except Exception as exc:  # pragma: no cover - defensive
            self.queue.put(("apps_error", str(exc)))

    # -- queue pump --------------------------------------------------------- #

    def _poll(self) -> None:
        try:
            while True:
                message = self.queue.get_nowait()
                self._handle(message)
        except queue.Empty:
            pass
        self.after(80, self._poll)

    def _handle(self, message: tuple) -> None:
        kind = message[0]

        if kind == "drives":
            self._render_drives(message[1])

        elif kind == "scan_start":
            self.cleanup_tab.summary.configure(text=f"Scanning {message[1]}…")

        elif kind == "scan_done":
            result: scanner.ScanResult = message[1]
            self.results[result.key] = result
            self.cleanup_tab.reload()

        elif kind == "scan_finished":
            self.busy = False
            self.cleanup_tab.set_busy(False)
            self.cleanup_tab.reload()
            total = sum(r.bytes for r in self.results.values())
            self.cleanup_tab.log(
                f"Scan complete — {scanner.human_size(total)} reclaimable across "
                f"{len(self.results)} locations."
            )

        elif kind == "log":
            self.cleanup_tab.log(message[1])

        elif kind == "clean_done":
            self.busy = False
            self.cleanup_tab.set_busy(False)
            result = message[1]
            failed = result["failed"]
            freed = result["freed"]
            recycled = result["recycled"]
            self.cleanup_tab.log(
                f"Finished — {scanner.human_size(freed)} removed permanently, "
                f"{scanner.human_size(recycled)} moved to the Recycle Bin, "
                f"{len(failed)} items skipped."
            )
            for path, reason in failed[:8]:
                self.cleanup_tab.log(f"  skipped: {path} ({reason})")
            self.refresh_drives()

            lines = []
            if freed:
                lines.append(f"{scanner.human_size(freed)} removed permanently.")
            if recycled:
                lines.append(f"{scanner.human_size(recycled)} moved to the Recycle Bin.")
            if failed:
                lines.append(f"{len(failed)} items skipped because they were in use.")
            summary = "\n".join(lines) or "Nothing needed to be removed."

            if recycled:
                answer = messagebox.askyesno(
                    "Cleanup finished",
                    summary + "\n\nFiles sent to the Recycle Bin still take up disk space. "
                    "Empty it now to actually reclaim it?",
                    icon="question", parent=self,
                )
                if answer:
                    before, _items = scanner.recycle_bin_stats()
                    if scanner.empty_recycle_bin():
                        self.refresh_drives()
                        messagebox.showinfo(
                            "Recycle Bin emptied",
                            f"{scanner.human_size(before)} reclaimed.",
                            parent=self,
                        )
                    else:
                        messagebox.showwarning(
                            "Could not empty the Recycle Bin",
                            "Windows refused the request. Open the Recycle Bin and empty it manually.",
                            parent=self,
                        )
            else:
                messagebox.showinfo("Cleanup finished", summary, parent=self)

            self.after(300, self.start_scan)

        elif kind == "apps_loaded":
            apps = message[1]
            self.apps_tab.load(apps)
            self.apps_tab.refresh_btn.configure(state="normal")
            self.apps_tab.status_label.configure(text="")

        elif kind == "apps_error":
            self.apps_tab.refresh_btn.configure(state="normal")
            self.apps_tab.count_label.configure(text="Could not load applications")
            self.apps_tab.status_label.configure(text=message[1])

    # -- misc --------------------------------------------------------------- #

    def relaunch_as_admin(self) -> None:
        script = os.path.abspath(__file__)
        try:
            rc = ctypes.windll.shell32.ShellExecuteW(
                None, "runas", sys.executable, f'"{script}"', None, 1
            )
        except Exception as exc:
            messagebox.showerror("Could not elevate", str(exc), parent=self)
            return
        if rc > 32:
            self.destroy()
        else:
            messagebox.showinfo(
                "Elevation cancelled",
                "Continuing without administrator rights. System folders will be skipped.",
                parent=self,
            )


def main() -> None:
    # Crisp text on high-DPI displays instead of a blurry bitmap stretch.
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass
    app = DriveCleaner()
    app.mainloop()


if __name__ == "__main__":
    main()
