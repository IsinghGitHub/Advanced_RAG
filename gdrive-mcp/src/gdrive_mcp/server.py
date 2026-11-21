"""MCP tools over a single Google Drive folder."""

from __future__ import annotations

import functools
from pathlib import Path
from typing import Annotated, Any, Callable

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from . import __version__
from .config import Config
from .drive import DriveClient, DriveError

INSTRUCTIONS = """\
Read, create, edit, move and delete files in one Google Drive folder and its
subfolders. Anything outside that folder is rejected.

Call drive_info first to see which folder is in play. Deletes move files to the
Drive trash by default and can be undone with restore_file.
"""

mcp = MCPServer(name="gdrive", version=__version__, instructions=INSTRUCTIONS)

_config = Config.from_env()
_client: DriveClient | None = None

FileId = Annotated[str, Field(description="Drive file id, or any Drive URL for the file.")]
ParentId = Annotated[
    str | None,
    Field(description="Folder id. Defaults to the root folder this server is scoped to."),
]


def get_client() -> DriveClient:
    global _client
    if _client is None:
        _client = DriveClient(_config)
    return _client


def set_client(client: DriveClient | None) -> None:
    """Used by tests to inject a fake Drive."""
    global _client
    _client = client


def tool(fn: Callable) -> Callable:
    """Register a tool, turning our own errors into clean MCP errors."""

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except DriveError as exc:
            raise ToolError(str(exc)) from exc

    return mcp.tool()(wrapper)


def _summary(meta: dict) -> dict:
    """Trim a Drive file resource down to what is worth reading."""
    keep = ("id", "name", "mimeType", "size", "modifiedTime", "trashed", "webViewLink")
    out = {key: meta[key] for key in keep if key in meta}
    if meta.get("parents"):
        out["parent_id"] = meta["parents"][0]
    if "replacements" in meta:
        out["replacements"] = meta["replacements"]
    return out


# ----------------------------------------------------------------- reading


@tool
def drive_info() -> dict:
    """Show which Drive folder this server is scoped to and how it is configured."""
    client = get_client()
    return {
        "root_folder": _summary(client.metadata(_config.root_folder_id)),
        "read_only": _config.read_only,
        "permanent_delete_enabled": _config.allow_permanent_delete,
        "credentials_path": str(_config.credentials_path),
    }


@tool
def list_files(
    folder_id: ParentId = None,
    include_trashed: Annotated[
        bool, Field(description="Also list items already in the trash.")
    ] = False,
    limit: Annotated[int, Field(description="Maximum entries to return.", ge=1)] = 200,
) -> dict:
    """List the files and folders directly inside a folder."""
    files = get_client().list_folder(folder_id, include_trashed=include_trashed, limit=limit)
    return {"count": len(files), "files": [_summary(f) for f in files]}


@tool
def search_files(
    text: Annotated[str, Field(description="Words to look for in file names or contents.")],
    limit: Annotated[int, Field(description="Maximum matches to return.", ge=1)] = 50,
) -> dict:
    """Search names and contents within the allowed folder tree."""
    files = get_client().search(text, limit=limit)
    return {"count": len(files), "files": [_summary(f) for f in files]}


@tool
def get_file_metadata(file_id: FileId) -> dict:
    """Get full Drive metadata for one file or folder."""
    return get_client().metadata(file_id)


@tool
def read_file(
    file_id: FileId,
    max_chars: Annotated[
        int, Field(description="Truncate the returned text at this many characters.", ge=1)
    ] = 200_000,
) -> dict:
    """Read a file as text. Google Docs, Sheets and Slides are exported automatically."""
    text, meta = get_client().read_text(file_id)
    return {
        "file": _summary(meta),
        "truncated": len(text) > max_chars,
        "content": text[:max_chars],
    }


