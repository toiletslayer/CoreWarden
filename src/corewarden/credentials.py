"""OS-backed credential storage for the Windows desktop application."""

from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from dataclasses import dataclass, field
from typing import Protocol

from corewarden.errors import CredentialStorageError

OPENAI_CREDENTIAL_TARGET = "CoreWarden/OpenAI"
OPENAI_CREDENTIAL_USERNAME = "OPENAI_API_KEY"

_CRED_TYPE_GENERIC = 1
_CRED_PERSIST_LOCAL_MACHINE = 2
_ERROR_NOT_FOUND = 1168
_MAX_CREDENTIAL_BYTES = 2560


class CredentialStore(Protocol):
    """Store application secrets without exposing their values."""

    def get_secret(self, target: str, username: str) -> str | None: ...

    def set_secret(self, target: str, username: str, secret: str) -> None: ...

    def delete_secret(self, target: str, username: str) -> None: ...


class _CREDENTIALW(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", wintypes.FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


class _WindowsCredentialApi:
    """Minimal wrapper around Advapi32's generic credential functions."""

    def __init__(self) -> None:
        if os.name != "nt":
            raise CredentialStorageError("Secure saved credentials are available only on Windows.")
        advapi32 = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
        self._read = advapi32.CredReadW
        self._read.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.POINTER(ctypes.POINTER(_CREDENTIALW)),
        ]
        self._read.restype = wintypes.BOOL
        self._write = advapi32.CredWriteW
        self._write.argtypes = [ctypes.POINTER(_CREDENTIALW), wintypes.DWORD]
        self._write.restype = wintypes.BOOL
        self._delete = advapi32.CredDeleteW
        self._delete.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
        self._delete.restype = wintypes.BOOL
        self._free = advapi32.CredFree
        self._free.argtypes = [ctypes.c_void_p]
        self._free.restype = None

    def get(self, target: str) -> tuple[str, str] | None:
        pointer = ctypes.POINTER(_CREDENTIALW)()
        if not self._read(target, _CRED_TYPE_GENERIC, 0, ctypes.byref(pointer)):
            if ctypes.get_last_error() == _ERROR_NOT_FOUND:
                return None
            raise CredentialStorageError("Windows Credential Manager could not read the key.")
        try:
            credential = pointer.contents
            blob = ctypes.string_at(
                credential.CredentialBlob, credential.CredentialBlobSize
            ).decode("utf-16-le")
            return credential.UserName or "", blob
        except (UnicodeDecodeError, ValueError):
            raise CredentialStorageError(
                "Windows Credential Manager returned an invalid CoreWarden key."
            ) from None
        finally:
            self._free(pointer)

    def set(self, target: str, username: str, secret: str) -> None:
        encoded = secret.encode("utf-16-le")
        if len(encoded) > _MAX_CREDENTIAL_BYTES:
            raise CredentialStorageError("The OpenAI key is too long to store securely.")
        blob = (ctypes.c_ubyte * len(encoded)).from_buffer_copy(encoded)
        credential = _CREDENTIALW(
            Type=_CRED_TYPE_GENERIC,
            TargetName=target,
            Comment="CoreWarden OpenAI development/testing key",
            CredentialBlobSize=len(encoded),
            CredentialBlob=ctypes.cast(blob, ctypes.POINTER(ctypes.c_ubyte)),
            Persist=_CRED_PERSIST_LOCAL_MACHINE,
            UserName=username,
        )
        if not self._write(ctypes.byref(credential), 0):
            raise CredentialStorageError("Windows Credential Manager could not save the key.")

    def delete(self, target: str) -> None:
        if self._delete(target, _CRED_TYPE_GENERIC, 0):
            return
        if ctypes.get_last_error() != _ERROR_NOT_FOUND:
            raise CredentialStorageError("Windows Credential Manager could not remove the key.")


class _CredentialApi(Protocol):
    def get(self, target: str) -> tuple[str, str] | None: ...

    def set(self, target: str, username: str, secret: str) -> None: ...

    def delete(self, target: str) -> None: ...


@dataclass(slots=True)
class WindowsCredentialStore:
    """Persist generic credentials in the current user's Windows Credential Manager."""

    _api: _CredentialApi | None = field(default=None, repr=False)

    def _credential_api(self) -> _CredentialApi:
        if self._api is None:
            self._api = _WindowsCredentialApi()
        return self._api

    def get_secret(self, target: str, username: str) -> str | None:
        credential = self._credential_api().get(target)
        if credential is None:
            return None
        stored_username, secret = credential
        if stored_username != username:
            raise CredentialStorageError(
                "Windows Credential Manager returned an unexpected CoreWarden credential."
            )
        return secret

    def set_secret(self, target: str, username: str, secret: str) -> None:
        if not secret:
            raise CredentialStorageError("An OpenAI API key is required before saving.")
        self._credential_api().set(target, username, secret)

    def delete_secret(self, target: str, username: str) -> None:
        del username
        self._credential_api().delete(target)
