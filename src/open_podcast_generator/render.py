"""Voice every script line with its host's cloned voice, stitch the episode and write all outputs.

Re-running is safe and cheap: each line's request is saved in `state.json` with its
idempotency key and task id, finished clips are reused, and only edited lines are voiced again.
"""

from __future__ import annotations

import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from .config import ConfigError, Host
from .media import decode_pcm, encode_episode, pcm_seconds, silence
from .miragea import MirageaClient, MirageaError, new_key
from .outputs import (Segment, rss_feed, rss_item, to_chapters_json, to_show_notes, to_srt, to_transcript_json,
                      to_txt, to_vtt)
from .script import Script, split_long

GAP_SAME_SPEAKER = 0.18
GAP_TURN = 0.32
GAP_CHAPTER = 0.9
LEAD_IN = 0.3
TAIL = 0.8

Log = Callable[[str], None]


@dataclass
class RenderOptions:
    model: Optional[str] = None
    concurrency: int = 4
    author: str = "open-podcast-generator"
    show_title: str = ""
    base_url: str = ""
    cover: Optional[Path] = None
    intro: Optional[Path] = None
    outro: Optional[Path] = None
    bitrate: str = "128k"
    normalize: bool = True
    language: str = "en"
    episode: Optional[int] = None
    max_chars: int = 600


@dataclass
class Piece:
    index: int
    speaker: str
    text: str
    chapter: int
    line: int  # index of the script line this piece belongs to
    pause_after: float = 0.0
    payload: dict[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> str:
        blob = json.dumps(self.payload, sort_keys=True, ensure_ascii=False).encode()
        return hashlib.sha256(blob).hexdigest()[:20]


@dataclass
class RenderResult:
    out_dir: Path
    files: dict[str, Path]
    duration: float
    voiced: int
    reused: int


def plan(script: Script, hosts: list[Host], options: RenderOptions) -> list[Piece]:
    by_name = {h.name.lower(): h for h in hosts}
    missing = [s for s in script.speakers if s.lower() not in by_name]
    if missing:
        raise ConfigError(
            f"The script has speakers with no voice: {', '.join(missing)}. Add --host NAME=VOICE for each "
            f"(hosts now: {', '.join(h.name for h in hosts)})."
        )
    pieces: list[Piece] = []
    line_no = 0
    for c, chapter in enumerate(script.chapters):
        for line in chapter.lines:
            host = by_name[line.speaker.lower()]
            parts = split_long(line.text, options.max_chars)
            for j, text in enumerate(parts):
                payload = {"text": text, "format": "wav", **host.voice_payload()}
                if options.model:
                    payload["model"] = options.model
                pieces.append(Piece(len(pieces), host.name, text, c, line_no,
                                    line.pause_after if j == len(parts) - 1 else 0.0, payload))
            line_no += 1
    return pieces


class StateFile:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock = threading.Lock()
        self.data: dict[str, Any] = {"version": 1, "clips": {}}
        if path.exists():
            try:
                self.data = json.loads(path.read_text(encoding="utf-8"))
            except ValueError:
                pass
        self.data.setdefault("clips", {})

    def get(self, key: str) -> dict[str, Any]:
        with self.lock:
            return dict(self.data["clips"].get(key, {}))

    def update(self, key: str, **values: Any) -> None:
        with self.lock:
            self.data["clips"].setdefault(key, {}).update(values)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, indent=2, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.path)


def voice_piece(client: MirageaClient, piece: Piece, clips_dir: Path, state: StateFile) -> tuple[Path, bool]:
    """Return (clip path, reused). Resumes an existing task id; never submits the same line twice."""
    clip = clips_dir / f"{piece.key}.wav"
    if clip.exists() and clip.stat().st_size > 44:
        return clip, True
    saved = state.get(piece.key)
    task_id = saved.get("task_id")
    if not task_id:
        key = saved.get("idempotency_key") or new_key()
        state.update(piece.key, idempotency_key=key, speaker=piece.speaker, text=piece.text)
        task = client.create_speech(piece.payload, key)
        task_id = task.get("task_id")
        if not task_id:
            raise MirageaError("invalid_response", "The voice API returned no task_id.")
        state.update(piece.key, task_id=task_id)
    task = client.wait(task_id)
    if task.get("status") != "succeeded":
        err = task.get("error") or {}
        # Forget the failed task so the next run submits a fresh request for this line.
        state.update(piece.key, task_id=None, idempotency_key=None, last_error=err)
        raise MirageaError(str(err.get("code") or "task_failed"),
                           f"Line {piece.line + 1} ({piece.speaker}) failed: {err.get('message') or 'unknown error'}",
                           task_id=task_id)
    url = (task.get("result") or {}).get("audio_url")
    if not url:
        raise MirageaError("invalid_result", "The finished task has no audio_url.", task_id=task_id)
    client.download(url, clip)
    state.update(piece.key, done=True)
    return clip, False


