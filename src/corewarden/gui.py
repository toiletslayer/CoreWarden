"""Minimal native Windows desktop interface for CoreWarden."""

from __future__ import annotations

import os
import sys
import threading
import tkinter as tk
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, tzinfo
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText
from typing import Any

from corewarden.credentials import WindowsCredentialStore
from corewarden.desktop import (
    DEFAULT_DESKTOP_RPC_URL,
    DesktopConfiguration,
    DesktopRunResult,
    DesktopService,
)
from corewarden.diagnostics import SecretRedactor
from corewarden.errors import CoreWardenError
from corewarden.history import (
    HISTORY_RETENTION_LIMIT,
    SanitizedHistoryEvent,
    local_timestamp_values,
)
from corewarden.monitoring import (
    DEFAULT_MONITORING_INTERVAL_SECONDS,
    SUPPORTED_MONITORING_INTERVAL_MINUTES,
    MonitoringService,
    MonitoringStatus,
)
from corewarden.tray import TrayController

FIRST_RUN_GUIDANCE = (
    "Start here: Choose a provider and enter its settings  →  Test Provider  →  "
    "Test Node  →  Run Diagnosis  →  Read the result"
)

RESULT_PLACEHOLDER = "Diagnosis results will appear here."

PROVIDER_LABELS = {
    "openai": "OpenAI",
    "bedrock": "Amazon Bedrock",
}

CREDENTIAL_STATUS_LABELS = {
    "saved": "OpenAI credential: Saved securely",
    "environment": "OpenAI credential: Available from environment",
    "missing": "OpenAI credential: Not configured",
    "unavailable": "OpenAI credential: Secure storage unavailable",
}

PRIVACY_NOTICE = """CoreWarden uses only four read-only node RPC methods:
getblockchaininfo, getnetworkinfo, getpeerinfo, and getchaintips.

Peer-identifying and endpoint data is filtered locally before model access.
OpenAI keys saved by the app use the current user's Windows Credential Manager.
CoreWarden does not intentionally persist raw RPC credentials or peer-identifying observations."""


@dataclass(frozen=True, slots=True)
class ProviderVisibility:
    openai: bool
    bedrock: bool


def provider_id_from_label(label: str) -> str:
    """Translate a user-facing provider label to the existing internal identifier."""
    for provider_id, provider_label in PROVIDER_LABELS.items():
        if provider_label == label:
            return provider_id
    raise ValueError(f"Unknown provider label: {label}")


def provider_visibility(label: str) -> ProviderVisibility:
    provider_id = provider_id_from_label(label)
    return ProviderVisibility(openai=provider_id == "openai", bedrock=provider_id == "bedrock")


def default_rpc_url(environment: Mapping[str, str]) -> str:
    return environment.get("COREWARDEN_RPC_URL", DEFAULT_DESKTOP_RPC_URL)


def format_status(provider: str, node: str, activity: str | None = None) -> str:
    text = f"Provider: {provider} | Node: {node}"
    return f"{text} | {activity}" if activity else text


def credential_status_text(status: str) -> str:
    return CREDENTIAL_STATUS_LABELS[status]


def format_diagnosis(result: DesktopRunResult) -> str:
    """Render a concise, sanitized diagnosis for the desktop output panel."""
    diagnosis = result.diagnosis
    provider_name = PROVIDER_LABELS.get(result.provider, result.provider)
    lines = [
        f"Provider: {provider_name}",
        f"Classification: {diagnosis.classification.value}",
        f"Confidence: {diagnosis.confidence:.0%}",
        "",
        diagnosis.summary,
        "",
        "Evidence:",
    ]
    for item in diagnosis.evidence:
        lines.append(f"• {item.observation} — {item.significance}")
    lines.extend(["", f"Safety: {diagnosis.safety_boundary}"])
    return "\n".join(lines)


def format_monitoring_state(status: MonitoringStatus | None) -> str:
    if status is None or not status.active:
        return "Off"
    return status.current_state.value.title() if status.current_state else "Starting"


def format_monitoring_time(value: Any) -> str:
    if value is None:
        return "Never"
    return value.astimezone().strftime("%H:%M:%S")


