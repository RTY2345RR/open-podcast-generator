"""Turn a source (PDF, web page, YouTube video, text/Markdown file, stdin or a plain string) into text."""

from __future__ import annotations

import io
import re
import sys
import tempfile
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit

import httpx

BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
TEXT_EXTS = {".txt", ".md", ".markdown", ".rst", ".html", ".htm", ".srt", ".vtt", ".csv", ".json"}
YOUTUBE_HOSTS = ("youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be", "music.youtube.com")


class SourceError(Exception):
    pass


@dataclass
class Source:
    text: str
    title: str
    origin: str  # path or URL
    kind: str  # pdf | web | youtube | text


class _HTMLText(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "nav", "footer", "header", "aside", "form", "button", "template"}
    BLOCK = {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article", "tr", "blockquote", "pre"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title = ""
        self._skip = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs) -> None:  # type: ignore[override]
        if tag in self.SKIP:
            self._skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.SKIP and self._skip:
            self._skip -= 1
        elif tag == "title":
            self._in_title = False
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        elif not self._skip:
            self.parts.append(data)


def html_to_text(html: str) -> tuple[str, str]:
    """Readable text and <title> from HTML (main content heuristics: drop nav, footer, scripts)."""
    parser = _HTMLText()
    parser.feed(html)
    raw = "".join(parser.parts)
    lines = [re.sub(r"[ \t\xa0]+", " ", ln).strip() for ln in raw.split("\n")]
    # Keep paragraphs with some substance; menus and buttons are short fragments.
    kept = [ln for ln in lines if len(ln) >= 40 or (ln and ln[-1:] in ".!?:")]
    text = "\n\n".join(kept) or "\n".join(ln for ln in lines if ln)
    return text.strip(), re.sub(r"\s+", " ", parser.title).strip()


def pdf_to_text(data: bytes) -> tuple[str, str]:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - pypdf is a dependency
        raise SourceError("Reading PDFs needs pypdf: pip install pypdf") from exc
    reader = PdfReader(io.BytesIO(data))
    pages = [(page.extract_text() or "") for page in reader.pages]
    text = "\n\n".join(p.strip() for p in pages if p.strip())
    text = re.sub(r"-\n(?=[a-z])", "", text)  # re-join hyphenated words
    text = re.sub(r"(?<![.!?:\n])\n(?!\n)", " ", text)  # unwrap hard-wrapped lines
    title = ""
    try:
        title = str((reader.metadata or {}).get("/Title") or "")
    except Exception:
        title = ""
    if not text.strip():
        raise SourceError("This PDF has no extractable text (it may be a scan). Run OCR first, e.g. ocrmypdf.")
    return text.strip(), title.strip()


def vtt_to_text(vtt: str) -> str:
    out: list[str] = []
    for ln in vtt.splitlines():
        ln = ln.strip()
        if not ln or "-->" in ln or ln.startswith(("WEBVTT", "Kind:", "Language:", "NOTE")) or ln.isdigit():
            continue
        ln = re.sub(r"<[^>]+>", "", ln)
        if not out or out[-1] != ln:
            out.append(ln)
    return " ".join(out)


def is_youtube(url: str) -> bool:
    return urlsplit(url).netloc.lower() in YOUTUBE_HOSTS


def youtube_to_text(url: str, language: str = "en") -> tuple[str, str]:
    try:
        import yt_dlp  # type: ignore[import-untyped]
    except ImportError as exc:
        raise SourceError(
            "YouTube sources need yt-dlp: pip install 'open-podcast-generator[youtube]' "
            "(or pass a transcript file instead)."
        ) from exc
    with tempfile.TemporaryDirectory() as tmp:
        opts = {
            "skip_download": True, "writesubtitles": True, "writeautomaticsub": True,
            "subtitleslangs": [language, f"{language}.*", "en", "en.*"], "subtitlesformat": "vtt",
            "outtmpl": str(Path(tmp) / "%(id)s.%(ext)s"), "quiet": True, "no_warnings": True,
        }
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
        files = sorted(Path(tmp).glob("*.vtt"))
        if not files:
            raise SourceError("This video has no captions to build a podcast from.")
        return vtt_to_text(files[0].read_text(encoding="utf-8", errors="replace")), str(info.get("title") or "")


def fetch_url(url: str, timeout: float = 30.0, transport: Optional[httpx.BaseTransport] = None) -> Source:
    if is_youtube(url):
        text, title = youtube_to_text(url)
        return Source(text, title or "YouTube video", url, "youtube")
    try:
        with httpx.Client(headers={"User-Agent": BROWSER_UA, "Accept": "text/html,application/pdf,*/*"},
                          follow_redirects=True, timeout=timeout, transport=transport) as client:
            resp = client.get(url)
    except httpx.HTTPError as exc:
        raise SourceError(f"Could not download {url} ({type(exc).__name__}).") from None
    if resp.status_code >= 400:
        raise SourceError(f"{url} returned HTTP {resp.status_code}.")
    ctype = resp.headers.get("content-type", "").lower()
    if "pdf" in ctype or resp.content[:5] == b"%PDF-":
        text, title = pdf_to_text(resp.content)
        return Source(text, title or Path(urlsplit(url).path).stem or url, url, "pdf")
    if "html" in ctype or resp.text.lstrip()[:1] == "<":
        text, title = html_to_text(resp.text)
        return Source(text, title or url, url, "web")
    return Source(resp.text.strip(), Path(urlsplit(url).path).stem or url, url, "text")


def load_source(spec: str, *, transport: Optional[httpx.BaseTransport] = None) -> Source:
    """`spec` is a URL, a file path (.pdf, .txt, .md, .html...), `-` for stdin, or literal text."""
    if spec == "-":
        return Source(sys.stdin.read().strip(), "Untitled", "stdin", "text")
    if re.match(r"^https?://", spec, re.I):
        src = fetch_url(spec, transport=transport)
    else:
        path = Path(spec).expanduser()
        if path.is_file():
            data = path.read_bytes()
            if path.suffix.lower() == ".pdf" or data[:5] == b"%PDF-":
                text, title = pdf_to_text(data)
                src = Source(text, title or path.stem.replace("-", " ").replace("_", " "), str(path), "pdf")
            else:
                raw = data.decode("utf-8", errors="replace")
                if path.suffix.lower() in (".html", ".htm"):
                    raw, title = html_to_text(raw)
                elif path.suffix.lower() in (".vtt", ".srt"):
                    raw, title = vtt_to_text(raw), ""
                else:
                    title = ""
                src = Source(raw.strip(), title or path.stem.replace("-", " ").replace("_", " "), str(path), "text")
        elif len(spec) > 200 or " " in spec.strip():
            src = Source(spec.strip(), "Untitled", "inline text", "text")
        else:
            raise SourceError(f"{spec!r} is not a file, URL or text. Pass a PDF/URL/text file, '-' for stdin, or quoted text.")
    if len(src.text.split()) < 20:
        raise SourceError(f"Only {len(src.text.split())} words could be read from {src.origin}; need at least 20.")
    return src
