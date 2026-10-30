import os
import json
import pypdf
import docx
from mcp.server.fastmcp import FastMCP

CAREER_DIR = os.path.join(os.path.dirname(__file__), "Career")
INDEX_FILE = os.path.join(os.path.dirname(__file__), "processed_files.json")
FACTS_FILE = os.path.join(os.path.dirname(__file__), "facts.md")

mcp = FastMCP("career")


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


@mcp.tool()
def list_career_documents() -> list[str]:
    """Lists every file under Career/ and its subdirectories, as paths
    relative to Career/. Multiple formats of the same document (e.g.
    resume.pdf and resume.docx) are both listed — pick whichever is most
    useful to read."""
    paths = []
    for root, _, files in os.walk(CAREER_DIR):
        for file in files:
            if file.startswith(".") or file.endswith((".tmp", ".json", ".zip", ".ps1")):
                continue
            full_path = os.path.join(root, file)
            paths.append(os.path.relpath(full_path, CAREER_DIR))
    return paths


@mcp.tool()
def read_document(relpath: str) -> str:
    """Reads and extracts the text content of one file under Career/, given
    its path relative to Career/ (as returned by list_career_documents)."""
    full_path = os.path.join(CAREER_DIR, relpath)
    if not os.path.isfile(full_path):
        return f"Error: '{relpath}' does not exist under Career/."

    ext = os.path.splitext(full_path)[1].lower()
    try:
        if ext == ".pdf":
            text = ""
            reader = pypdf.PdfReader(full_path)
            for page in reader.pages:
                extracted = page.extract_text()
                if extracted:
                    text += extracted + "\n"
            return text.strip()
        elif ext == ".docx":
            document = docx.Document(full_path)
            return "\n".join(p.text for p in document.paragraphs if p.text)
        else:
            with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
                return f.read()
    except Exception as e:
        return f"Error reading '{relpath}': {e}"


@mcp.tool()
def list_unprocessed_documents() -> list[str]:
    """Lists paths (relative to Career/) that have NOT yet been marked
    processed. This is list_career_documents with everything already
    covered by mark_processed removed — work through this list instead of
    list_career_documents so you don't re-investigate what's already done."""
    processed = set(_load_index().keys())
    return [p for p in list_career_documents() if p not in processed]


@mcp.tool()
def mark_processed(relpath: str) -> str:
    """Marks one document (path relative to Career/, as returned by
    list_career_documents) as processed. Call this once you're done
    investigating it and any leads it pointed to — not before."""
    full_path = os.path.join(CAREER_DIR, relpath)
    if not os.path.isfile(full_path):
        return f"Error: '{relpath}' does not exist under Career/."
    index = _load_index()
    index[relpath] = os.path.getmtime(full_path)
    _save_index(index)
    return f"Marked '{relpath}' as processed."


@mcp.tool()
def save_candidate_fact(text: str) -> str:
    """Appends one piece of first-pass knowledge about the candidate to
    facts.md — a direct fact or a hedged inference, along with what evidence
    it's based on. This is raw material for a downstream stakeholder panel,
    not the final word — record real semantic judgment, not just mechanical
    extraction. Call this each time you learn something, not just once at
    the end."""
    with open(FACTS_FILE, "a", encoding="utf-8") as f:
        f.write(text.strip() + "\n\n")
    return "Saved to facts.md."


if __name__ == "__main__":
    mcp.run()
