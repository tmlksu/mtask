"""User config: stored at ~/.config/mtask/config.toml (override with MTASK_CONFIG_DIR).

Schema:
    [user]
    name = "alice"          # default reporter (起票者)

    [current]
    sheet = "myproj"        # currently selected project slug

    [sheets]
    myproj = "<spreadsheet-id>"
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any

import tomli_w


def config_dir() -> Path:
    return Path(os.environ.get("MTASK_CONFIG_DIR", Path.home() / ".config" / "mtask"))


def config_path() -> Path:
    return config_dir() / "config.toml"


def oauth_client_path() -> Path:
    """OAuth client-secret JSON (Desktop app type), provided by the user."""
    return config_dir() / "oauth_client.json"


def authorized_user_path() -> Path:
    """Cached OAuth token, written after the browser flow."""
    return config_dir() / "authorized_user.json"


def service_account_path() -> Path:
    return config_dir() / "service_account.json"


def load() -> dict[str, Any]:
    path = config_path()
    if path.exists():
        with open(path, "rb") as f:
            return tomllib.load(f)
    return {}


def save(data: dict[str, Any]) -> None:
    config_dir().mkdir(parents=True, exist_ok=True)
    with open(config_path(), "wb") as f:
        tomli_w.dump(data, f)


# --- convenience accessors -------------------------------------------------

def get_auth_method() -> str:
    """'oauth' (default) or 'service_account'."""
    return load().get("auth", {}).get("method", "oauth")


def set_auth_method(method: str) -> None:
    if method not in ("oauth", "service_account"):
        raise ValueError("method must be 'oauth' or 'service_account'")
    data = load()
    data.setdefault("auth", {})["method"] = method
    save(data)


def get_auth_port() -> int:
    """Port for the OAuth local-server flow. 0 = OS-chosen ephemeral port."""
    return int(load().get("auth", {}).get("port", 0))


def set_auth_port(port: int) -> None:
    if not 0 <= port <= 65535:
        raise ValueError("port must be between 0 and 65535 (0 = ephemeral)")
    data = load()
    data.setdefault("auth", {})["port"] = port
    save(data)


def get_auth_path() -> str:
    """Path of the OAuth redirect endpoint, e.g. '/' or '/oauth2callback'."""
    return str(load().get("auth", {}).get("path", "/"))


def normalize_auth_path(path: str) -> str:
    path = path.strip()
    if not path:
        path = "/"
    if not path.startswith("/"):
        path = "/" + path
    if any(c in path for c in " ?#"):
        raise ValueError("path must not contain spaces, '?' or '#'")
    return path


def set_auth_path(path: str) -> None:
    path = normalize_auth_path(path)
    data = load()
    data.setdefault("auth", {})["path"] = path
    save(data)


def get_user() -> str | None:
    return load().get("user", {}).get("name")


def set_user(name: str) -> None:
    data = load()
    data.setdefault("user", {})["name"] = name
    save(data)


def get_sheets() -> dict[str, str]:
    return load().get("sheets", {})


def add_sheet(slug: str, spreadsheet_id: str) -> None:
    data = load()
    data.setdefault("sheets", {})[slug] = spreadsheet_id
    # auto-select if it's the first one
    data.setdefault("current", {}).setdefault("sheet", slug)
    save(data)


def use_sheet(slug: str) -> None:
    data = load()
    if slug not in data.get("sheets", {}):
        raise KeyError(slug)
    data.setdefault("current", {})["sheet"] = slug
    save(data)


def current_sheet() -> str | None:
    return load().get("current", {}).get("sheet")


def resolve_spreadsheet_id(slug: str | None) -> tuple[str, str]:
    """Return (slug, spreadsheet_id) for the given slug or the current one."""
    sheets = get_sheets()
    slug = slug or current_sheet()
    if not slug:
        raise LookupError("no sheet selected; run `mtask sheet add <slug> <id>` first")
    if slug not in sheets:
        raise LookupError(f"unknown sheet '{slug}'; known: {', '.join(sheets) or '(none)'}")
    return slug, sheets[slug]
