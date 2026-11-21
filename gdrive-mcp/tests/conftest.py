"""An in-memory stand-in for the Drive v3 API, enough for the calls we make."""

from __future__ import annotations

import itertools
import re
from pathlib import Path

import pytest

from gdrive_mcp.config import Config
from gdrive_mcp.drive import FOLDER_MIME, DriveClient

ROOT = "root-folder"


class _Exec:
    def __init__(self, fn):
        self._fn = fn

    def execute(self, **_kwargs):
        return self._fn()


def _media_bytes(media_body) -> bytes:
    if media_body is None:
        return b""
    return media_body.getbytes(0, media_body.size())


class FakeFiles:
    def __init__(self, drive: "FakeDrive"):
        self.drive = drive

    # --- reads

    def get(self, fileId, fields=None, **_):
        return _Exec(lambda: self.drive.meta(fileId))

    def list(self, q="", pageToken=None, **_):
        return _Exec(lambda: {"files": self.drive.query(q)})

    def export(self, fileId, mimeType):
        def run():
            record = self.drive.record(fileId)
            if mimeType not in record.get("exports", ("text/markdown", "text/plain", "text/csv")):
                raise RuntimeError(f"export to {mimeType} unsupported")
            return record["content"]

        return _Exec(run)

    def get_media(self, fileId, **_):
        return _Exec(lambda: self.drive.record(fileId)["content"])

    # --- writes

    def create(self, body=None, media_body=None, **_):
        def run():
            body_ = dict(body or {})
            new_id = f"f{next(self.drive.counter)}"
            self.drive.store[new_id] = {
                "id": new_id,
                "name": body_.get("name", "untitled"),
                "mimeType": body_.get("mimeType", "text/plain"),
                "parents": list(body_.get("parents") or []),
                "trashed": False,
                "content": _media_bytes(media_body),
                "upload_mime": getattr(media_body, "mimetype", lambda: None)()
                if media_body
                else None,
            }
            return self.drive.meta(new_id)

        return _Exec(run)

    def update(self, fileId, body=None, media_body=None, addParents=None, removeParents=None, **_):
        def run():
            record = self.drive.record(fileId)
            record.update(body or {})
            if media_body is not None:
                record["content"] = _media_bytes(media_body)
                record["upload_mime"] = media_body.mimetype()
            if removeParents:
                drop = set(removeParents.split(","))
                record["parents"] = [p for p in record["parents"] if p not in drop]
            if addParents:
                record["parents"] = sorted(set(record["parents"]) | {addParents})
            return self.drive.meta(fileId)

        return _Exec(run)

    def copy(self, fileId, body=None, **_):
        def run():
            source = self.drive.record(fileId)
            new_id = f"f{next(self.drive.counter)}"
            clone = dict(source)
            clone["id"] = new_id
            clone["name"] = (body or {}).get("name", f"Copy of {source['name']}")
            clone["parents"] = list((body or {}).get("parents") or source["parents"])
            self.drive.store[new_id] = clone
            return self.drive.meta(new_id)

        return _Exec(run)

    def delete(self, fileId, **_):
        def run():
            self.drive.record(fileId)
            del self.drive.store[fileId]
            return None

        return _Exec(run)


class FakeRevisions:
    def __init__(self, drive: "FakeDrive"):
        self.drive = drive

    def list(self, fileId, **_):
        return _Exec(
            lambda: {"revisions": self.drive.record(fileId).get("revisions", [])}
        )


class FakeDrive:
    """Holds the file table and answers queries the client actually builds."""

    def __init__(self):
        self.store: dict[str, dict] = {}
        self.counter = itertools.count(1)
        self.deleted: list[str] = []

    def files(self):
        return FakeFiles(self)

    def revisions(self):
        return FakeRevisions(self)

    # --- helpers used by the fakes

    def record(self, file_id: str) -> dict:
        if file_id not in self.store:
            raise RuntimeError(f"File not found: {file_id}")
        return self.store[file_id]

    def meta(self, file_id: str) -> dict:
        record = self.record(file_id)
        return {k: v for k, v in record.items() if k not in {"content", "exports"}}

    def query(self, q: str) -> list[dict]:
        parent = re.search(r"'([^']+)' in parents", q)
        mime = re.search(r"mimeType = '([^']+)'", q)
        text = re.search(r"(?:fullText|name) contains '([^']+)'", q)
        no_trash = "trashed = false" in q

        out = []
        for record in self.store.values():
            if parent and parent.group(1) not in record["parents"]:
                continue
            if mime and record["mimeType"] != mime.group(1):
                continue
            if no_trash and record["trashed"]:
                continue
            if text:
                needle = text.group(1).lower()
                haystack = record["name"].lower() + record["content"].decode(
                    "utf-8", "ignore"
                ).lower()
                if needle not in haystack:
                    continue
            out.append(self.meta(record["id"]))
        return out

    # --- test fixtures build the tree with these

    def add_folder(self, file_id: str, name: str, parent: str | None) -> str:
        self.store[file_id] = {
            "id": file_id,
            "name": name,
            "mimeType": FOLDER_MIME,
            "parents": [parent] if parent else [],
            "trashed": False,
            "content": b"",
        }
        return file_id

    def add_file(
        self,
        file_id: str,
        name: str,
        parent: str,
        content: str = "",
        mime: str = "text/plain",
        exports: tuple[str, ...] | None = None,
    ) -> str:
        self.store[file_id] = {
            "id": file_id,
            "name": name,
            "mimeType": mime,
            "parents": [parent],
            "trashed": False,
            "content": content.encode("utf-8"),
        }
        if exports is not None:
            self.store[file_id]["exports"] = exports
        return file_id


@pytest.fixture
def config(tmp_path: Path) -> Config:
    return Config(
        root_folder_id=ROOT,
        credentials_path=tmp_path / "credentials.json",
        token_path=tmp_path / "token.json",
        allow_permanent_delete=False,
        read_only=False,
    )


@pytest.fixture
def drive() -> FakeDrive:
    """Root folder with a subfolder and a file in each, plus a file outside it."""
    fake = FakeDrive()
    fake.add_folder(ROOT, "Shared Folder", None)
    fake.add_folder("sub", "Notes", ROOT)
    fake.add_folder("outside-folder", "Somewhere Else", None)
    fake.add_file("top", "readme.txt", ROOT, "hello world")
    fake.add_file("nested", "deep.txt", "sub", "nested content")
    fake.add_file("stranger", "private.txt", "outside-folder", "not yours")
    return fake


@pytest.fixture
def client(config: Config, drive: FakeDrive) -> DriveClient:
    return DriveClient(config, service=drive)


@pytest.fixture
def anyio_backend():
    return "asyncio"
