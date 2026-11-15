"""Transcript (SRT, WebVTT, TXT, Podcasting 2.0 JSON), chapters JSON, show notes and RSS feed."""

from __future__ import annotations

import email.utils
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional
from xml.sax.saxutils import escape


@dataclass
class Segment:
    speaker: str
    text: str
    start: float
    end: float
    chapter: int


def ts(seconds: float, sep: str = ",") -> str:
    ms = int(round(max(0.0, seconds) * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def clock(seconds: float) -> str:
    """YouTube/Spotify style chapter stamp: 4:05 or 1:02:03."""
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def cues(seg: Segment, max_chars: int = 84) -> list[tuple[float, float, str]]:
    """Split one spoken line into caption-sized cues timed in proportion to their length."""
    sentences = re.split(r"(?<=[.!?;:,])\s+", seg.text)
    chunks: list[str] = []
    cur = ""
    for sent in sentences:
        words = sent.split()
        for word in words:
            if cur and len(cur) + 1 + len(word) > max_chars:
                chunks.append(cur)
                cur = word
            else:
                cur = f"{cur} {word}".strip()
        if cur and len(cur) > max_chars * 0.5 and sent is not sentences[-1]:
            chunks.append(cur)
            cur = ""
    if cur:
        chunks.append(cur)
    total = sum(len(c) for c in chunks) or 1
    out, t = [], seg.start
    for c in chunks:
        dur = (seg.end - seg.start) * len(c) / total
        out.append((t, t + dur, c))
        t += dur
    return out


def to_srt(segments: list[Segment], speaker_labels: bool = True) -> str:
    blocks, n = [], 0
    for seg in segments:
        for i, (a, b, text) in enumerate(cues(seg)):
            n += 1
            label = f"{seg.speaker}: " if speaker_labels and i == 0 else ""
            blocks.append(f"{n}\n{ts(a)} --> {ts(b)}\n{label}{text}\n")
    return "\n".join(blocks)


def to_vtt(segments: list[Segment]) -> str:
    out = ["WEBVTT", ""]
    for seg in segments:
        for a, b, text in cues(seg):
            out += [f"{ts(a, '.')} --> {ts(b, '.')}", f"<v {seg.speaker}>{text}", ""]
    return "\n".join(out)


def to_txt(segments: list[Segment], chapter_titles: list[str]) -> str:
    out, last = [], -1
    for seg in segments:
        if seg.chapter != last:
            last = seg.chapter
            out += ["" if out else "", f"[{clock(seg.start)}] {chapter_titles[seg.chapter]}", ""]
        out.append(f"{seg.speaker}: {seg.text}")
    return "\n".join(out).strip() + "\n"


def to_transcript_json(segments: list[Segment]) -> str:
    """Podcasting 2.0 JSON transcript (application/json, podcast:transcript)."""
    data = {"version": "1.0.0", "segments": [
        {"speaker": s.speaker, "startTime": round(s.start, 3), "endTime": round(s.end, 3), "body": s.text}
        for s in segments]}
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def to_chapters_json(chapters: list[tuple[float, float, str]]) -> str:
    """Podcasting 2.0 JSON chapters (application/json+chapters)."""
    data = {"version": "1.2.0", "chapters": [
        {"startTime": round(a, 3), "endTime": round(b, 3), "title": t} for a, b, t in chapters]}
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def to_show_notes(title: str, description: str, chapters: list[tuple[float, float, str]],
                  hosts: list[str], source: str = "") -> str:
    out = [f"# {title}", ""]
    if description:
        out += [description, ""]
    out += [f"Hosts: {', '.join(hosts)}", "", "## Chapters", ""]
    out += [f"{clock(a)} {t}" for a, _b, t in chapters]
    if source:
        out += ["", f"Source: {source}"]
    out += ["", "Made with open-podcast-generator: https://github.com/RTY2345RR/open-podcast-generator", ""]
    return "\n".join(out)


def _join(base: str, name: str) -> str:
    return f"{base.rstrip('/')}/{name}" if base else name


def rss_item(*, title: str, description: str, base_url: str, audio_name: str, audio_bytes: int,
             duration: float, guid: str, pub_date: Optional[datetime] = None, author: str = "",
             transcript_names: tuple[str, str] = ("transcript.srt", "transcript.json"),
             chapters_name: str = "chapters.json", episode: Optional[int] = None) -> str:
    pub = email.utils.format_datetime(pub_date or datetime.now(timezone.utc))
    lines = [
        "<item>",
        f"  <title>{escape(title)}</title>",
        f"  <description>{escape(description or title)}</description>",
        f"  <pubDate>{pub}</pubDate>",
        f"  <guid isPermaLink=\"false\">{escape(guid)}</guid>",
        f"  <enclosure url=\"{escape(_join(base_url, audio_name))}\" length=\"{audio_bytes}\" type=\"audio/mpeg\"/>",
        f"  <itunes:duration>{int(round(duration))}</itunes:duration>",
        "  <itunes:explicit>false</itunes:explicit>",
        "  <itunes:episodeType>full</itunes:episodeType>",
    ]
    if episode:
        lines.append(f"  <itunes:episode>{episode}</itunes:episode>")
    if author:
        lines.append(f"  <itunes:author>{escape(author)}</itunes:author>")
    lines += [
        f"  <podcast:transcript url=\"{escape(_join(base_url, transcript_names[0]))}\" type=\"application/srt\"/>",
        f"  <podcast:transcript url=\"{escape(_join(base_url, transcript_names[1]))}\" type=\"application/json\"/>",
        f"  <podcast:chapters url=\"{escape(_join(base_url, chapters_name))}\" type=\"application/json+chapters\"/>",
        "</item>",
    ]
    return "\n".join(lines) + "\n"


def rss_feed(item_xml: str, *, show_title: str, show_description: str, base_url: str, author: str,
             language: str = "en", image_name: str = "") -> str:
    link = base_url or "https://github.com/RTY2345RR/open-podcast-generator"
    head = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd" '
        'xmlns:podcast="https://podcastindex.org/namespace/1.0" xmlns:atom="http://www.w3.org/2005/Atom">',
        "<channel>",
        f"  <title>{escape(show_title)}</title>",
        f"  <link>{escape(link)}</link>",
        f"  <description>{escape(show_description or show_title)}</description>",
        f"  <language>{escape(language)}</language>",
        f"  <itunes:author>{escape(author)}</itunes:author>",
        "  <itunes:explicit>false</itunes:explicit>",
        '  <itunes:category text="Technology"/>',
        "  <generator>open-podcast-generator</generator>",
    ]
    if base_url:
        head.append(f'  <atom:link href="{escape(_join(base_url, "feed.xml"))}" rel="self" type="application/rss+xml"/>')
    if image_name:
        head.append(f'  <itunes:image href="{escape(_join(base_url, image_name))}"/>')
    body = "\n".join("  " + ln if ln else ln for ln in item_xml.rstrip("\n").split("\n"))
    return "\n".join(head) + "\n" + body + "\n</channel>\n</rss>\n"
