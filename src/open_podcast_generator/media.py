"""ffmpeg helpers. ffmpeg comes from PATH, FFMPEG_BINARY, or the bundled imageio-ffmpeg wheel."""

from __future__ import annotations

import os
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Optional

SAMPLE_RATE = 44_100
BYTES_PER_SAMPLE = 2  # s16le mono


class MediaError(Exception):
    pass


@lru_cache(maxsize=1)
def ffmpeg_exe() -> str:
    env = os.environ.get("FFMPEG_BINARY", "").strip()
    if env:
        return env
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg  # type: ignore[import-untyped]

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:  # pragma: no cover
        raise MediaError("ffmpeg was not found. Install it (brew/apt/winget install ffmpeg) or set FFMPEG_BINARY.") from exc


def run(args: list[str], input_bytes: Optional[bytes] = None) -> bytes:
    proc = subprocess.run([ffmpeg_exe(), "-hide_banner", "-nostdin", "-loglevel", "error", "-y", *args],
                          input=input_bytes, capture_output=True)
    if proc.returncode != 0:
        tail = proc.stderr.decode("utf-8", "replace").strip().splitlines()[-4:]
        raise MediaError("ffmpeg failed: " + " | ".join(tail))
    return proc.stdout


def decode_pcm(path: Path) -> bytes:
    """Decode any audio file to 44.1 kHz mono s16le PCM, trimming leading/trailing silence."""
    trim = ("silenceremove=start_periods=1:start_threshold=-50dB:start_silence=0.05,"
            "areverse,silenceremove=start_periods=1:start_threshold=-50dB:start_silence=0.08,areverse")
    pcm = run(["-i", str(path), "-af", trim, "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "s16le", "-"])
    if not pcm:  # all silence after trimming: fall back to the untrimmed clip
        pcm = run(["-i", str(path), "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "s16le", "-"])
    return pcm


def silence(seconds: float) -> bytes:
    return b"\x00" * (int(round(seconds * SAMPLE_RATE)) * BYTES_PER_SAMPLE)


def pcm_seconds(pcm: bytes) -> float:
    return len(pcm) / (SAMPLE_RATE * BYTES_PER_SAMPLE)


def ffmeta_escape(value: str) -> str:
    for ch in ("\\", "=", ";", "#", "\n"):
        value = value.replace(ch, "\\" + ch)
    return value


def encode_episode(pcm: bytes, dest: Path, *, title: str, artist: str, album: str,
                   chapters: list[tuple[float, float, str]], cover: Optional[Path] = None,
                   bitrate: str = "128k", normalize: bool = True) -> Path:
    """Encode the stitched PCM to MP3 at -16 LUFS with ID3 tags and ID3 CHAP chapter markers."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    meta = [";FFMETADATA1", f"title={ffmeta_escape(title)}", f"artist={ffmeta_escape(artist)}",
            f"album={ffmeta_escape(album)}", "genre=Podcast"]
    for start, end, name in chapters:
        meta += ["[CHAPTER]", "TIMEBASE=1/1000", f"START={int(start * 1000)}", f"END={int(end * 1000)}",
                 f"title={ffmeta_escape(name)}"]
    meta_path = dest.with_name(dest.stem + ".ffmeta.txt")
    meta_path.write_text("\n".join(meta) + "\n", encoding="utf-8")
    raw = dest.with_name(dest.stem + ".raw.pcm")
    raw.write_bytes(pcm)
    try:
        args = ["-f", "s16le", "-ar", str(SAMPLE_RATE), "-ac", "1", "-i", str(raw), "-i", str(meta_path)]
        if cover:
            args += ["-i", str(cover)]
        args += ["-map", "0:a", "-map_metadata", "1", "-map_chapters", "1"]
        if cover:
            args += ["-map", "2:v", "-c:v", "mjpeg", "-disposition:v", "attached_pic",
                     "-metadata:s:v", "title=Album cover", "-metadata:s:v", "comment=Cover (front)"]
        if normalize:
            args += ["-af", "loudnorm=I=-16:TP=-1.5:LRA=11", "-ar", str(SAMPLE_RATE)]
        args += ["-c:a", "libmp3lame", "-b:a", bitrate, "-id3v2_version", "3", "-write_xing", "1", str(dest)]
        run(args)
    finally:
        raw.unlink(missing_ok=True)
        meta_path.unlink(missing_ok=True)
    return dest
