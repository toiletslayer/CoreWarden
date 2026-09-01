"""Minimal native Windows desktop interface for CoreWarden."""

from __future__ import annotations

import os
import sys
import threading
import tkinter as tk
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText
from typing import Any

from corewarden.credentials import WindowsCredentialStore
from corewarden.desktop import DesktopConfiguration, DesktopRunResult, DesktopService
from corewarden.diagnostics import SecretRedactor
from corewarden.errors import CoreWardenError

FIRST_RUN_GUIDANCE = (
    "Start here: 1 Choose a provider and enter its settings  →  2 Test Provider  →  "
    "3 Test Node  →  4 Run Diagnosis  →  5 Read the result"
)

PRIVACY_NOTICE = """CoreWarden uses only four read-only node RPC methods:
getblockchaininfo, getnetworkinfo, getpeerinfo, and getchaintips.

Peer-identifying and endpoint data is filtered locally before model access.
OpenAI keys saved by the app use the current user's Windows Credential Manager.
CoreWarden does not intentionally persist raw RPC credentials or peer-identifying observations."""


def format_diagnosis(result: DesktopRunResult) -> str:
    """Render a concise, sanitized diagnosis for the desktop output panel."""
    diagnosis = result.diagnosis
    provider_name = {"openai": "OpenAI", "bedrock": "Amazon Bedrock"}.get(
        result.provider, result.provider
    )
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


def _asset_path(name: str) -> Path:
    frozen_root = getattr(sys, "_MEIPASS", None)
    if frozen_root:
        return Path(frozen_root) / "assets" / name
    return Path(__file__).resolve().parents[2] / "assets" / name


