#!/usr/bin/env python3
"""note.py — Notes system for curve-memory

Notes are detailed documents stored at ~/.hermes/notes/{name}.md
that are referenced from memory files but NOT loaded into context
unless explicitly fetched via curve_memory_read_note.

Functions:
    extract_note_refs(content)  — scan FULL file content for note: references
    get_notes_dir()              — get or create notes directory
    note_exists()                — check if note file exists
    read_note()                  — read note content (returns None if not found)
    write_note()                 — create/overwrite a note file
    link_note_to_memory()        — append note: reference to a memory file
    delete_note()                — remove a note file
    list_notes()                 — list all note names
"""

import re
from pathlib import Path
from typing import Optional

NOTE_REF_PATTERN = re.compile(r'^note:\s*(\S[\w\-. /]*)$', re.MULTILINE)


def extract_note_refs(content: str) -> list[str]:
    """Extract all note: references from memory content.

    Scans the ENTIRE content, not just the first lines.
    This is critical because note references may be at the bottom
    of the file (e.g., after enriched sections).
    """
    return [m.group(1).strip() for m in NOTE_REF_PATTERN.finditer(content)]


def get_notes_dir(hermes_home: Path) -> Path:
    """Get or create the notes directory (~/.hermes/notes/)."""
    notes_dir = hermes_home / "notes"
    notes_dir.mkdir(parents=True, exist_ok=True)
    return notes_dir


def note_exists(note_name: str, notes_dir: Path) -> bool:
    """Check if a note file exists."""
    return (notes_dir / f"{note_name}.md").exists()


def read_note(note_name: str, notes_dir: Path) -> Optional[str]:
    """Read a note file. Returns None if not found."""
    path = notes_dir / f"{note_name}.md"
    if not path.exists():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return None


def write_note(note_name: str, content: str, notes_dir: Path) -> bool:
    """Write a note file. Returns True on success.

    Standalone function — does NOT modify any memory file.
    Use link_note_to_memory() separately if you need a reference.
    """
    path = notes_dir / f"{note_name}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.write_text(f"# {note_name}\n\n{content.strip()}\n", encoding="utf-8")
        return True
    except Exception:
        return False


def link_note_to_memory(topic: str, note_name: str, memories_dir: Path) -> bool:
    """Append a note: reference line to a memory file.

    Standalone function — does NOT create any note file.
    Use write_note() first if the note doesn't exist yet.
    """
    mem_path = memories_dir / "active" / f"{topic}.md"
    if not mem_path.exists():
        return False
    try:
        existing = mem_path.read_text(encoding="utf-8")
        # Only add if not already present
        if f"note: {note_name}" not in existing:
            new_content = existing.rstrip("\n") + f"\nnote: {note_name}\n"
            mem_path.write_text(new_content, encoding="utf-8")
        return True
    except Exception:
        return False


def delete_note(note_name: str, notes_dir: Path) -> bool:
    """Delete a note file."""
    path = notes_dir / f"{note_name}.md"
    if not path.exists():
        return False
    try:
        path.unlink()
        return True
    except Exception:
        return False


def list_notes(notes_dir: Path) -> list[str]:
    """List all note names (sorted)."""
    if not notes_dir.exists():
        return []
    return sorted(f.stem for f in notes_dir.glob("*.md"))
