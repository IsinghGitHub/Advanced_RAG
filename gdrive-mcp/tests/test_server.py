"""The MCP layer: tools are registered, call through, and report errors cleanly."""

from __future__ import annotations

import dataclasses
import json

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from gdrive_mcp import server
from gdrive_mcp.drive import DriveClient

from .conftest import ROOT

EXPECTED_TOOLS = {
    "drive_info",
    "list_files",
    "search_files",
    "get_file_metadata",
    "read_file",
    "download_file",
    "list_revisions",
    "create_file",
    "create_folder",
    "update_file",
    "edit_file",
    "rename_file",
    "move_file",
    "copy_file",
    "delete_file",
    "restore_file",
}


async def call(name: str, arguments: dict | None = None) -> dict:
    """Invoke a tool and return its decoded payload."""
    result = await server.mcp.call_tool(name, arguments or {})
    assert not result.is_error, result.content
    return json.loads(result.content[0].text)


@pytest.fixture
def wired(monkeypatch, config, drive):
    """Point the server module at the fake Drive and the test config."""
    monkeypatch.setattr(server, "_config", config)
    server.set_client(DriveClient(config, service=drive))
    yield drive
    server.set_client(None)


@pytest.mark.anyio
async def test_all_tools_are_registered():
    assert {tool.name for tool in await server.mcp.list_tools()} == EXPECTED_TOOLS


@pytest.mark.anyio
async def test_every_tool_describes_itself_and_its_arguments():
    for tool in await server.mcp.list_tools():
        assert tool.description, f"{tool.name} has no description"
        for arg, schema in tool.input_schema.get("properties", {}).items():
            assert schema.get("description"), f"{tool.name}.{arg} has no description"


@pytest.mark.anyio
async def test_read_file_returns_content(wired):
    result = await call("read_file", {"file_id": "top"})
    assert result["content"] == "hello world"
    assert result["truncated"] is False


@pytest.mark.anyio
async def test_read_file_truncates_on_request(wired):
    result = await call("read_file", {"file_id": "top", "max_chars": 5})
    assert result["content"] == "hello"
    assert result["truncated"] is True


@pytest.mark.anyio
async def test_list_files_defaults_to_the_root_folder(wired):
    assert (await call("list_files"))["count"] == 2


@pytest.mark.anyio
async def test_a_drive_url_works_wherever_an_id_does(wired):
    result = await call(
        "read_file", {"file_id": "https://drive.google.com/file/d/top/view"}
    )
    assert result["content"] == "hello world"


@pytest.mark.anyio
async def test_create_then_edit_through_the_tools(wired):
    created = await call("create_file", {"name": "todo.md", "content": "- one\n- two"})
    edited = await call(
        "edit_file", {"file_id": created["id"], "old_text": "two", "new_text": "three"}
    )
    assert edited["replacements"] == 1
    assert wired.store[created["id"]]["content"] == b"- one\n- three"


@pytest.mark.anyio
async def test_delete_defaults_to_the_trash_and_restores(wired):
    deleted = await call("delete_file", {"file_id": "top"})
    assert deleted["deleted"] == "to trash"
    assert wired.store["top"]["trashed"] is True
    await call("restore_file", {"file_id": "top"})
    assert wired.store["top"]["trashed"] is False


@pytest.mark.anyio
async def test_out_of_scope_target_surfaces_as_a_tool_error(wired):
    with pytest.raises(ToolError, match="not inside the allowed folder"):
        await server.mcp.call_tool("read_file", {"file_id": "stranger"})


@pytest.mark.anyio
async def test_permanent_delete_is_refused_with_a_usable_message(wired):
    with pytest.raises(ToolError, match="GDRIVE_ALLOW_PERMANENT_DELETE"):
        await server.mcp.call_tool("delete_file", {"file_id": "top", "permanent": True})
    assert "top" in wired.store


@pytest.mark.anyio
async def test_drive_info_reports_the_folder_in_scope(wired):
    result = await call("drive_info")
    assert result["root_folder"]["id"] == ROOT
    assert result["read_only"] is False
    assert result["permanent_delete_enabled"] is False


@pytest.mark.anyio
async def test_read_only_mode_is_reported_and_enforced(monkeypatch, config, drive):
    read_only = dataclasses.replace(config, read_only=True)
    monkeypatch.setattr(server, "_config", read_only)
    server.set_client(DriveClient(read_only, service=drive))
    try:
        assert (await call("drive_info"))["read_only"] is True
        with pytest.raises(ToolError, match="read-only"):
            await server.mcp.call_tool("update_file", {"file_id": "top", "content": "x"})
    finally:
        server.set_client(None)