class CoreWardenDesktop:
    """Small tkinter view over the testable DesktopService."""

    def __init__(self, root: tk.Tk, service: DesktopService | None = None) -> None:
        self.root = root
        self.service = service or DesktopService(WindowsCredentialStore())
        self._busy_widgets: list[ttk.Button] = []
        self._brand_image: tk.PhotoImage | None = None

        root.title("CoreWarden — Read-only Node Health")
        root.minsize(760, 720)
        root.geometry("820x800")
        icon = _asset_path("corewarden.ico")
        if icon.exists():
            with suppress(tk.TclError):
                root.iconbitmap(default=str(icon))
        logo = _asset_path("Sprite64.png")
        if logo.exists():
            with suppress(tk.TclError):
                self._brand_image = tk.PhotoImage(file=str(logo))

        self.provider = tk.StringVar(value="openai")
        self.rpc_url = tk.StringVar(
            value=os.environ.get("COREWARDEN_RPC_URL", "http://127.0.0.1:8332")
        )
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
        self.status = tk.StringVar(value="Ready — provider and node not yet tested")

        self._build()
        self._refresh_credential_status()

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

        ttk.Label(
            outer,
            text=FIRST_RUN_GUIDANCE,
            wraplength=780,
            justify=tk.LEFT,
        ).pack(anchor=tk.W, pady=(0, 10))

        provider_frame = ttk.LabelFrame(outer, text="Provider", padding=10)
        provider_frame.pack(fill=tk.X, pady=4)
        ttk.Label(provider_frame, text="Use:").grid(row=0, column=0, sticky=tk.W)
        ttk.Combobox(
            provider_frame,
            textvariable=self.provider,
            values=("openai", "bedrock"),
            state="readonly",
            width=18,
        ).grid(row=0, column=1, sticky=tk.W, padx=8)

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

        openai_frame = ttk.LabelFrame(outer, text="OpenAI", padding=10)
        openai_frame.pack(fill=tk.X, pady=4)
        openai_frame.columnconfigure(1, weight=1)
        ttk.Label(openai_frame, text="API key").grid(row=0, column=0, sticky=tk.W)
        ttk.Entry(openai_frame, textvariable=self.openai_key, show="•").grid(
            row=0, column=1, sticky=tk.EW, padx=8
        )
        ttk.Button(openai_frame, text="Save securely", command=self._save_key).grid(row=0, column=2)
        ttk.Button(openai_frame, text="Remove saved key", command=self._remove_key).grid(
            row=0, column=3, padx=(6, 0)
        )
        ttk.Label(openai_frame, textvariable=self.openai_status).grid(
            row=1, column=0, columnspan=4, sticky=tk.W, pady=(6, 0)
        )

        bedrock_frame = ttk.LabelFrame(outer, text="Amazon Bedrock", padding=10)
        bedrock_frame.pack(fill=tk.X, pady=4)
        bedrock_frame.columnconfigure(1, weight=1)
        self._entry_row(bedrock_frame, 0, "AWS profile", self.aws_profile)
        self._entry_row(bedrock_frame, 1, "AWS region", self.aws_region)
        self._entry_row(bedrock_frame, 2, "Model ID", self.bedrock_model)
        ttk.Label(
            bedrock_frame,
            text="Uses the existing AWS CLI/profile/session; CoreWarden stores no AWS secret.",
        ).grid(row=3, column=0, columnspan=3, sticky=tk.W, pady=(3, 0))

        actions = ttk.Frame(outer)
        actions.pack(fill=tk.X, pady=10)
        self._add_action(actions, "2. Test Provider", self._test_provider)
        self._add_action(actions, "3. Test Node", self._test_node)
        self._add_action(actions, "4. Run Diagnosis", self._run_diagnosis)

        ttk.Label(outer, textvariable=self.status).pack(anchor=tk.W, pady=(0, 6))
        self.output = ScrolledText(outer, wrap=tk.WORD, height=15, font=("Segoe UI", 10))
        self.output.pack(fill=tk.BOTH, expand=True)
        self.output.configure(state=tk.DISABLED)

    @staticmethod
    def _entry_row(
        frame: ttk.LabelFrame,
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

    def _configuration(self) -> DesktopConfiguration:
        return DesktopConfiguration(
            provider=self.provider.get(),
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

    def _refresh_credential_status(self) -> None:
        messages = {
            "saved": "OpenAI key: saved securely in Windows Credential Manager",
            "environment": "OpenAI key: available from OPENAI_API_KEY",
            "missing": "OpenAI key: not configured",
            "unavailable": "OpenAI key: secure storage unavailable",
        }
        self.openai_status.set(messages[self.service.credential_status()])

    def _save_key(self) -> None:
        secret = self.openai_key.get()
        try:
            self.service.save_openai_key(secret)
        except Exception as exc:
            self._show_error(exc, extra_secret=secret)
            return
        self.openai_key.set("")
        self._refresh_credential_status()
        self.status.set("OpenAI key saved securely; its full value will not be shown again.")

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
        self.status.set("Saved OpenAI key removed.")

    def _test_provider(self) -> None:
        config = self._configuration()
        self._run_async(
            "Testing provider configuration…",
            lambda: self.service.test_provider(config),
            lambda result: self._show_text(result.message),
            config,
        )

    def _test_node(self) -> None:
        config = self._configuration()
        self._run_async(
            "Testing read-only node connection…",
            lambda: self.service.test_node(config),
            lambda result: self._show_text(result.message),
            config,
        )

    def _run_diagnosis(self) -> None:
        config = self._configuration()
        self._run_async(
            "Running read-only diagnosis…",
            lambda: self.service.run_diagnosis(config),
            lambda result: self._show_text(format_diagnosis(result)),
            config,
        )

    def _run_async(
        self,
        label: str,
        operation: Callable[[], Any],
        success: Callable[[Any], None],
        configuration: DesktopConfiguration,
    ) -> None:
        self._set_busy(True)
        self.status.set(label)

        def work() -> None:
            try:
                result = operation()
            except Exception as exc:
                self.root.after(0, lambda error=exc: self._complete_error(error, configuration))
            else:
                self.root.after(0, lambda: self._complete_success(result, success))

        threading.Thread(target=work, daemon=True, name="corewarden-desktop-worker").start()

    def _complete_success(self, result: Any, callback: Callable[[Any], None]) -> None:
        self._set_busy(False)
        callback(result)
        self.status.set("Succeeded")

    def _complete_error(self, exc: Exception, configuration: DesktopConfiguration) -> None:
        self._set_busy(False)
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
        self.status.set("Failed")

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
