"""Google Drive access, fenced to a single folder tree."""

from __future__ import annotations

import io
import time
from pathlib import Path
from typing import Any, Iterable

from .config import SCOPES, Config, parse_id

FOLDER_MIME = "application/vnd.google-apps.folder"
SHORTCUT_MIME = "application/vnd.google-apps.shortcut"

FIELDS = (
    "id, name, mimeType, parents, size, modifiedTime, createdTime, "
    "trashed, webViewLink, owners(emailAddress), md5Checksum"
)

# How Google-native files come out as text, and what we send back in.
GOOGLE_TEXT_FORMATS: dict[str, tuple[str, ...]] = {
    "application/vnd.google-apps.document": ("text/markdown", "text/plain"),
    "application/vnd.google-apps.spreadsheet": ("text/csv",),
    "application/vnd.google-apps.presentation": ("text/plain",),
    "application/vnd.google-apps.script": ("application/vnd.google-apps.script+json",),
}

# Max depth when walking parents looking for the root folder.
MAX_PARENT_DEPTH = 64
# How long a cached folder tree stays fresh.
TREE_TTL_SECONDS = 60.0


class DriveError(Exception):
    """Anything the caller did wrong, reported back as a tool error."""


class OutOfScopeError(DriveError):
    """The target is not inside the folder this server is allowed to touch."""


class ReadOnlyError(DriveError):
    """A write was attempted while the server is in read-only mode."""


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