def history_export_filename(extension: str, value: datetime | None = None) -> str:
    timestamp = (value or datetime.now()).strftime("%Y%m%d-%H%M%S")
    return f"corewarden-history-{timestamp}.{extension}"


def history_row(
    event: SanitizedHistoryEvent, local_timezone: tzinfo | None = None
) -> tuple[str, ...]:
    result = event.classification or ""
    if event.confidence is not None:
        result = f"{result} ({event.confidence:.0%})".strip()
    local_timestamp, _timezone_label = local_timestamp_values(event.timestamp, local_timezone)
    return (
        local_timestamp.replace("T", " ", 1),
        event.event_type.replace("_", " ").title(),
        (event.state or "").title(),
        event.reason,
        "Yes" if event.investigation_occurred else "No",
        event.provider or "",
        result,
    )


def _asset_path(name: str) -> Path:
    frozen_root = getattr(sys, "_MEIPASS", None)
    if frozen_root:
        return Path(frozen_root) / "assets" / name
    return Path(__file__).resolve().parents[2] / "assets" / name


class CoreWardenDesktop:
    """Small tkinter view over the testable DesktopService."""

    def __init__(
        self,
        root: tk.Tk,
        service: DesktopService | None = None,
        tray_factory: Callable[..., TrayController] = TrayController,
    ) -> None:
        self.root = root
        self.service = service or DesktopService(WindowsCredentialStore())
        self._busy_widgets: list[ttk.Button] = []
        self._brand_image: tk.PhotoImage | None = None
        self._monitor: MonitoringService | None = None
        self._tray: TrayController | None = None
        self._tray_factory = tray_factory
        self._history_window: tk.Toplevel | None = None
        self._quitting = False

        root.title("CoreWarden — Read-only Node Health")
        root.minsize(760, 800)
        root.geometry("840x920")
        root.protocol("WM_DELETE_WINDOW", self._close)
        icon = _asset_path("corewarden.ico")
        if icon.exists():
            with suppress(tk.TclError):
                root.iconbitmap(default=str(icon))
        logo = _asset_path("Sprite64.png")
        if logo.exists():
            with suppress(tk.TclError):
                self._brand_image = tk.PhotoImage(file=str(logo))

        self.provider = tk.StringVar(value=PROVIDER_LABELS["openai"])
        self.rpc_url = tk.StringVar(value=default_rpc_url(os.environ))
        self.rpc_user = tk.StringVar(value=os.environ.get("COREWARDEN_RPC_USER", ""))
        self.rpc_password = tk.StringVar(value=os.environ.get("COREWARDEN_RPC_PASSWORD", ""))
        self.rpc_cookie_path = tk.StringVar()
        self.openai_key = tk.StringVar()
        self.openai_status = tk.StringVar()
        self.aws_profile = tk.StringVar(value=os.environ.get("AWS_PROFILE", ""))
        self.aws_region = tk.StringVar(value=os.environ.get("AWS_REGION", "us-west-2"))
        self.bedrock_model = tk.StringVar(
            value=os.environ.get("COREWARDEN_MODEL_ID", "global.anthropic.claude-sonnet-4-6")
        )
        self.show_bedrock_advanced = tk.BooleanVar(value=False)
        self._provider_test_state = "Not tested"
        self._node_test_state = "Not tested"
        self.status = tk.StringVar(
            value=format_status(self._provider_test_state, self._node_test_state)
        )
        self.monitor_interval = tk.StringVar(value=str(DEFAULT_MONITORING_INTERVAL_SECONDS // 60))
        self.monitor_state = tk.StringVar(value="Monitoring: Off")
        self.monitor_details = tk.StringVar(
            value="Last check: Never | Last AI investigation: Never"
        )

        self._build()
        self._refresh_credential_status()
        self._sync_provider_settings()

    def _build(self) -> None:
        outer = ttk.Frame(self.root, padding=16)
        outer.pack(fill=tk.BOTH, expand=True)

        header = ttk.Frame(outer)
        header.pack(fill=tk.X, pady=(0, 8))
        if self._brand_image is not None:
            ttk.Label(header, image=self._brand_image).pack(side=tk.LEFT, padx=(0, 10))
        title = ttk.Frame(header)
        title.pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Label(title, text="CoreWarden", font=("Segoe UI", 20, "bold")).pack(anchor=tk.W)
        ttk.Label(
            title,
            text="Read-only health investigation for Core-compatible nodes",
        ).pack(anchor=tk.W)
        ttk.Button(header, text="Privacy", command=self._show_privacy).pack(side=tk.RIGHT)
        ttk.Button(header, text="History", command=self._show_history).pack(
            side=tk.RIGHT, padx=(0, 6)
        )

        ttk.Label(
            outer,
            text=FIRST_RUN_GUIDANCE,
            wraplength=780,
            justify=tk.LEFT,
        ).pack(anchor=tk.W, pady=(0, 10))

        provider_frame = ttk.LabelFrame(outer, text="Provider", padding=10)
        provider_frame.pack(fill=tk.X, pady=4)
        ttk.Label(provider_frame, text="Use:").grid(row=0, column=0, sticky=tk.W)
        provider_selector = ttk.Combobox(
            provider_frame,
            textvariable=self.provider,
            values=tuple(PROVIDER_LABELS.values()),
            state="readonly",
            width=18,
        )
        provider_selector.grid(row=0, column=1, sticky=tk.W, padx=8)
        provider_selector.bind("<<ComboboxSelected>>", self._sync_provider_settings)

        node_frame = ttk.LabelFrame(outer, text="Node connection", padding=10)
        node_frame.pack(fill=tk.X, pady=4)
        node_frame.columnconfigure(1, weight=1)
        self._entry_row(node_frame, 0, "RPC URL", self.rpc_url)
        self._entry_row(node_frame, 1, "RPC username", self.rpc_user)
        self._entry_row(node_frame, 2, "RPC password", self.rpc_password, show="•")
        ttk.Label(node_frame, text="RPC cookie file").grid(row=3, column=0, sticky=tk.W, pady=3)
        ttk.Entry(node_frame, textvariable=self.rpc_cookie_path).grid(
            row=3, column=1, sticky=tk.EW, padx=8, pady=3
        )
        ttk.Button(node_frame, text="Browse…", command=self._choose_cookie).grid(
            row=3, column=2, pady=3
        )
        ttk.Label(
            node_frame,
            text="Use either username/password or a cookie file. These values are not saved.",
        ).grid(row=4, column=0, columnspan=3, sticky=tk.W, pady=(3, 0))

        provider_settings = ttk.Frame(outer)
        provider_settings.pack(fill=tk.X)

        self.openai_frame = ttk.LabelFrame(provider_settings, text="OpenAI", padding=10)
        self.openai_frame.columnconfigure(1, weight=1)
        ttk.Label(self.openai_frame, text="API key").grid(row=0, column=0, sticky=tk.W)
        ttk.Entry(self.openai_frame, textvariable=self.openai_key, show="•").grid(
            row=0, column=1, sticky=tk.EW, padx=8
        )
        ttk.Button(self.openai_frame, text="Save securely", command=self._save_key).grid(
            row=0, column=2
        )
        ttk.Button(self.openai_frame, text="Remove saved key", command=self._remove_key).grid(
            row=0, column=3, padx=(6, 0)
        )
        ttk.Label(self.openai_frame, textvariable=self.openai_status).grid(
            row=1, column=0, columnspan=4, sticky=tk.W, pady=(6, 0)
        )

        self.bedrock_frame = ttk.LabelFrame(provider_settings, text="Amazon Bedrock", padding=10)
        self.bedrock_frame.columnconfigure(1, weight=1)
        self._entry_row(self.bedrock_frame, 0, "AWS profile", self.aws_profile)
        self._entry_row(self.bedrock_frame, 1, "AWS region", self.aws_region)
        ttk.Checkbutton(
            self.bedrock_frame,
            text="Show advanced model setting",
            variable=self.show_bedrock_advanced,
            command=self._sync_bedrock_advanced,
        ).grid(row=2, column=0, columnspan=3, sticky=tk.W, pady=(4, 0))
        self.bedrock_advanced_frame = ttk.Frame(self.bedrock_frame)
        self.bedrock_advanced_frame.grid(row=3, column=0, columnspan=3, sticky=tk.EW)
        self.bedrock_advanced_frame.columnconfigure(1, weight=1)
        self._entry_row(self.bedrock_advanced_frame, 0, "Model ID", self.bedrock_model)
        ttk.Label(
            self.bedrock_frame,
            text="Uses the existing AWS CLI/profile/session; CoreWarden stores no AWS secret.",
        ).grid(row=4, column=0, columnspan=3, sticky=tk.W, pady=(3, 0))
        self._sync_bedrock_advanced()

        actions = ttk.Frame(outer)
        actions.pack(fill=tk.X, pady=10)
        self._add_action(actions, "Test Provider", self._test_provider)
        self._add_action(actions, "Test Node", self._test_node)
        self._add_action(actions, "Run Diagnosis", self._run_diagnosis)

        ttk.Label(outer, textvariable=self.status).pack(anchor=tk.W, pady=(0, 6))
        monitor_frame = ttk.LabelFrame(outer, text="Optional local monitoring", padding=10)
        monitor_frame.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(monitor_frame, textvariable=self.monitor_state).grid(row=0, column=0, sticky=tk.W)
        ttk.Label(monitor_frame, text="Interval (minutes)").grid(
            row=0, column=1, sticky=tk.E, padx=(20, 4)
        )
        ttk.Combobox(
            monitor_frame,
            textvariable=self.monitor_interval,
            values=tuple(str(value) for value in SUPPORTED_MONITORING_INTERVAL_MINUTES),
            state="readonly",
            width=5,
        ).grid(row=0, column=2, sticky=tk.W)
        self.start_monitor_button = ttk.Button(
            monitor_frame, text="Start Monitoring", command=self._start_monitoring
        )
        self.start_monitor_button.grid(row=0, column=3, padx=(12, 4))
        self.stop_monitor_button = ttk.Button(
            monitor_frame, text="Stop Monitoring", command=self._stop_monitoring
        )
        self.stop_monitor_button.grid(row=0, column=4)
        self.stop_monitor_button.configure(state=tk.DISABLED)
        ttk.Label(monitor_frame, textvariable=self.monitor_details).grid(
            row=1, column=0, columnspan=5, sticky=tk.W, pady=(5, 3)
        )
        self.monitor_history = ScrolledText(
            monitor_frame, wrap=tk.WORD, height=4, font=("Segoe UI", 9)
        )
        self.monitor_history.grid(row=2, column=0, columnspan=5, sticky=tk.EW)
        self.monitor_history.configure(state=tk.DISABLED)
        monitor_frame.columnconfigure(0, weight=1)

        self.output = ScrolledText(outer, wrap=tk.WORD, height=12, font=("Segoe UI", 10))
        self.output.pack(fill=tk.BOTH, expand=True)
        self.output.configure(state=tk.DISABLED)
        self._show_text(RESULT_PLACEHOLDER)

    @staticmethod
    def _entry_row(
        frame: ttk.Frame,
        row: int,
        label: str,
        variable: tk.StringVar,
        *,
        show: str | None = None,
    ) -> None:
        ttk.Label(frame, text=label).grid(row=row, column=0, sticky=tk.W, pady=3)
        ttk.Entry(frame, textvariable=variable, show=show).grid(
            row=row, column=1, columnspan=2, sticky=tk.EW, padx=8, pady=3
        )

    def _add_action(self, frame: ttk.Frame, label: str, command: Callable[[], None]) -> None:
        button = ttk.Button(frame, text=label, command=command)
        button.pack(side=tk.LEFT, padx=(0, 8))
        self._busy_widgets.append(button)

    def _sync_provider_settings(self, event: Any | None = None) -> None:
        visibility = provider_visibility(self.provider.get())
        self.openai_frame.pack_forget()
        self.bedrock_frame.pack_forget()
        if visibility.openai:
            self.openai_frame.pack(fill=tk.X, pady=4)
        if visibility.bedrock:
            self.bedrock_frame.pack(fill=tk.X, pady=4)
        if event is not None:
            self._provider_test_state = "Not tested"
            self._update_status()

    def _sync_bedrock_advanced(self) -> None:
        if self.show_bedrock_advanced.get():
            self.bedrock_advanced_frame.grid()
        else:
            self.bedrock_advanced_frame.grid_remove()

    def _configuration(self) -> DesktopConfiguration:
        return DesktopConfiguration(
            provider=provider_id_from_label(self.provider.get()),
            rpc_url=self.rpc_url.get(),
            rpc_user=self.rpc_user.get(),
            rpc_password=self.rpc_password.get(),
            rpc_cookie_path=self.rpc_cookie_path.get(),
            aws_profile=self.aws_profile.get(),
            aws_region=self.aws_region.get(),
            bedrock_model_id=self.bedrock_model.get(),
        )

    def _choose_cookie(self) -> None:
        path = filedialog.askopenfilename(title="Choose a Core-compatible RPC cookie")
        if path:
            self.rpc_cookie_path.set(path)

    def _show_privacy(self) -> None:
        messagebox.showinfo("CoreWarden privacy", PRIVACY_NOTICE)

    def _show_history(self) -> None:
        if self._history_window is not None and self._history_window.winfo_exists():
            self._history_window.deiconify()
            self._history_window.lift()
            self._history_window.focus_force()
            return
        window = tk.Toplevel(self.root)
        self._history_window = window
        window.title("CoreWarden — Sanitized Local History")
        window.geometry("1000x520")
        window.minsize(820, 400)
        window.transient(self.root)
        frame = ttk.Frame(window, padding=12)
        frame.pack(fill=tk.BOTH, expand=True)
        ttk.Label(
            frame,
            text=(
                f"Sanitized local monitoring history — newest {HISTORY_RETENTION_LIMIT} "
                "events retained"
            ),
            font=("Segoe UI", 11, "bold"),
        ).pack(anchor=tk.W)
        warning = self.service.history_warning()
        if warning:
            ttk.Label(frame, text=warning, foreground="#8a4b00").pack(anchor=tk.W, pady=(4, 0))
        columns = ("time", "event", "state", "reason", "ai", "provider", "result")
        tree = ttk.Treeview(frame, columns=columns, show="headings", height=16)
        headings = {
            "time": "Local date / time",
            "event": "Event",
            "state": "State",
            "reason": "Reason",
            "ai": "AI",
            "provider": "Provider",
            "result": "Result",
        }
        widths = {
            "time": 190,
            "event": 150,
            "state": 85,
            "reason": 300,
            "ai": 45,
            "provider": 175,
            "result": 130,
        }
        for column in columns:
            tree.heading(column, text=headings[column])
            tree.column(column, width=widths[column], minwidth=45, stretch=column == "reason")
        scrollbar = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, pady=(10, 0))
        scrollbar.pack(side=tk.LEFT, fill=tk.Y, pady=(10, 0))
        for event in reversed(self.service.history_events()):
            tree.insert("", tk.END, values=history_row(event))
        actions = ttk.Frame(window, padding=(12, 0, 12, 12))
        actions.pack(fill=tk.X)
        ttk.Button(actions, text="Export JSON…", command=self._export_history_json).pack(
            side=tk.LEFT
        )
        ttk.Button(actions, text="Export CSV…", command=self._export_history_csv).pack(
            side=tk.LEFT, padx=(6, 0)
        )
        ttk.Button(actions, text="Close", command=window.destroy).pack(side=tk.RIGHT)
        window.protocol("WM_DELETE_WINDOW", window.destroy)

    def _export_history_json(self) -> None:
        self._export_history("json")

    def _export_history_csv(self) -> None:
        self._export_history("csv")

    def _export_history(self, extension: str) -> None:
        destination = filedialog.asksaveasfilename(
            parent=self.root,
            title=f"Export sanitized history as {extension.upper()}",
            defaultextension=f".{extension}",
            initialfile=history_export_filename(extension),
            filetypes=[(f"{extension.upper()} files", f"*.{extension}")],
        )
        if not destination:
            return
        try:
            if extension == "json":
                self.service.export_history_json(Path(destination))
            else:
                self.service.export_history_csv(Path(destination))
        except OSError:
            messagebox.showerror(
                "History export failed",
                "CoreWarden could not save the sanitized history export.",
                parent=self.root,
            )
            return
        messagebox.showinfo(
            "History exported",
            "Sanitized local monitoring history was exported successfully.",
            parent=self.root,
        )

    def _refresh_credential_status(self) -> None:
        self.openai_status.set(credential_status_text(self.service.credential_status()))

    def _save_key(self) -> None:
        secret = self.openai_key.get()
        try:
            self.service.save_openai_key(secret)
        except Exception as exc:
            self._show_error(exc, extra_secret=secret)
            return
        self.openai_key.set("")
        self._refresh_credential_status()
        self._update_status("OpenAI credential saved securely")

    def _remove_key(self) -> None:
        if not messagebox.askyesno(
            "Remove saved key", "Remove CoreWarden's OpenAI key from Windows Credential Manager?"
        ):
            return
        try:
            self.service.remove_openai_key()
        except Exception as exc:
            self._show_error(exc)
            return
        self._refresh_credential_status()
        self._update_status("Saved OpenAI credential removed")

    def _test_provider(self) -> None:
        config = self._configuration()
        self._run_async(
            "Testing provider configuration…",
            lambda: self.service.test_provider(config),
            lambda result: self._show_text(result.message),
            config,
            "provider",
        )

    def _test_node(self) -> None:
        config = self._configuration()
        self._run_async(
            "Testing read-only node connection…",
            lambda: self.service.test_node(config),
            lambda result: self._show_text(result.message),
            config,
            "node",
        )

    def _run_diagnosis(self) -> None:
        config = self._configuration()
        self._run_async(
            "Running read-only diagnosis…",
            lambda: self.service.run_diagnosis(config),
            lambda result: self._show_text(format_diagnosis(result)),
            config,
            "diagnosis",
        )

    def _start_monitoring(self) -> None:
        if self._monitor is not None and self._monitor.status.active:
            return
        configuration = self._configuration()
        try:
            self._monitor = self.service.create_monitor(
                configuration,
                interval_seconds=float(self.monitor_interval.get()) * 60,
                status_callback=lambda status: self.root.after(
                    0, lambda current=status: self._show_monitoring_status(current)
                ),
            )
            self._monitor.start()
        except Exception as exc:
            self._show_error(exc, configuration=configuration)

    def _stop_monitoring(self) -> None:
        if self._monitor is not None:
            self._monitor.stop(wait=False)
        if self._tray is not None:
            self._tray.refresh()

    def _show_monitoring_status(self, status: MonitoringStatus) -> None:
        self.monitor_state.set(f"Monitoring: {format_monitoring_state(status)}")
        self.monitor_details.set(
            f"Last check: {format_monitoring_time(status.last_check_at)} | "
            f"Last AI investigation: {format_monitoring_time(status.last_ai_at)} "
            f"({status.last_ai_status})"
        )
        self.start_monitor_button.configure(state=tk.DISABLED if status.active else tk.NORMAL)
        self.stop_monitor_button.configure(state=tk.NORMAL if status.active else tk.DISABLED)
        lines = [
            f"{format_monitoring_time(event.occurred_at)} — {event.message}"
            for event in status.events
        ]
        self.monitor_history.configure(state=tk.NORMAL)
        self.monitor_history.delete("1.0", tk.END)
        self.monitor_history.insert(tk.END, "\n".join(lines) or "No monitoring events yet.")
        self.monitor_history.configure(state=tk.DISABLED)
        if self._tray is not None:
            self._tray.refresh()

    def _close(self) -> None:
        if self._monitoring_active():
            self._hide_to_tray()
            return
        self._quit()

    def _monitoring_active(self) -> bool:
        return self._monitor is not None and self._monitor.status.active

    def _marshal(self, callback: Callable[[], None]) -> None:
        with suppress(tk.TclError):
            self.root.after(0, callback)

    def _ensure_tray(self) -> bool:
        if self._tray is None:
            self._tray = self._tray_factory(
                _asset_path("Sprite128.png"),
                on_open=lambda: self._marshal(self._restore_from_tray),
                on_toggle_monitoring=lambda: self._marshal(self._toggle_monitoring_from_tray),
                on_quit=lambda: self._marshal(self._quit),
                monitoring_active=self._monitoring_active,
            )
        try:
            self._tray.start()
        except Exception:
            self._show_text(
                "Error: CoreWarden could not start the system tray. Monitoring remains visible."
            )
            return False
        return True

    def _hide_to_tray(self) -> None:
        if not self._ensure_tray():
            return
        preferences = self.service.preferences
        if preferences is not None and not preferences.tray_notice_shown():
            messagebox.showinfo(
                "CoreWarden is still monitoring",
                "CoreWarden is still monitoring in the system tray. "
                "Use Quit CoreWarden from the tray menu to stop and exit.",
                parent=self.root,
            )
            preferences.mark_tray_notice_shown()
        self.root.withdraw()

    def _restore_from_tray(self) -> None:
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def _toggle_monitoring_from_tray(self) -> None:
        if self._monitoring_active():
            self._stop_monitoring()
        else:
            self._start_monitoring()
        if self._tray is not None:
            self._tray.refresh()

    def _quit(self) -> None:
        if self._quitting:
            return
        self._quitting = True
        if self._monitor is not None:
            self._monitor.stop(wait=True)
        if self._tray is not None:
            self._tray.stop()
        with suppress(tk.TclError):
            self.root.destroy()

    def _run_async(
        self,
        label: str,
        operation: Callable[[], Any],
        success: Callable[[Any], None],
        configuration: DesktopConfiguration,
        status_target: str,
    ) -> None:
        self._set_busy(True)
        self._update_status(label)

        def work() -> None:
            try:
                result = operation()
            except Exception as exc:
                self.root.after(
                    0,
                    lambda error=exc: self._complete_error(error, configuration, status_target),
                )
            else:
                self.root.after(0, lambda: self._complete_success(result, success, status_target))

        threading.Thread(target=work, daemon=True, name="corewarden-desktop-worker").start()

    def _complete_success(
        self, result: Any, callback: Callable[[Any], None], status_target: str
    ) -> None:
        self._set_busy(False)
        callback(result)
        if status_target == "provider":
            self._provider_test_state = "Ready"
        elif status_target == "node":
            self._node_test_state = "Connected"
        self._update_status("Diagnosis complete" if status_target == "diagnosis" else None)

    def _complete_error(
        self, exc: Exception, configuration: DesktopConfiguration, status_target: str
    ) -> None:
        self._set_busy(False)
        if status_target == "provider":
            self._provider_test_state = "Failed"
        elif status_target == "node":
            self._node_test_state = "Failed"
        self._show_error(exc, configuration=configuration)

    def _show_error(
        self,
        exc: Exception,
        *,
        configuration: DesktopConfiguration | None = None,
        extra_secret: str | None = None,
    ) -> None:
        if isinstance(exc, CoreWardenError):
            message = str(exc)
        else:
            message = "CoreWarden failed unexpectedly. Check the configuration and try again."
        secrets = [extra_secret]
        if configuration is not None:
            secrets.extend([configuration.rpc_user, configuration.rpc_password])
        safe = SecretRedactor.from_values(*secrets).text(message)
        self._show_text(f"Error: {safe}")
        self._update_status("Action failed")

    def _update_status(self, activity: str | None = None) -> None:
        self.status.set(format_status(self._provider_test_state, self._node_test_state, activity))

    def _show_text(self, text: str) -> None:
        self.output.configure(state=tk.NORMAL)
        self.output.delete("1.0", tk.END)
        self.output.insert(tk.END, text)
        self.output.configure(state=tk.DISABLED)

    def _set_busy(self, busy: bool) -> None:
        state = tk.DISABLED if busy else tk.NORMAL
        for widget in self._busy_widgets:
            widget.configure(state=state)


def main() -> None:
    root = tk.Tk()
    CoreWardenDesktop(root)
    root.mainloop()


if __name__ == "__main__":
    main()
