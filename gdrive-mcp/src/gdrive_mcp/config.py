"""Settings for the Google Drive MCP server, all sourced from the environment."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

# The folder this server was built for. Override with GDRIVE_ROOT_FOLDER_ID.
DEFAULT_ROOT_FOLDER_ID = "14bBoWM5xwXMjSrG4gJcjcI2BMr6WOT8n"

# Full drive scope: the server has to touch files it did not create, which
# drive.file does not allow.
SCOPES = ["https://www.googleapis.com/auth/drive"]

_FOLDER_URL = re.compile(r"/folders/([A-Za-z0-9_-]+)")
_FILE_URL = re.compile(r"/(?:file|document|spreadsheets|presentation)/d/([A-Za-z0-9_-]+)")
_ID_PARAM = re.compile(r"[?&]id=([A-Za-z0-9_-]+)")


def parse_id(ref: str) -> str:
    """Accept a bare Drive id or any Drive URL and return the id."""
    ref = (ref or "").strip()
    if not ref:
        raise ValueError("empty file or folder reference")
    for pattern in (_FOLDER_URL, _FILE_URL, _ID_PARAM):
        match = pattern.search(ref)
        if match:
            return match.group(1)
    if "/" in ref or ref.startswith("http"):
        raise ValueError(f"could not find a Drive id in {ref!r}")
    return ref


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _config_home() -> Path:
    override = os.environ.get("GDRIVE_MCP_HOME")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".config" / "gdrive-mcp"


@dataclass(frozen=True)
class Config:
    root_folder_id: str
    credentials_path: Path
    token_path: Path
    allow_permanent_delete: bool
    read_only: bool

    @classmethod
    def from_env(cls) -> "Config":
        home = _config_home()
        return cls(
            root_folder_id=parse_id(
                os.environ.get("GDRIVE_ROOT_FOLDER_ID") or DEFAULT_ROOT_FOLDER_ID
            ),
            credentials_path=Path(
                os.environ.get("GDRIVE_CREDENTIALS_PATH") or home / "credentials.json"
            ).expanduser(),
            token_path=Path(
                os.environ.get("GDRIVE_TOKEN_PATH") or home / "token.json"
            ).expanduser(),
            allow_permanent_delete=_env_bool("GDRIVE_ALLOW_PERMANENT_DELETE", False),
            read_only=_env_bool("GDRIVE_READ_ONLY", False),
        )
