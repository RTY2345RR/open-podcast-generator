"""The podcast script format: plain Markdown you can write or edit by hand.

    ---
    title: Why the sky is blue
    description: A two-host explainer on Rayleigh scattering.
    ---
    ## Cold open
    Alex: So, why is the sky blue?
    Sam: Short answer: sunlight bounces off air molecules, and blue bounces the most.

    ## The physics
    Alex: ...

* Optional front matter (`key: value` lines between `---`) holds episode metadata.
* `## Heading` starts a chapter. Lines before the first heading go into "Intro".
* `Name: text` is one spoken line. A line with no `Name:` prefix continues the previous one.
* `[pause]` or `[pause 2]` inserts silence (seconds). Lines starting with `>` or `<!--` are notes and are skipped.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

SPEAKER_RE = re.compile(r"^\s*(?:\*\*)?([A-Za-z][\w .'\-]{0,40}?)(?:\*\*)?\s*:\s*(.+)$")
PAUSE_RE = re.compile(r"^\s*\[pause(?:\s+(\d+(?:\.\d+)?))?\]\s*$", re.I)
# Stage directions an LLM may add, e.g. "(laughs)" or "*sighs*", are not spoken.
DIRECTION_RE = re.compile(r"\((?:laughs?|laughing|chuckles?|sighs?|pause|beat|music|smiles?)[^)]*\)|\*[^*]{1,30}\*", re.I)


class ScriptError(ValueError):
    pass


@dataclass
class Line:
    speaker: str
    text: str
    pause_after: float = 0.0  # extra silence requested with [pause]


@dataclass
class Chapter:
    title: str
    lines: list[Line] = field(default_factory=list)


@dataclass
class Script:
    title: str = "Untitled episode"
    description: str = ""
    meta: dict[str, str] = field(default_factory=dict)
    chapters: list[Chapter] = field(default_factory=list)

    @property
    def lines(self) -> list[Line]:
        return [line for ch in self.chapters for line in ch.lines]

    @property
    def speakers(self) -> list[str]:
        seen: list[str] = []
        for line in self.lines:
            if line.speaker not in seen:
                seen.append(line.speaker)
        return seen

    def word_count(self) -> int:
        return sum(len(line.text.split()) for line in self.lines)

    def to_markdown(self) -> str:
        out = ["---", f"title: {self.title}"]
        if self.description:
            out.append(f"description: {self.description}")
        for key, value in self.meta.items():
            if key not in ("title", "description"):
                out.append(f"{key}: {value}")
        out.append("---")
        for ch in self.chapters:
            out += ["", f"## {ch.title}"]
            for line in ch.lines:
                out.append(f"{line.speaker}: {line.text}")
                if line.pause_after:
                    out.append(f"[pause {line.pause_after:g}]")
        return "\n".join(out).rstrip() + "\n"


def clean_text(text: str) -> str:
    text = DIRECTION_RE.sub(" ", text)
    text = text.replace("**", "").replace("__", "")
    return re.sub(r"\s+", " ", text).strip()


def parse_script(source: str, hosts: Optional[list[str]] = None) -> Script:
    """Parse the Markdown script. `hosts` (names) restricts which `Name:` prefixes count as speakers."""
    allowed = {h.lower(): h for h in hosts} if hosts else None
    script = Script()
    body = source.replace("\r\n", "\n").lstrip("\ufeff")
    lines = body.split("\n")
    i = 0
    while i < len(lines) and not lines[i].strip():
        i += 1
    if i < len(lines) and lines[i].strip() == "---":
        j = i + 1
        while j < len(lines) and lines[j].strip() != "---":
            if ":" in lines[j]:
                key, value = lines[j].split(":", 1)
                script.meta[key.strip().lower()] = value.strip()
            j += 1
        if j >= len(lines):
            raise ScriptError("Front matter starts with '---' but has no closing '---'.")
        lines = lines[j + 1:]
    script.title = script.meta.get("title") or script.title
    script.description = script.meta.get("description", "")

    chapter: Optional[Chapter] = None
    last: Optional[Line] = None
    for raw in lines:
        s = raw.strip()
        if not s or s.startswith(">") or s.startswith("<!--") or s == "---":
            continue
        if s.startswith("# ") and not script.meta.get("title"):
            script.title = s[2:].strip()
            continue
        if s.startswith("#"):
            chapter = Chapter(s.lstrip("#").strip() or f"Part {len(script.chapters) + 1}")
            script.chapters.append(chapter)
            last = None
            continue
        pause = PAUSE_RE.match(s)
        if pause:
            if last is not None:
                last.pause_after += float(pause.group(1) or 1.0)
            continue
        m = SPEAKER_RE.match(s)
        speaker = None
        if m:
            name = m.group(1).strip()
            if allowed is None or name.lower() in allowed:
                speaker = allowed[name.lower()] if allowed else name
        if speaker:
            text = clean_text(m.group(2))
            if not text:
                continue
            if chapter is None:
                chapter = Chapter("Intro")
                script.chapters.append(chapter)
            last = Line(speaker, text)
            chapter.lines.append(last)
        elif last is not None:
            extra = clean_text(s)
            if extra:
                last.text = f"{last.text} {extra}"
    script.chapters = [c for c in script.chapters if c.lines]
    if not script.chapters:
        names = ", ".join(hosts) if hosts else "Alex"
        raise ScriptError(f"No spoken lines found. Write lines as 'Name: text' (hosts: {names}).")
    return script


def split_long(text: str, limit: int = 600) -> list[str]:
    """Split a long line at sentence boundaries so every voice request stays short and natural."""
    if len(text) <= limit:
        return [text]
    parts = re.split(r"(?<=[.!?;])\s+", text)
    chunks: list[str] = []
    cur = ""
    for part in parts:
        while len(part) > limit:
            cut = part.rfind(" ", 0, limit)
            cut = cut if cut > 0 else limit
            if cur:
                chunks.append(cur)
                cur = ""
            chunks.append(part[:cut].strip())
            part = part[cut:].strip()
        if cur and len(cur) + 1 + len(part) > limit:
            chunks.append(cur)
            cur = part
        else:
            cur = f"{cur} {part}".strip()
    if cur:
        chunks.append(cur)
    return chunks