@tool
def download_file(
    file_id: FileId,
    destination_path: Annotated[str, Field(description="Local path to write the file to.")],
) -> dict:
    """Save a file to local disk. Use this for images, PDFs and other binaries."""
    return get_client().download_to_path(file_id, Path(destination_path))


@tool
def list_revisions(
    file_id: FileId,
    limit: Annotated[int, Field(description="Maximum revisions to return.", ge=1)] = 20,
) -> dict:
    """List stored revisions of a file. Useful for checking what an edit changed."""
    revisions = get_client().revisions(file_id, limit=limit)
    return {"count": len(revisions), "revisions": revisions}


# ----------------------------------------------------------------- writing


@tool
def create_file(
    name: Annotated[str, Field(description="File name, including any extension.")],
    content: Annotated[str, Field(description="Initial text contents.")] = "",
    parent_id: ParentId = None,
    mime_type: Annotated[
        str | None,
        Field(
            description=(
                "Drive mime type. Pass application/vnd.google-apps.document for a "
                "Google Doc or application/vnd.google-apps.spreadsheet for a Sheet. "
                "Omit for a plain text file."
            )
        ),
    ] = None,
) -> dict:
    """Create a new file in the allowed folder tree."""
    return _summary(get_client().create_file(name, content, parent_id, mime_type))


@tool
def create_folder(
    name: Annotated[str, Field(description="Folder name.")],
    parent_id: ParentId = None,
) -> dict:
    """Create a subfolder."""
    return _summary(get_client().create_folder(name, parent_id))


@tool
def update_file(
    file_id: FileId,
    content: Annotated[str, Field(description="The new full contents of the file.")],
) -> dict:
    """Replace a file's entire contents. Use edit_file for a smaller change."""
    return _summary(get_client().write_text(file_id, content))


@tool
def edit_file(
    file_id: FileId,
    old_text: Annotated[
        str, Field(description="Exact text to find. Must appear expected_count times.")
    ],
    new_text: Annotated[
        str, Field(description="Text to put in its place. An empty string deletes it.")
    ],
    expected_count: Annotated[
        int,
        Field(description="How many matches you expect. Guards against a wider edit.", ge=1),
    ] = 1,
) -> dict:
    """Change part of a file by replacing exact text, leaving the rest alone."""
    return _summary(
        get_client().replace_in_file(file_id, old_text, new_text, expected_count)
    )


@tool
def rename_file(
    file_id: FileId,
    new_name: Annotated[str, Field(description="The new name.")],
) -> dict:
    """Rename a file or folder."""
    return _summary(get_client().rename(file_id, new_name))


@tool
def move_file(
    file_id: FileId,
    new_parent_id: Annotated[
        str, Field(description="Destination folder id, which must also be inside the tree.")
    ],
) -> dict:
    """Move a file or folder to another folder inside the allowed tree."""
    return _summary(get_client().move(file_id, new_parent_id))


@tool
def copy_file(
    file_id: FileId,
    new_name: Annotated[
        str | None, Field(description="Name for the copy. Defaults to Drive's own naming.")
    ] = None,
    parent_id: ParentId = None,
) -> dict:
    """Copy a file."""
    return _summary(get_client().copy(file_id, new_name, parent_id))


# ---------------------------------------------------------------- deleting


@tool
def delete_file(
    file_id: FileId,
    permanent: Annotated[
        bool,
        Field(
            description=(
                "Delete beyond recovery. Requires GDRIVE_ALLOW_PERMANENT_DELETE=true "
                "and cannot be undone. Leave false to move the file to the trash."
            )
        ),
    ] = False,
) -> dict:
    """Move a file to the Drive trash, or delete it for good."""
    client = get_client()
    if permanent:
        return client.delete_forever(file_id)
    return {**_summary(client.trash(file_id)), "deleted": "to trash"}


@tool
def restore_file(file_id: FileId) -> dict:
    """Restore a file from the Drive trash."""
    return _summary(get_client().untrash(file_id))


def main() -> None:
    mcp.run()