class DriveClient:
    """A thin Drive wrapper where every file id is checked against the root folder."""

    def __init__(self, config: Config, service: Any | None = None) -> None:
        self.config = config
        self._service = service
        self._parent_cache: dict[str, list[str]] = {}
        self._tree: set[str] | None = None
        self._tree_at = 0.0

    # ---------------------------------------------------------------- auth

    @property
    def service(self) -> Any:
        if self._service is None:
            self._service = self._build_service()
        return self._service

    def _build_service(self) -> Any:
        import json

        from googleapiclient.discovery import build

        creds_path = self.config.credentials_path
        if not creds_path.exists():
            raise DriveError(
                f"No Google credentials at {creds_path}. Download an OAuth desktop "
                "client (or a service account key) from the Google Cloud console and "
                "save it there, or set GDRIVE_CREDENTIALS_PATH."
            )

        raw = json.loads(creds_path.read_text())
        if raw.get("type") == "service_account":
            from google.oauth2 import service_account

            creds = service_account.Credentials.from_service_account_file(
                str(creds_path), scopes=SCOPES
            )
        else:
            creds = self._oauth_credentials(creds_path)

        return build("drive", "v3", credentials=creds, cache_discovery=False)

    def _oauth_credentials(self, creds_path: Path) -> Any:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow

        token_path = self.config.token_path
        creds = None
        if token_path.exists():
            creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

        if creds and creds.valid:
            return creds

        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(str(creds_path), SCOPES)
            creds = flow.run_local_server(port=0)

        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text(creds.to_json())
        token_path.chmod(0o600)
        return creds

    # --------------------------------------------------------------- guards

    def _require_writable(self) -> None:
        if self.config.read_only:
            raise ReadOnlyError(
                "Server is in read-only mode (GDRIVE_READ_ONLY). Unset it to make changes."
            )

    def _parents_of(self, file_id: str) -> list[str]:
        if file_id not in self._parent_cache:
            meta = (
                self.service.files()
                .get(fileId=file_id, fields="parents", supportsAllDrives=True)
                .execute()
            )
            self._parent_cache[file_id] = list(meta.get("parents") or [])
        return self._parent_cache[file_id]

    def assert_in_scope(self, file_id: str) -> str:
        """Raise unless file_id is the root folder or lives somewhere under it."""
        file_id = parse_id(file_id)
        root = self.config.root_folder_id
        if file_id == root:
            return file_id

        seen: set[str] = set()
        frontier = [file_id]
        for _ in range(MAX_PARENT_DEPTH):
            if not frontier:
                break
            nxt: list[str] = []
            for node in frontier:
                if node in seen:
                    continue
                seen.add(node)
                for parent in self._parents_of(node):
                    if parent == root:
                        return file_id
                    nxt.append(parent)
            frontier = nxt

        raise OutOfScopeError(
            f"{file_id} is not inside the allowed folder ({root}). This server only "
            "touches that folder and its subfolders."
        )

    def folder_tree(self, refresh: bool = False) -> set[str]:
        """Ids of the root folder and every folder beneath it."""
        now = time.monotonic()
        if not refresh and self._tree is not None and now - self._tree_at < TREE_TTL_SECONDS:
            return self._tree

        root = self.config.root_folder_id
        found = {root}
        frontier = [root]
        while frontier:
            batch, frontier = frontier, []
            for folder_id in batch:
                for child in self._raw_list(
                    f"'{_escape(folder_id)}' in parents and mimeType = '{FOLDER_MIME}' "
                    "and trashed = false",
                    fields="files(id)",
                ):
                    if child["id"] not in found:
                        found.add(child["id"])
                        frontier.append(child["id"])

        self._tree = found
        self._tree_at = now
        return found

    def _invalidate(self) -> None:
        self._tree = None
        self._parent_cache.clear()

    # ----------------------------------------------------------- read paths

    def _raw_list(self, query: str, fields: str = f"files({FIELDS})", limit: int = 1000) -> list[dict]:
        files: list[dict] = []
        page_token = None
        while True:
            response = (
                self.service.files()
                .list(
                    q=query,
                    fields=f"nextPageToken, {fields}",
                    pageSize=min(1000, max(1, limit - len(files))),
                    pageToken=page_token,
                    supportsAllDrives=True,
                    includeItemsFromAllDrives=True,
                    orderBy="folder,name",
                )
                .execute()
            )
            files.extend(response.get("files", []))
            page_token = response.get("nextPageToken")
            if not page_token or len(files) >= limit:
                break
        return files[:limit]

    def list_folder(
        self, folder_id: str | None, include_trashed: bool = False, limit: int = 200
    ) -> list[dict]:
        target = self.assert_in_scope(folder_id or self.config.root_folder_id)
        clauses = [f"'{_escape(target)}' in parents"]
        if not include_trashed:
            clauses.append("trashed = false")
        return self._raw_list(" and ".join(clauses), limit=limit)

    def search(self, text: str, limit: int = 50) -> list[dict]:
        """Full-text search, then drop anything outside the root folder tree."""
        escaped = _escape(text)
        query = (
            f"(fullText contains '{escaped}' or name contains '{escaped}') "
            "and trashed = false"
        )
        tree = self.folder_tree()
        hits: list[dict] = []
        for item in self._raw_list(query, limit=max(limit * 5, 100)):
            if tree.intersection(item.get("parents") or []):
                hits.append(item)
                if len(hits) >= limit:
                    break
        return hits

    def metadata(self, file_id: str) -> dict:
        file_id = self.assert_in_scope(file_id)
        return (
            self.service.files()
            .get(fileId=file_id, fields=FIELDS, supportsAllDrives=True)
            .execute()
        )

    def read_text(self, file_id: str) -> tuple[str, dict]:
        """Return (text, metadata). Google-native files are exported, others downloaded."""
        meta = self.metadata(file_id)
        mime = meta.get("mimeType", "")
        if mime == FOLDER_MIME:
            raise DriveError(f"{meta.get('name')} is a folder, not a file.")

        data = self._download_bytes(meta["id"], mime)
        try:
            return data.decode("utf-8"), meta
        except UnicodeDecodeError as exc:
            raise DriveError(
                f"{meta.get('name')} ({mime}) is not UTF-8 text. "
                "Use download_file to save it to disk instead."
            ) from exc

    def _export(self, file_id: str, mime: str) -> bytes:
        """Google-native file to bytes, trying each text format we know."""
        last_error: Exception | None = None
        for export_mime in GOOGLE_TEXT_FORMATS[mime]:
            try:
                return (
                    self.service.files()
                    .export(fileId=file_id, mimeType=export_mime)
                    .execute()
                )
            except Exception as exc:  # the first choice may be unsupported
                last_error = exc
        raise DriveError(f"Could not export {file_id}: {last_error}")

    def _download_bytes(self, file_id: str, mime: str) -> bytes:
        if mime in GOOGLE_TEXT_FORMATS:
            return self._export(file_id, mime)
        return (
            self.service.files()
            .get_media(fileId=file_id, supportsAllDrives=True)
            .execute()
        )

    def download_to_path(self, file_id: str, destination: Path) -> dict:
        """Save a file to disk, streaming binaries so large ones stay off the heap."""
        from googleapiclient.http import MediaIoBaseDownload

        meta = self.metadata(file_id)
        mime = meta.get("mimeType", "")
        if mime == FOLDER_MIME:
            raise DriveError(f"{meta.get('name')} is a folder, not a file.")

        destination = destination.expanduser()
        destination.parent.mkdir(parents=True, exist_ok=True)

        if mime in GOOGLE_TEXT_FORMATS:
            data = self._export(meta["id"], mime)
            destination.write_bytes(data)
            written = len(data)
        else:
            request = self.service.files().get_media(
                fileId=meta["id"], supportsAllDrives=True
            )
            buffer = io.BytesIO()
            downloader = MediaIoBaseDownload(buffer, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()
            destination.write_bytes(buffer.getvalue())
            written = buffer.tell()

        return {"path": str(destination), "bytes": written, "name": meta.get("name")}

    def revisions(self, file_id: str, limit: int = 20) -> list[dict]:
        file_id = self.assert_in_scope(file_id)
        response = (
            self.service.revisions()
            .list(
                fileId=file_id,
                fields="revisions(id, modifiedTime, size, lastModifyingUser(emailAddress))",
                pageSize=limit,
            )
            .execute()
        )
        return response.get("revisions", [])

    # ---------------------------------------------------------- write paths

    def _media(self, content: str, mime_type: str) -> Any:
        from googleapiclient.http import MediaIoBaseUpload

        return MediaIoBaseUpload(
            io.BytesIO(content.encode("utf-8")), mimetype=mime_type, resumable=False
        )

    @staticmethod
    def _upload_mime(target_mime: str, fallback: str) -> str:
        """The mime we send bytes as. Google-native targets take text and convert."""
        if target_mime in GOOGLE_TEXT_FORMATS:
            return GOOGLE_TEXT_FORMATS[target_mime][0]
        return fallback

    def create_file(
        self,
        name: str,
        content: str = "",
        parent_id: str | None = None,
        mime_type: str | None = None,
    ) -> dict:
        self._require_writable()
        parent = self.assert_in_scope(parent_id or self.config.root_folder_id)
        body = {"name": name, "parents": [parent]}
        if mime_type:
            body["mimeType"] = mime_type

        upload_mime = self._upload_mime(mime_type or "", "text/plain")
        created = (
            self.service.files()
            .create(
                body=body,
                media_body=self._media(content, upload_mime),
                fields=FIELDS,
                supportsAllDrives=True,
            )
            .execute()
        )
        self._invalidate()
        return created

    def create_folder(self, name: str, parent_id: str | None = None) -> dict:
        self._require_writable()
        parent = self.assert_in_scope(parent_id or self.config.root_folder_id)
        created = (
            self.service.files()
            .create(
                body={"name": name, "mimeType": FOLDER_MIME, "parents": [parent]},
                fields=FIELDS,
                supportsAllDrives=True,
            )
            .execute()
        )
        self._invalidate()
        return created

    def write_text(self, file_id: str, content: str) -> dict:
        """Replace a file's entire contents."""
        self._require_writable()
        meta = self.metadata(file_id)
        if meta.get("mimeType") == FOLDER_MIME:
            raise DriveError(f"{meta.get('name')} is a folder; it has no contents to write.")
        upload_mime = self._upload_mime(meta.get("mimeType", ""), "text/plain")
        return (
            self.service.files()
            .update(
                fileId=meta["id"],
                media_body=self._media(content, upload_mime),
                fields=FIELDS,
                supportsAllDrives=True,
            )
            .execute()
        )

    def replace_in_file(
        self, file_id: str, old_text: str, new_text: str, expect: int | None = 1
    ) -> dict:
        """Swap old_text for new_text, failing loudly on a surprising match count."""
        self._require_writable()
        if not old_text:
            raise DriveError("old_text must not be empty.")
        current, meta = self.read_text(file_id)
        found = current.count(old_text)
        if found == 0:
            raise DriveError(f"old_text does not appear in {meta.get('name')}.")
        if expect is not None and found != expect:
            raise DriveError(
                f"old_text appears {found} times in {meta.get('name')}, expected {expect}. "
                "Pass a longer, unique old_text or set expect to the real count."
            )
        updated = self.write_text(meta["id"], current.replace(old_text, new_text))
        return {**updated, "replacements": found}

    def rename(self, file_id: str, new_name: str) -> dict:
        self._require_writable()
        target = self.assert_in_scope(file_id)
        return (
            self.service.files()
            .update(
                fileId=target, body={"name": new_name}, fields=FIELDS, supportsAllDrives=True
            )
            .execute()
        )

    def move(self, file_id: str, new_parent_id: str) -> dict:
        self._require_writable()
        target = self.assert_in_scope(file_id)
        destination = self.assert_in_scope(new_parent_id)
        current_parents = self._parents_of(target)
        moved = (
            self.service.files()
            .update(
                fileId=target,
                addParents=destination,
                removeParents=",".join(current_parents) if current_parents else None,
                fields=FIELDS,
                supportsAllDrives=True,
            )
            .execute()
        )
        self._invalidate()
        return moved

    def copy(
        self, file_id: str, new_name: str | None = None, parent_id: str | None = None
    ) -> dict:
        self._require_writable()
        target = self.assert_in_scope(file_id)
        destination = self.assert_in_scope(parent_id or self.config.root_folder_id)
        body: dict[str, Any] = {"parents": [destination]}
        if new_name:
            body["name"] = new_name
        copied = (
            self.service.files()
            .copy(fileId=target, body=body, fields=FIELDS, supportsAllDrives=True)
            .execute()
        )
        self._invalidate()
        return copied

    def trash(self, file_id: str) -> dict:
        self._require_writable()
        target = self.assert_in_scope(file_id)
        trashed = (
            self.service.files()
            .update(
                fileId=target, body={"trashed": True}, fields=FIELDS, supportsAllDrives=True
            )
            .execute()
        )
        self._invalidate()
        return trashed

    def untrash(self, file_id: str) -> dict:
        self._require_writable()
        target = self.assert_in_scope(file_id)
        restored = (
            self.service.files()
            .update(
                fileId=target, body={"trashed": False}, fields=FIELDS, supportsAllDrives=True
            )
            .execute()
        )
        self._invalidate()
        return restored

    def delete_forever(self, file_id: str) -> dict:
        self._require_writable()
        self.assert_in_scope(file_id)
        if not self.config.allow_permanent_delete:
            raise DriveError(
                "Permanent delete is off. The file can be moved to the trash instead, or "
                "set GDRIVE_ALLOW_PERMANENT_DELETE=true to enable it."
            )
        meta = self.metadata(file_id)
        self.service.files().delete(fileId=meta["id"], supportsAllDrives=True).execute()
        self._invalidate()
        return {"id": meta["id"], "name": meta.get("name"), "deleted": "permanently"}
