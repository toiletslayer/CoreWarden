"""Errors with safe, user-facing messages."""


class CoreWardenError(Exception):
    """Base class for expected CoreWarden failures."""


class ConfigurationError(CoreWardenError):
    """Raised when environment configuration is missing or invalid."""


class ProviderError(CoreWardenError):
    """Raised when a model provider fails without exposing provider details or secrets."""


class CredentialStorageError(CoreWardenError):
    """Raised when an OS-backed credential cannot be stored or retrieved safely."""


class RpcTransportError(CoreWardenError):
    """Raised when the RPC endpoint cannot be reached or decoded."""


class RpcResponseError(CoreWardenError):
    """Raised when the node returns a JSON-RPC error."""

    def __init__(self, method: str, code: int | None, message: str) -> None:
        self.method = method
        self.code = code
        self.rpc_message = message
        super().__init__(f"RPC method {method!r} failed (code {code}): {message}")
