"""Windows DPAPI-backed storage for local Training Camp request credentials."""

from __future__ import annotations

import ctypes
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class CredentialStoreError(RuntimeError):
    """Credential storage failed; exception text never includes secret values."""


def state_directory() -> Path:
    """Use one explicit directory across desktop and background Windows hosts."""
    override = os.environ.get("HTC_STATE_DIR")
    if override:
        path = Path(override).expanduser()
        if not path.is_absolute():
            raise CredentialStoreError("HTC_STATE_DIR must be an absolute path.")
        return path
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        raise CredentialStoreError("LOCALAPPDATA is unavailable.")
    return Path(base) / "HuaweiTrainingCampMCP"


@dataclass(frozen=True)
class StoredCredentials:
    headers: dict[str, str]
    page_url: str | None = None


class CredentialStore:
    """Encrypt credentials for the current Windows user on this computer."""

    def __init__(self, path: str | Path | None = None):
        if path is None:
            path = state_directory() / "credentials.dpapi"
        self.path = Path(path)

    def exists(self) -> bool:
        return self.path.is_file()

    def save(self, credentials: StoredCredentials) -> None:
        allowed_headers = {"Authorization", "x-client-id", "x-version", "Cookie"}
        if (
            not credentials.headers.get("Authorization")
            or not credentials.headers.get("x-client-id")
            or set(credentials.headers) - allowed_headers
        ):
            raise CredentialStoreError("Credential header set is invalid.")
        payload = json.dumps(
            {"headers": credentials.headers, "page_url": credentials.page_url},
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        encrypted = _dpapi_protect(payload)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_name: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb", dir=self.path.parent, prefix=".credentials-", delete=False
            ) as handle:
                temp_name = handle.name
                handle.write(encrypted)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self.path)
        except OSError as exc:
            if temp_name:
                try:
                    os.unlink(temp_name)
                except OSError:
                    pass
            raise CredentialStoreError("Could not save encrypted credentials.") from exc

    def load(self) -> StoredCredentials | None:
        try:
            encrypted = self.path.read_bytes()
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise CredentialStoreError("Could not read encrypted credentials.") from exc
        try:
            payload = json.loads(_dpapi_unprotect(encrypted).decode("utf-8"))
            headers = payload["headers"]
            page_url = payload.get("page_url")
            if not isinstance(headers, dict) or not all(
                isinstance(key, str) and isinstance(value, str)
                for key, value in headers.items()
            ):
                raise ValueError("invalid header map")
            if page_url is not None and not isinstance(page_url, str):
                raise ValueError("invalid page URL")
            if not headers.get("Authorization") or not headers.get("x-client-id"):
                raise ValueError("missing required headers")
            return StoredCredentials(headers=dict(headers), page_url=page_url)
        except Exception as exc:
            raise CredentialStoreError(
                "Encrypted credentials are invalid or unavailable to this Windows user."
            ) from exc

    def clear(self) -> None:
        try:
            self.path.unlink(missing_ok=True)
        except OSError as exc:
            raise CredentialStoreError("Could not remove encrypted credentials.") from exc


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_ulong), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def _make_blob(data: bytes) -> tuple[_DataBlob, Any]:
    buffer = ctypes.create_string_buffer(data, len(data))
    return _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))), buffer


def _dpapi_protect(data: bytes) -> bytes:
    if os.name != "nt":
        raise CredentialStoreError("DPAPI credential storage is available only on Windows.")
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    source, keepalive = _make_blob(data)
    result = _DataBlob()
    protect = crypt32.CryptProtectData
    protect.argtypes = [ctypes.POINTER(_DataBlob), ctypes.c_wchar_p,
                        ctypes.POINTER(_DataBlob), ctypes.c_void_p, ctypes.c_void_p,
                        ctypes.c_ulong, ctypes.POINTER(_DataBlob)]
    protect.restype = ctypes.c_int
    try:
        if not protect(ctypes.byref(source), "Huawei Training Camp MCP", None, None,
                       None, 0x1, ctypes.byref(result)):
            raise CredentialStoreError("Windows could not encrypt credentials with DPAPI.")
        return ctypes.string_at(result.pbData, result.cbData)
    finally:
        if result.pbData:
            kernel32.LocalFree(result.pbData)
        del keepalive


def _dpapi_unprotect(data: bytes) -> bytes:
    if os.name != "nt":
        raise CredentialStoreError("DPAPI credential storage is available only on Windows.")
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    source, keepalive = _make_blob(data)
    result = _DataBlob()
    unprotect = crypt32.CryptUnprotectData
    unprotect.argtypes = [ctypes.POINTER(_DataBlob), ctypes.c_void_p,
                          ctypes.POINTER(_DataBlob), ctypes.c_void_p, ctypes.c_void_p,
                          ctypes.c_ulong, ctypes.POINTER(_DataBlob)]
    unprotect.restype = ctypes.c_int
    try:
        if not unprotect(ctypes.byref(source), None, None, None, None, 0x1,
                         ctypes.byref(result)):
            raise CredentialStoreError("Windows could not decrypt credentials with DPAPI.")
        return ctypes.string_at(result.pbData, result.cbData)
    finally:
        if result.pbData:
            kernel32.LocalFree(result.pbData)
        del keepalive
