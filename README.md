# Advanced_RAG

## gdrive-mcp

A local MCP server that creates, reads, edits, moves and deletes files in a
single Google Drive folder
([`14bBoWM5xwXMjSrG4gJcjcI2BMr6WOT8n`](https://drive.google.com/drive/folders/14bBoWM5xwXMjSrG4gJcjcI2BMr6WOT8n)
by default) and refuses to touch anything outside it.

See [`gdrive-mcp/README.md`](gdrive-mcp/README.md) for setup, the tool list and
configuration.

```bash
cd gdrive-mcp
uv venv --python 3.13
uv pip install -e ".[dev]"
uv run pytest
```