def voice_all(client: MirageaClient, pieces: list[Piece], out_dir: Path, concurrency: int, log: Log) -> tuple[dict[int, Path], int]:
    clips_dir = out_dir / "clips"
    clips_dir.mkdir(parents=True, exist_ok=True)
    state = StateFile(out_dir / "state.json")
    results: dict[int, Path] = {}
    reused = 0
    errors: list[MirageaError] = []
    total = len(pieces)
    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        futures = {pool.submit(voice_piece, client, p, clips_dir, state): p for p in pieces}
        for fut in as_completed(futures):
            piece = futures[fut]
            try:
                path, was_reused = fut.result()
            except MirageaError as exc:
                errors.append(exc)
                log(f"  ! line {piece.line + 1} ({piece.speaker}): {exc}")
                continue
            results[piece.index] = path
            reused += int(was_reused)
            log(f"  [{len(results)}/{total}] {piece.speaker}: {piece.text[:60]}{'...' if len(piece.text) > 60 else ''}"
                + (" (cached)" if was_reused else ""))
    if errors:
        first = errors[0]
        raise MirageaError(first.code, f"{len(errors)} of {total} lines could not be voiced. First error: {first.message}. "
                           "Re-run the same command to resume; finished lines are kept.", task_id=first.task_id,
                           hint=first.hint)
    return results, reused


def stitch(script: Script, pieces: list[Piece], clips: dict[int, Path], options: RenderOptions,
           decode: Callable[[Path], bytes] = decode_pcm) -> tuple[bytes, list[Segment], list[tuple[float, float, str]]]:
    """Concatenate clips with natural gaps; return PCM, per-line timed segments and chapter spans."""
    audio = bytearray()
    if options.intro:
        audio += decode(options.intro) + silence(0.4)
    audio += silence(LEAD_IN)
    segments: list[Segment] = []
    chapter_starts: dict[int, float] = {}
    prev: Optional[Piece] = None
    for piece in pieces:
        if prev is not None:
            if piece.chapter != prev.chapter:
                gap = GAP_CHAPTER
            elif piece.line == prev.line:
                gap = 0.08
            else:
                gap = GAP_SAME_SPEAKER if piece.speaker == prev.speaker else GAP_TURN
            audio += silence(gap + prev.pause_after)
        start = pcm_seconds(audio)
        chapter_starts.setdefault(piece.chapter, max(0.0, start - (LEAD_IN if prev is None else 0.0)))
        audio += decode(clips[piece.index])
        end = pcm_seconds(audio)
        if segments and prev is not None and prev.line == piece.line:
            segments[-1].text += " " + piece.text
            segments[-1].end = end
        else:
            segments.append(Segment(piece.speaker, piece.text, start, end, piece.chapter))
        prev = piece
    if prev is not None:
        audio += silence(TAIL + prev.pause_after)
    if options.outro:
        audio += decode(options.outro)
    total = pcm_seconds(audio)
    if options.intro:
        chapter_starts[0] = 0.0
    order = sorted(chapter_starts)
    spans = []
    for i, c in enumerate(order):
        end = chapter_starts[order[i + 1]] if i + 1 < len(order) else total
        spans.append((chapter_starts[c], end, script.chapters[c].title))
    return bytes(audio), segments, spans


def write_outputs(script: Script, hosts: list[Host], pcm: bytes, segments: list[Segment],
                  spans: list[tuple[float, float, str]], out_dir: Path, options: RenderOptions,
                  encode: Callable[..., Path] = encode_episode) -> dict[str, Path]:
    files: dict[str, Path] = {}

    def put(name: str, text: str) -> None:
        path = out_dir / name
        path.write_text(text, encoding="utf-8", newline="\n")
        files[name] = path

    show = options.show_title or script.meta.get("show") or "AI Podcast"
    episode = out_dir / "episode.mp3"
    encode(pcm, episode, title=script.title, artist=", ".join(h.name for h in hosts), album=show,
           chapters=spans, cover=options.cover, bitrate=options.bitrate, normalize=options.normalize)
    files["episode.mp3"] = episode
    titles = [c.title for c in script.chapters]
    put("script.md", script.to_markdown())
    put("transcript.srt", to_srt(segments))
    put("transcript.vtt", to_vtt(segments))
    put("transcript.txt", to_txt(segments, titles))
    put("transcript.json", to_transcript_json(segments))
    put("chapters.json", to_chapters_json(spans))
    duration = pcm_seconds(pcm)
    put("show-notes.md", to_show_notes(script.title, script.description, spans, [h.name for h in hosts],
                                       script.meta.get("source", "")))
    guid = hashlib.sha256(script.to_markdown().encode()).hexdigest()[:32]
    item = rss_item(title=script.title, description=script.description, base_url=options.base_url,
                    audio_name="episode.mp3", audio_bytes=episode.stat().st_size, duration=duration, guid=guid,
                    pub_date=datetime.now(timezone.utc), author=options.author, episode=options.episode)
    put("feed-item.xml", item)
    image = ""
    if options.cover:
        image = "cover" + options.cover.suffix.lower()
        target = out_dir / image
        if options.cover.resolve() != target.resolve():
            target.write_bytes(options.cover.read_bytes())
    put("feed.xml", rss_feed(item, show_title=show, show_description=script.description or show,
                             base_url=options.base_url, author=options.author, language=options.language,
                             image_name=image))
    return files


def render(script: Script, hosts: list[Host], client: MirageaClient, out_dir: Path,
           options: Optional[RenderOptions] = None, log: Log = print) -> RenderResult:
    options = options or RenderOptions()
    out_dir.mkdir(parents=True, exist_ok=True)
    pieces = plan(script, hosts, options)
    log(f"Voicing {len(pieces)} lines with {len(hosts)} cloned voices ({', '.join(h.name for h in hosts)})...")
    clips, reused = voice_all(client, pieces, out_dir, options.concurrency, log)
    log("Stitching the episode...")
    pcm, segments, spans = stitch(script, pieces, clips, options)
    files = write_outputs(script, hosts, pcm, segments, spans, out_dir, options)
    return RenderResult(out_dir, files, pcm_seconds(pcm), len(pieces) - reused, reused)
