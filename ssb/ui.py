"""Tk window; all Windows mutations remain in the blocking engine."""
import datetime as dt
import logging
import queue
import threading
from . import core as engine
from .paths import resource

class SSBWindow:
    def __init__(self) -> None:
        import tkinter as tk
        from tkinter import ttk

        self.tk = tk
        self.ttk = ttk
        self.root = tk.Tk()
        self.root.title(engine.APP_NAME)
        try:
            self.root.iconbitmap(str(resource("icons/ssb.ico")))
        except tk.TclError:
            pass
        self.root.geometry("820x720")
        self.root.minsize(780, 680)
        self.root.option_add("*Font", ("Segoe UI", 10))
        self.current_config = engine.load_config()
        self.sites = [dict(site) for site in self.current_config["sites"]]
        self.firefox_var = tk.BooleanVar(value=self.current_config.get("sync_firefox", True))
        self.updater = None
        self.hostname_var = tk.StringVar()
        self.subdomains_var = tk.BooleanVar(value=True)
        self.start_var = tk.StringVar(value=self.current_config["block_start"])
        self.end_var = tk.StringVar(value=self.current_config["block_end"])
        self.status_var = tk.StringVar()
        self.progress_var = tk.StringVar()
        self._busy = False
        self._events: queue.Queue = queue.Queue()
        self._disabled_controls = []
        self.root.protocol("WM_DELETE_WINDOW", self._close)
        self._build()
        self._refresh_table()
        self._refresh_status()

    def _build(self) -> None:
        from tkinter import ttk

        outer = ttk.Frame(self.root, padding=20)
        outer.pack(fill="both", expand=True)
        heading = ttk.Frame(outer)
        heading.pack(fill="x", pady=(0, 12))
        try:
            self.logo = self.tk.PhotoImage(file=str(resource("icons/header.png")))
            ttk.Label(heading, image=self.logo).pack(side="left", padx=(0, 12))
        except self.tk.TclError:
            pass
        ttk.Label(heading, text="SSB", font=("Segoe UI Semibold", 26)).pack(side="left")
        from .status_light import StatusLight
        self.status_light = StatusLight(heading, self._refresh_status_clicked)
        self.status_light.widget.pack(side="right", padx=(12, 0))
        ttk.Label(heading, text="v" + engine.APP_VERSION).pack(side="right")
        status = ttk.Label(outer, textvariable=self.status_var, padding=10, relief="solid", wraplength=720)
        status.pack(fill="x", pady=(0, 14))

        site_box = ttk.LabelFrame(outer, text="Websites", padding=10)
        site_box.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(site_box, columns=("hostname", "subdomains"), show="headings", height=9)
        self.tree.heading("hostname", text="Website")
        self.tree.heading("subdomains", text="All subdomains")
        self.tree.column("hostname", width=440, anchor="w")
        self.tree.column("subdomains", width=130, anchor="center")
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self._load_selected)

        entry_row = ttk.Frame(site_box)
        entry_row.pack(fill="x", pady=(9, 0))
        ttk.Entry(entry_row, textvariable=self.hostname_var).pack(side="left", fill="x", expand=True)
        ttk.Checkbutton(entry_row, text="Include all subdomains", variable=self.subdomains_var).pack(side="left", padx=10)
        ttk.Button(entry_row, text="Add / Update", command=self._add_or_update).pack(side="left")
        ttk.Button(entry_row, text="Remove", command=self._remove_selected).pack(side="left", padx=(8, 0))

        schedule = ttk.LabelFrame(outer, text="Daily blocking period", padding=10)
        schedule.pack(fill="x", pady=14)
        times = [f"{hour:02d}:{minute:02d}" for hour in range(24) for minute in (0, 15, 30, 45)]
        ttk.Label(schedule, text="Block from").pack(side="left")
        ttk.Combobox(schedule, textvariable=self.start_var, values=times, width=8).pack(side="left", padx=8)
        ttk.Label(schedule, text="until").pack(side="left")
        ttk.Combobox(schedule, textvariable=self.end_var, values=times, width=8).pack(side="left", padx=8)
        ttk.Label(schedule, text="(local system time; midnight crossover is supported)").pack(side="left", padx=8)

        firefox = ttk.Frame(outer)
        firefox.pack(fill="x", pady=(0, 12))
        ttk.Checkbutton(firefox, text="Sync changes to Firefox", variable=self.firefox_var).pack(anchor="w")
        ttk.Label(firefox, text="Firefox may need a full restart after rules change or blocking hours end.",
                  wraplength=700).pack(anchor="w", pady=(4, 0))
        actions = ttk.Frame(outer)
        actions.pack(fill="x")
        self.save_button = ttk.Button(
            actions,
            text="Save Changes" if engine.installation_complete() else "Install SSB",
            command=self._save,
        )
        self.save_button.pack(side="left")
        ttk.Button(actions, text="Instructions", command=self._show_help).pack(side="left", padx=8)
        ttk.Button(actions, text="Check for Updates", command=self._check_updates).pack(side="left", padx=8)
        if engine.installation_complete():
            ttk.Button(actions, text="Uninstall SSB", command=self._uninstall).pack(side="left")
        ttk.Button(actions, text="Close", command=self._close).pack(side="right")
        self.progress_frame = ttk.Frame(outer)
        ttk.Label(self.progress_frame, textvariable=self.progress_var).pack(anchor="w")
        self.progress_bar = ttk.Progressbar(self.progress_frame, mode="indeterminate")
        self.progress_bar.pack(fill="x", pady=(4, 0))

    def _close(self) -> None:
        if self._busy:
            self.root.bell()
            return
        if self.updater:
            self.updater.close()
        self.root.destroy()

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        if busy:
            self._disabled_controls = []
            def disable(widget):
                for child in widget.winfo_children():
                    if isinstance(child, (self.ttk.Button, self.ttk.Entry, self.ttk.Checkbutton, self.ttk.Treeview)):
                        self._disabled_controls.append((child, child.instate(["disabled"])))
                        child.state(["disabled"])
                    disable(child)
            disable(self.root)
            self.progress_var.set("Preparing changes...")
            self.progress_frame.pack(fill="x", pady=(12, 0))
            self.progress_bar.start(12)
        else:
            self.progress_bar.stop()
            self.progress_frame.pack_forget()
            for widget, was_disabled in self._disabled_controls:
                if not was_disabled:
                    widget.state(["!disabled"])
            self._disabled_controls = []

    def _save_worker(self, proposed: dict) -> None:
        # Only the main Tk thread may touch widgets or show dialogs.
        try:
            engine.install_or_update(proposed, progress=lambda message: self._events.put(("progress", message)))
        except Exception as exc:
            logging.exception("SSB could not save")
            self._events.put(("error", str(exc)))
        else:
            self._events.put(("done", proposed))

    def _poll_save(self) -> None:
        from tkinter import messagebox

        while True:
            try:
                kind, value = self._events.get_nowait()
            except queue.Empty:
                break
            if kind == "progress":
                self.progress_var.set(value)
                continue
            self._set_busy(False)
            if kind == "error":
                messagebox.showerror("SSB could not save", value, parent=self.root)
            else:
                self.current_config = value
                self.save_button.configure(text="Save Changes")
                self._refresh_status()
                messagebox.showinfo("SSB is ready", "Settings were saved. Restart Firefox fully to refresh its website policies.", parent=self.root)
            return
        if self._busy:
            self.root.after(75, self._poll_save)

    def _refresh_table(self) -> None:
        self.tree.delete(*self.tree.get_children())
        for site in sorted(self.sites, key=lambda item: item["hostname"]):
            self.tree.insert("", "end", iid=site["hostname"], values=(site["hostname"], "Yes" if site["include_subdomains"] else "No"))

    def _refresh_status(self) -> None:
        now = dt.datetime.now().astimezone()
        start = engine.parse_clock(self.current_config["block_start"])
        end = engine.parse_clock(self.current_config["block_end"])
        state = engine.load_json(engine.STATE_PATH, {}) or {}
        exception = engine.active_exception(now, state)
        installed = engine.installation_complete()
        blocked = state.get('blocked') is True
        self.status_light.set_state('disabled' if not installed else 'active' if blocked else 'primed')
        if not installed:
            text = "Not installed. Review the website list and schedule, then select Install SSB."
        elif blocked:
            text = f"Blocking is active. Scheduled period: {self.current_config['block_start']}–{self.current_config['block_end']}."
        elif engine.in_block_window(now, start, end) and exception:
            text = f"Temporarily open until {exception.astimezone().strftime('%H:%M')}. Scheduled period: {self.current_config['block_start']}–{self.current_config['block_end']}."
        elif engine.in_block_window(now, start, end):
            text = f"Waiting for scheduled blocking. Scheduled period: {self.current_config['block_start']}–{self.current_config['block_end']}."
        else:
            text = f"SSB blocking is off. Firefox may need a restart. Scheduled period: {self.current_config['block_start']}–{self.current_config['block_end']}."
        self.status_var.set(text)

    def _load_selected(self, _event=None) -> None:
        selected = self.tree.selection()
        if not selected:
            return
        hostname = selected[0]
        site = next(item for item in self.sites if item["hostname"] == hostname)
        self.hostname_var.set(hostname)
        self.subdomains_var.set(site["include_subdomains"])

    def _add_or_update(self) -> None:
        from tkinter import messagebox

        try:
            hostname = engine.normalize_hostname(self.hostname_var.get())
        except ValueError as exc:
            messagebox.showerror("Invalid website", str(exc), parent=self.root)
            return
        updated = False
        for site in self.sites:
            if site["hostname"] == hostname:
                site["include_subdomains"] = self.subdomains_var.get()
                updated = True
                break
        if not updated:
            self.sites.append({"hostname": hostname, "include_subdomains": self.subdomains_var.get()})
        self.hostname_var.set("")
        self.subdomains_var.set(True)
        self._refresh_table()

    def _remove_selected(self) -> None:
        selected = set(self.tree.selection())
        if not selected:
            return
        self.sites = [site for site in self.sites if site["hostname"] not in selected]
        self._refresh_table()

    def _proposed_config(self) -> dict:
        return engine.migrate_config({
            "version": 1,
            "block_start": self.start_var.get().strip(),
            "block_end": self.end_var.get().strip(),
            "default_unlock_minutes": self.current_config.get("default_unlock_minutes", 30),
            "maximum_unlock_minutes": self.current_config.get("maximum_unlock_minutes", 120),
            "sites": self.sites,
            "sync_firefox": self.firefox_var.get(),
        })

    def _save(self) -> None:
        from tkinter import messagebox

        if self._busy or (self.updater and self.updater.shutdown_requested.is_set()):
            return
        try:
            proposed = self._proposed_config()
        except (ValueError, TypeError) as exc:
            messagebox.showerror("Invalid configuration", str(exc), parent=self.root)
            return
        site_lines = "\n".join(
            f"• {site['hostname']}{' and all subdomains' if site['include_subdomains'] else ''}"
            for site in proposed["sites"]
        )
        summary = f"Block daily from {proposed['block_start']} until {proposed['block_end']}:\n\n{site_lines}"
        if not messagebox.askyesno("Confirm SSB configuration", summary, parent=self.root):
            return

        changed = proposed != self.current_config
        now = dt.datetime.now().astimezone()
        if engine.installation_complete() and engine.requires_blocked_period_confirmation(
            now, self.current_config, proposed, changed
        ):
            warning = (
                "The present time falleth within the existing or proposed blocked period.\n\n"
                "Changing websites or hours now may immediately grant or remove access. "
                "Dost thou truly wish to apply this change?"
            )
            if not messagebox.askyesno("Blocked-period confirmation", warning, icon="warning", parent=self.root):
                return
        self._set_busy(True)
        try:
            threading.Thread(target=self._save_worker, args=(proposed,), daemon=False).start()
        except Exception as exc:
            self._events.put(("error", str(exc)))
        self.root.after(75, self._poll_save)

    def _show_help(self) -> None:
        import tkinter as tk
        from tkinter import ttk

        window = tk.Toplevel(self.root)
        window.title("SSB Instructions")
        window.geometry("720x570")
        window.transient(self.root)
        text = tk.Text(window, wrap="word", padx=16, pady=16, font=("Segoe UI", 10))
        text.insert("1.0", engine.HELP_TEXT)
        text.configure(state="disabled")
        text.pack(fill="both", expand=True)
        ttk.Button(window, text="Close", command=window.destroy).pack(pady=10)

    def _check_updates(self) -> None:
        from tkinter import messagebox
        if self._busy:
            return
        try:
            from .updater import Updater
            if self.updater is None:
                self.updater = Updater(lambda: not self._busy)
            self.updater.check()
        except Exception as exc:
            messagebox.showerror("Update check unavailable", str(exc), parent=self.root)

    def _poll_updater(self):
        if self.updater and self.updater.shutdown_requested.is_set() and not self._busy:
            self.root.destroy()
            return
        self.root.after(100, self._poll_updater)

    def _poll_status(self):
        # Schedule boundaries and temporary exceptions can change while the
        # manager remains open. Keep the label current without saving settings.
        try:
            if not self._busy:
                self._refresh_status()
        except (OSError, ValueError) as exc:
            self.status_var.set(f"Status unavailable: {exc}")
        finally:
            self.root.after(1000, self._poll_status)

    def _refresh_status_clicked(self):
        if self._busy:
            return
        try:
            self._refresh_status()
        except (OSError, ValueError) as exc:
            self.status_var.set(f"Status unavailable: {exc}")

    def _uninstall(self) -> None:
        from tkinter import messagebox
        try:
            engine.launch_uninstaller()
            self._close()
        except Exception as exc:
            messagebox.showerror("Uninstallation failed", str(exc), parent=self.root)

    def run(self) -> None:
        self.root.after(100, self._poll_updater)
        self.root.after(1000, self._poll_status)
        self.root.mainloop()
