"""The fence: nothing outside the root folder may be read or changed."""

from __future__ import annotations

import dataclasses

import pytest

from gdrive_mcp.config import parse_id
from gdrive_mcp.drive import DriveClient, DriveError, OutOfScopeError, ReadOnlyError

from .conftest import ROOT


def test_root_folder_itself_is_in_scope(client: DriveClient):
    assert client.assert_in_scope(ROOT) == ROOT


def test_direct_child_is_in_scope(client: DriveClient):
    assert client.assert_in_scope("top") == "top"


def test_grandchild_is_in_scope(client: DriveClient):
    assert client.assert_in_scope("nested") == "nested"


def test_file_elsewhere_in_drive_is_rejected(client: DriveClient):
    with pytest.raises(OutOfScopeError):
        client.assert_in_scope("stranger")


def test_a_drive_url_is_accepted_in_place_of_an_id(client: DriveClient):
    url = "https://drive.google.com/file/d/top/view?usp=sharing"
    assert client.assert_in_scope(url) == "top"


@pytest.mark.parametrize(
    "call",
    [
        lambda c: c.metadata("stranger"),
        lambda c: c.read_text("stranger"),
        lambda c: c.write_text("stranger", "overwritten"),
        lambda c: c.replace_in_file("stranger", "not", "very"),
        lambda c: c.rename("stranger", "renamed"),
        lambda c: c.trash("stranger"),
        lambda c: c.delete_forever("stranger"),
        lambda c: c.copy("stranger"),
        lambda c: c.move("stranger", ROOT),
        lambda c: c.list_folder("outside-folder"),
        lambda c: c.revisions("stranger"),
    ],
    ids=[
        "metadata",
        "read",
        "write",
        "edit",
        "rename",
        "trash",
        "delete",
        "copy",
        "move",
        "list",
        "revisions",
    ],
)
def test_every_operation_refuses_an_outside_target(client: DriveClient, call):
    with pytest.raises(OutOfScopeError):
        call(client)


def test_outside_file_is_untouched_after_a_rejected_write(client: DriveClient, drive):
    with pytest.raises(OutOfScopeError):
        client.write_text("stranger", "overwritten")
    assert drive.store["stranger"]["content"] == b"not yours"


def test_move_refuses_a_destination_outside_the_folder(client: DriveClient):
    with pytest.raises(OutOfScopeError):
        client.move("top", "outside-folder")


def test_search_drops_matches_outside_the_folder(client: DriveClient, drive):
    drive.add_file("other-hit", "hello-elsewhere.txt", "outside-folder", "hello world")
    names = {hit["name"] for hit in client.search("hello")}
    assert names == {"readme.txt"}


def test_read_only_mode_blocks_writes_but_allows_reads(config, drive):
    client = DriveClient(dataclasses.replace(config, read_only=True), service=drive)
    assert client.read_text("top")[0] == "hello world"
    with pytest.raises(ReadOnlyError):
        client.write_text("top", "nope")


def test_permanent_delete_is_off_by_default(client: DriveClient, drive):
    with pytest.raises(DriveError, match="Permanent delete is off"):
        client.delete_forever("top")
    assert "top" in drive.store


def test_permanent_delete_works_once_enabled(config, drive):
    client = DriveClient(dataclasses.replace(config, allow_permanent_delete=True), service=drive)
    result = client.delete_forever("top")
    assert result["deleted"] == "permanently"
    assert "top" not in drive.store


def test_parse_id_rejects_a_url_with_no_id():
    with pytest.raises(ValueError):
        parse_id("https://example.com/not-a-drive-link")
