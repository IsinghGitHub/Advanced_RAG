# gdrive-mcp

A local MCP server that reads, creates, edits, moves and deletes files in **one
Google Drive folder** — by default
[`14bBoWM5xwXMjSrG4gJcjcI2BMr6WOT8n`](https://drive.google.com/drive/folders/14bBoWM5xwXMjSrG4gJcjcI2BMr6WOT8n)
— and refuses to touch anything outside it.

## Why the fence

Every tool resolves its target and walks that file's parents up to the root
folder before doing anything. A file id from somewhere else in your Drive comes
back as an `out of scope` error rather than a modified file. Deletes go to the
Drive trash unless you deliberately turn permanent deletion on.

## Setup

### 1. Get Google credentials

1. Open the [Google Cloud console](https://console.cloud.google.com/), create or
   pick a project, and enable the **Google Drive API**.
2. Under **APIs & Services → Credentials**, create an **OAuth client ID** of type
   **Desktop app**.
3. Download the JSON and save it as `~/.config/gdrive-mcp/credentials.json`.

While your OAuth consent screen is in *Testing*, add your own Google account
under **Audience → Test users**, or sign-in will be refused.

A service account key works too — drop it at the same path and the server will
detect it. Remember to share the Drive folder with the service account's email
address, since a service account has no Drive of its own.

### 2. Install

```bash
cd gdrive-mcp
uv venv --python 3.13
uv pip install -e ".[dev]"
```

Python 3.10 works, but Google's client libraries now warn on it — 3.11 or newer
keeps the output clean.

### 3. First run (one browser consent)

```bash
uv run gdrive-mcp
```

A browser window opens for you to grant access. The resulting token is cached at
`~/.config/gdrive-mcp/token.json` (mode `600`) and reused after that. The process
then sits waiting on stdin — that is a healthy MCP server. Press `Ctrl-C`.

### 4. Register it with Claude Code

```bash
claude mcp add gdrive -- uv --directory /Users/indrajitsingh/Projects/Advanced_RAG/gdrive-mcp run gdrive-mcp
```

Or add it to an MCP client config by hand:

```json
{
  "mcpServers": {
    "gdrive": {
      "command": "uv",
      "args": [
        "--directory",
        "/Users/indrajitsingh/Projects/Advanced_RAG/gdrive-mcp",
        "run",
        "gdrive-mcp"
      ]
    }
  }
}
```

## Tools

| Tool | What it does |
| --- | --- |
| `drive_info` | Which folder is in scope, and the current settings |
| `list_files` | Direct contents of a folder |
| `search_files` | Full-text and name search, filtered to the folder tree |
| `get_file_metadata` | Full Drive metadata for one item |
| `read_file` | File as text; Docs, Sheets and Slides are exported automatically |
| `download_file` | Save a binary (PDF, image) to local disk |
| `list_revisions` | Revision history, handy after an edit |
| `create_file` | New file, optionally a native Google Doc or Sheet |
| `create_folder` | New subfolder |
| `update_file` | Replace a file's whole contents |
| `edit_file` | Replace exact text inside a file, leaving the rest alone |
| `rename_file` | Rename a file or folder |
| `move_file` | Move within the folder tree |
| `copy_file` | Duplicate a file |
| `delete_file` | To trash, or permanently when explicitly enabled |
| `restore_file` | Pull a file back out of the trash |

### Editing Google-native files

`create_file` with `mime_type="application/vnd.google-apps.document"` makes a real
Google Doc. `read_file` exports Docs as Markdown and Sheets as CSV, and
`update_file` converts your text back on the way in. Round-tripping a richly
formatted Doc through Markdown will flatten formatting the export cannot carry,
so prefer `edit_file` for small changes to a Doc you care about.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `GDRIVE_ROOT_FOLDER_ID` | the folder above | Folder id or URL to fence the server to |
| `GDRIVE_CREDENTIALS_PATH` | `~/.config/gdrive-mcp/credentials.json` | OAuth client or service account JSON |
| `GDRIVE_TOKEN_PATH` | `~/.config/gdrive-mcp/token.json` | Where the cached OAuth token lives |
| `GDRIVE_READ_ONLY` | `false` | Reject every write, for a look-but-don't-touch session |
| `GDRIVE_ALLOW_PERMANENT_DELETE` | `false` | Allow `delete_file(permanent=True)` |

The OAuth scope is full `drive` access, because the server has to modify files it
did not create; the narrower `drive.file` scope only ever sees an app's own
files. The folder fence, not the scope, is what keeps it contained.

## Tests

```bash
uv run pytest
```

62 tests run against an in-memory fake Drive, so they need no credentials and
touch no real files. The bulk of them pin down the folder fence: every operation
is checked against a file that lives elsewhere in the Drive.
