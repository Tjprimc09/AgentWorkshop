import os
import json
import sqlite3
from mcp.server.fastmcp import FastMCP

DB_PATH = os.path.join(os.path.dirname(__file__), "sessions.db")
DIGEST_FILE = os.path.join(os.path.dirname(__file__), "logs", "digest.md")
INDEX_FILE = os.path.join(os.path.dirname(__file__), "consolidated_sessions.json")

mcp = FastMCP("log_consolidator")


def _load_index() -> dict:
    if os.path.exists(INDEX_FILE):
        try:
            with open(INDEX_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def _save_index(index: dict) -> None:
    with open(INDEX_FILE, "w", encoding="utf-8") as f:
        json.dump(index, f, indent=2)


def _render_item(item: dict) -> str:
    """Best-effort turns one stored session item (the SDK's raw JSON message
    format) into one readable line. Items vary in shape (plain user input,
    structured assistant output, tool calls) — anything without recognizable
    text content is skipped rather than guessed at."""
    role = item.get("role") or item.get("type") or "unknown"
    content = item.get("content")

    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        parts = [part.get("text", "") for part in content if isinstance(part, dict)]
        text = "".join(parts)
    else:
        text = ""

    return f"{role}: {text}" if text.strip() else ""


@mcp.tool()
def list_unconsolidated_sessions() -> list[str]:
    """Lists session_ids from sessions.db that haven't been folded into the
    digest yet, oldest first."""
    if not os.path.exists(DB_PATH):
        return []
    conn = sqlite3.connect(DB_PATH)
    try:
        rows = conn.execute(
            "SELECT session_id FROM agent_sessions ORDER BY updated_at ASC"
        ).fetchall()
    finally:
        conn.close()
    consolidated = set(_load_index().keys())
    return [row[0] for row in rows if row[0] not in consolidated]


@mcp.tool()
def read_session(session_id: str) -> str:
    """Reads one session's conversation from sessions.db, reconstructed as
    readable text (as returned by list_unconsolidated_sessions)."""
    if not os.path.exists(DB_PATH):
        return "Error: sessions.db does not exist."
    conn = sqlite3.connect(DB_PATH)
    try:
        rows = conn.execute(
            "SELECT message_data FROM agent_messages WHERE session_id = ? ORDER BY id ASC",
            (session_id,),
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        return f"Error: no messages found for session '{session_id}'."

    lines = []
    for (message_data,) in rows:
        try:
            item = json.loads(message_data)
        except (json.JSONDecodeError, TypeError):
            continue
        rendered = _render_item(item)
        if rendered:
            lines.append(rendered)
    return "\n\n".join(lines)


@mcp.tool()
def mark_consolidated(session_id: str) -> str:
    """Marks one session_id as folded into the digest, so it isn't
    re-consolidated on a future run."""
    index = _load_index()
    index[session_id] = True
    _save_index(index)
    return f"Marked session '{session_id}' as consolidated."


@mcp.tool()
def get_digest() -> str:
    """Returns the current running digest text, or an empty string if none
    exists yet."""
    if os.path.exists(DIGEST_FILE):
        with open(DIGEST_FILE, "r", encoding="utf-8") as f:
            return f.read()
    return ""


@mcp.tool()
def save_digest(text: str) -> str:
    """Overwrites digest.md with the full updated digest text. This
    replaces the previous digest entirely — always pass the complete
    digest, not just what changed."""
    os.makedirs(os.path.dirname(DIGEST_FILE), exist_ok=True)
    with open(DIGEST_FILE, "w", encoding="utf-8") as f:
        f.write(text)
    return "Saved digest.md."


if __name__ == "__main__":
    mcp.run()
