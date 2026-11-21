"""Create, read, modify, move and delete behaviour."""

from __future__ import annotations

import pytest

from gdrive_mcp.drive import FOLDER_MIME, DriveClient, DriveError

from .conftest import ROOT

DOC_MIME = "application/vnd.google-apps.document"
SHEET_MIME = "application/vnd.google-apps.spreadsheet"


def test_list_folder_shows_direct_children_only(client: DriveClient):
    names = {item["name"] for item in client.list_folder(None)}
    assert names == {"Notes", "readme.txt"}


def test_list_folder_hides_trashed_items_by_default(client: DriveClient, drive):
    drive.store["top"]["trashed"] = True
    assert [i["name"] for i in client.list_folder(None)] == ["Notes"]
    assert len(client.list_folder(None, include_trashed=True)) == 2


def test_read_text_returns_contents_and_metadata(client: DriveClient):
    text, meta = client.read_text("top")
    assert text == "hello world"
    assert meta["name"] == "readme.txt"


def test_reading_a_folder_is_an_error(client: DriveClient):
    with pytest.raises(DriveError, match="is a folder"):
        client.read_text("sub")


def test_reading_a_binary_file_points_at_download(client: DriveClient, drive):
    drive.store["blob"] = {
        "id": "blob",
        "name": "logo.png",
        "mimeType": "image/png",
        "parents": [ROOT],
        "trashed": False,
        "content": b"\x89PNG\xff\xfe",
    }
    with pytest.raises(DriveError, match="download_file"):
        client.read_text("blob")


def test_create_file_lands_in_the_root_folder(client: DriveClient, drive):
    created = client.create_file("new.txt", "fresh contents")
    assert created["parents"] == [ROOT]
    assert drive.store[created["id"]]["content"] == b"fresh contents"


def test_create_file_can_target_a_subfolder(client: DriveClient):
    created = client.create_file("note.txt", "x", parent_id="sub")
    assert created["parents"] == ["sub"]


def test_create_folder_nests_under_root(client: DriveClient):
    created = client.create_folder("Reports")
    assert created["mimeType"] == FOLDER_MIME
    assert created["parents"] == [ROOT]


def test_update_file_replaces_the_whole_contents(client: DriveClient, drive):
    client.write_text("top", "completely new")
    assert drive.store["top"]["content"] == b"completely new"


def test_edit_file_changes_only_the_matched_text(client: DriveClient, drive):
    client.replace_in_file("top", "world", "there")
    assert drive.store["top"]["content"] == b"hello there"


def test_edit_file_reports_missing_text(client: DriveClient):
    with pytest.raises(DriveError, match="does not appear"):
        client.replace_in_file("top", "absent", "x")


def test_edit_file_refuses_an_ambiguous_match(client: DriveClient, drive):
    drive.store["top"]["content"] = b"a a a"
    with pytest.raises(DriveError, match="appears 3 times"):
        client.replace_in_file("top", "a", "b")


def test_edit_file_allows_a_wide_edit_when_asked_for(client: DriveClient, drive):
    drive.store["top"]["content"] = b"a a a"
    result = client.replace_in_file("top", "a", "b", expect=3)
    assert result["replacements"] == 3
    assert drive.store["top"]["content"] == b"b b b"


def test_edit_file_can_delete_text(client: DriveClient, drive):
    client.replace_in_file("top", " world", "")
    assert drive.store["top"]["content"] == b"hello"


def test_rename_keeps_everything_else(client: DriveClient, drive):
    renamed = client.rename("top", "readme.md")
    assert renamed["name"] == "readme.md"
    assert drive.store["top"]["content"] == b"hello world"


def test_move_reparents_within_the_tree(client: DriveClient, drive):
    client.move("top", "sub")
    assert drive.store["top"]["parents"] == ["sub"]


def test_copy_defaults_to_the_root_folder(client: DriveClient, drive):
    copy = client.copy("nested", new_name="deep-copy.txt")
    assert copy["parents"] == [ROOT]
    assert drive.store[copy["id"]]["content"] == b"nested content"


def test_trash_then_restore_round_trips(client: DriveClient, drive):
    client.trash("top")
    assert drive.store["top"]["trashed"] is True
    client.untrash("top")
    assert drive.store["top"]["trashed"] is False


def test_search_matches_name_and_contents(client: DriveClient):
    assert [hit["id"] for hit in client.search("nested content")] == ["nested"]


def test_folder_tree_includes_nested_folders(client: DriveClient, drive):
    drive.add_folder("deeper", "Archive", "sub")
    assert client.folder_tree(refresh=True) == {ROOT, "sub", "deeper"}


# --- Google-native files


def test_google_doc_is_read_as_markdown(client: DriveClient, drive):
    drive.add_file(
        "doc", "Plan", ROOT, "# Plan\n\nbody", mime=DOC_MIME, exports=("text/markdown",)
    )
    text, _ = client.read_text("doc")
    assert text == "# Plan\n\nbody"


def test_google_doc_export_falls_back_to_plain_text(client: DriveClient, drive):
    drive.add_file("doc", "Plan", ROOT, "plain body", mime=DOC_MIME, exports=("text/plain",))
    assert client.read_text("doc")[0] == "plain body"


def test_sheet_is_read_as_csv(client: DriveClient, drive):
    drive.add_file("sheet", "Data", ROOT, "a,b\n1,2", mime=SHEET_MIME, exports=("text/csv",))
    assert client.read_text("sheet")[0] == "a,b\n1,2"


def test_writing_a_google_doc_uploads_convertible_text(client: DriveClient, drive):
    drive.add_file("doc", "Plan", ROOT, "old", mime=DOC_MIME, exports=("text/markdown",))
    client.write_text("doc", "# New")
    assert drive.store["doc"]["upload_mime"] == "text/markdown"
    assert drive.store["doc"]["mimeType"] == DOC_MIME


def test_creating_a_google_doc_keeps_the_doc_mime_type(client: DriveClient, drive):
    created = client.create_file("Notes", "# Hi", mime_type=DOC_MIME)
    assert created["mimeType"] == DOC_MIME
    assert drive.store[created["id"]]["upload_mime"] == "text/markdown"


def test_plain_files_upload_as_plain_text(client: DriveClient, drive):
    created = client.create_file("x.txt", "hi")
    assert drive.store[created["id"]]["upload_mime"] == "text/plain"


def test_download_writes_a_binary_to_disk(client: DriveClient, drive, tmp_path):
    drive.add_file("doc", "Plan", ROOT, "exported", mime=DOC_MIME, exports=("text/markdown",))
    target = tmp_path / "out" / "plan.md"
    result = client.download_to_path("doc", target)
    assert target.read_text() == "exported"
    assert result["bytes"] == len("exported")
