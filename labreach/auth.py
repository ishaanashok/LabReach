"""Gmail app-password storage in the OS keychain (via `keyring`). Never in a file, never logged."""

from __future__ import annotations

import keyring

SERVICE = "labreach-gmail"


class AuthMissing(RuntimeError):
    pass


def store_credentials(address: str, app_password: str) -> None:
    keyring.set_password(SERVICE, address, app_password.replace(" ", ""))   # Google shows it in 4 groups


def get_password(address: str) -> str:
    password = keyring.get_password(SERVICE, address)
    if not password:
        raise AuthMissing(f"no app password stored for {address}; run `labreach auth`")
    return password


def forget(address: str) -> None:
    try:
        keyring.delete_password(SERVICE, address)
    except keyring.errors.PasswordDeleteError:
        pass
