"""Command line: `open-podcast-generator make|script|render|voices|doctor`."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Optional

from . import __version__
from .config import (API_KEY_PAGE, ConfigError, Host, load_hosts, load_llm_settings, load_miragea_settings)
from .llm import STYLES, WORDS_PER_MINUTE, LLMError, write_script
from .media import MediaError, ffmpeg_exe
from .miragea import MirageaClient, MirageaError
from .render import RenderOptions, plan, render
from .script import Script, ScriptError, parse_script
from .sources import SourceError, load_source

EPILOG = """examples:
  open-podcast-generator make paper.pdf                      # PDF to podcast (LLM writes the script)
  open-podcast-generator make https://example.com/article    # article / web page to podcast
  open-podcast-generator script notes.md -o script.md        # write the script only, edit it, then:
  open-podcast-generator render script.md -o my-episode      # voice a script you wrote or edited
  open-podcast-generator make paper.pdf --host "Ana=https://me.example/ana.wav" --ref-text "Ana=exact words in the clip" \\
      --host "Ben=actor:ACTOR_ID"                            # your own cloned voices

env: MIRAGEA_API_KEY (voices), LLM_API_KEY or OPENAI_API_KEY, LLM_BASE_URL, LLM_MODEL (script writing).
docs: https://github.com/RTY2345RR/open-podcast-generator"""


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:48].strip("-") or "episode"


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def add_host_args(p: argparse.ArgumentParser) -> None:
    g = p.add_argument_group("hosts and voices")
    g.add_argument("--host", action="append", metavar="NAME=VOICE",
                   help="a host and their voice: NAME=https://clip.wav (voice clone from a 6-15 s public clip) "
                        "or NAME=actor:ID (a saved voice). Repeat per host. Default: Alex (male) and Sam (female) demo voices.")
    g.add_argument("--ref-text", action="append", metavar="NAME=TEXT",
                   help="exact words spoken in NAME's reference clip (best cloning quality)")
    g.add_argument("--hosts-file", metavar="hosts.json", help="JSON list of hosts: name, voice, reference_text, role, speed")


def add_llm_args(p: argparse.ArgumentParser) -> None:
    g = p.add_argument_group("script writing (any OpenAI-compatible LLM)")
    g.add_argument("--minutes", type=float, default=5, help="target episode length in minutes (default 5)")
    g.add_argument("--style", default="conversational",
                   help=f"one of {', '.join(STYLES)} or your own description (default conversational)")
    g.add_argument("--language", default="English", help="episode language (default English)")
    g.add_argument("--instructions", default="", help="extra guidance, e.g. 'focus on the methods section'")
    g.add_argument("--llm-base-url", help="default $LLM_BASE_URL or https://api.openai.com/v1 (Ollama: http://localhost:11434/v1)")
    g.add_argument("--llm-model", help="default $LLM_MODEL or gpt-4o-mini")
    g.add_argument("--title", help="episode title (default: the source title)")


def add_render_args(p: argparse.ArgumentParser) -> None:
    g = p.add_argument_group("audio and publishing")
    g.add_argument("-o", "--out", help="output folder (default ./podcast-<title>)")
    g.add_argument("--model", choices=["expressive", "standard"], help="voice model (default: the engine's default)")
    g.add_argument("--concurrency", type=int, default=4, help="lines voiced in parallel (default 4)")
    g.add_argument("--show", dest="show_title", default="", help="podcast (show) name for ID3 and RSS")
    g.add_argument("--author", default="open-podcast-generator", help="author for ID3 and RSS")
    g.add_argument("--base-url", default="", help="public URL where you will upload the folder; used in feed.xml")
    g.add_argument("--cover", type=Path, help="cover art (square JPG/PNG) embedded in the MP3 and the feed")
    g.add_argument("--intro", type=Path, help="audio file (music/jingle) to play before the conversation")
    g.add_argument("--outro", type=Path, help="audio file to play after the conversation")
    g.add_argument("--episode", type=int, help="episode number for RSS")
    g.add_argument("--bitrate", default="128k", help="MP3 bitrate (default 128k)")
    g.add_argument("--no-normalize", action="store_true", help="skip -16 LUFS loudness normalization")
    g.add_argument("--dry-run", action="store_true", help="show the plan, call no voice API")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="open-podcast-generator",
        description="Open-source AI podcast generator: turn a PDF, URL or text into a two-host podcast with "
                    "cloned voices, transcript, chapters and an RSS feed item.",
        epilog=EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    make = sub.add_parser("make", help="source -> script -> voiced episode (all in one)",
                          formatter_class=argparse.RawDescriptionHelpFormatter, epilog=EPILOG)
    make.add_argument("source", help="PDF, URL, YouTube link, .txt/.md/.html file, '-' for stdin, or quoted text")
    add_llm_args(make)
    add_host_args(make)
    add_render_args(make)

    scr = sub.add_parser("script", help="write the podcast script only (edit it, then `render`)")
    scr.add_argument("source")
    scr.add_argument("-o", "--out", help="script file to write (default script.md)")
    add_llm_args(scr)
    add_host_args(scr)

    ren = sub.add_parser("render", help="voice a Markdown script with each host's cloned voice")
    ren.add_argument("script", help="script.md ('Name: text' lines, '## Chapter' headings)")
    add_host_args(ren)
    add_render_args(ren)

    voices = sub.add_parser("voices", help="list saved voices (actor ids) on your Miragea account")
    voices.add_argument("--limit", type=int, default=50)
    voices.add_argument("--offset", type=int, default=0)

    sub.add_parser("doctor", help="check ffmpeg, keys and the voice API")
    return parser


def hosts_from(args: argparse.Namespace) -> list[Host]:
    return load_hosts(args.host, args.ref_text, args.hosts_file)


def make_script(args: argparse.Namespace, hosts: list[Host]) -> Script:
    source = load_source(args.source)
    log(f"Read {len(source.text.split()):,} words from {source.kind}: {source.title}")
    settings = load_llm_settings(args.llm_base_url, args.llm_model)
    if not settings.api_key and "localhost" not in settings.base_url and "127.0.0.1" not in settings.base_url:
        raise ConfigError("Writing the script needs an LLM: set LLM_API_KEY (or OPENAI_API_KEY), or point "
                          "--llm-base-url at a local server such as Ollama. Or write the script yourself and use `render`.")
    log(f"Writing a {args.minutes:g}-minute {args.style} script with {settings.model}...")
    script = write_script(source, hosts, settings, minutes=args.minutes, language=args.language,
                          style=args.style, instructions=args.instructions)
    if args.title:
        script.title = args.title
    log(f"Script: {len(script.chapters)} chapters, {len(script.lines)} lines, {script.word_count():,} words")
    return script


def options_from(args: argparse.Namespace) -> RenderOptions:
    for name in ("cover", "intro", "outro"):
        path = getattr(args, name)
        if path and not path.is_file():
            raise ConfigError(f"--{name} file not found: {path}")
    return RenderOptions(model=args.model, concurrency=max(1, min(16, args.concurrency)), author=args.author,
                         show_title=args.show_title, base_url=args.base_url, cover=args.cover, intro=args.intro,
                         outro=args.outro, bitrate=args.bitrate, normalize=not args.no_normalize,
                         episode=args.episode)


def do_render(script: Script, hosts: list[Host], args: argparse.Namespace) -> int:
    options = options_from(args)
    out = Path(args.out or f"podcast-{slugify(script.title)}").expanduser()
    if args.dry_run:
        pieces = plan(script, hosts, options)
        minutes = script.word_count() / WORDS_PER_MINUTE
        print(json.dumps({
            "title": script.title, "out": str(out), "chapters": [c.title for c in script.chapters],
            "lines": len(script.lines), "voice_requests": len(pieces), "words": script.word_count(),
            "estimated_minutes": round(minutes, 1),
            "hosts": {h.name: h.describe() for h in hosts},
        }, indent=2))
        return 0
    with MirageaClient(load_miragea_settings()) as client:
        result = render(script, hosts, client, out, options, log=log)
    log(f"Done: {result.duration / 60:.1f} min episode, {result.voiced} lines voiced, {result.reused} reused.")
    for name, path in result.files.items():
        print(path)
    return 0


def cmd_doctor() -> int:
    ok = True
    try:
        log(f"ffmpeg: {ffmpeg_exe()}")
    except MediaError as exc:
        ok = False
        log(f"ffmpeg: MISSING ({exc})")
    llm = load_llm_settings()
    log(f"LLM: {llm.base_url} model={llm.model} key={'set' if llm.api_key else 'not set'}")
    if not os.environ.get("MIRAGEA_API_KEY"):
        log(f"MIRAGEA_API_KEY: not set (create one at {API_KEY_PAGE})")
        return 1
    try:
        with MirageaClient(load_miragea_settings()) as client:
            caps = client.capabilities()
        models = [m.get("id") for m in (caps.get("audio") or {}).get("models", [])]
        log(f"Voice API: OK, models: {', '.join(map(str, models))}")
    except MirageaError as exc:
        ok = False
        log(f"Voice API: {exc}")
    return 0 if ok else 1


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "doctor":
            return cmd_doctor()
        if args.command == "voices":
            with MirageaClient(load_miragea_settings()) as client:
                print(json.dumps(client.list_actors(args.limit, args.offset), indent=2, ensure_ascii=False))
            return 0
        hosts = hosts_from(args)
        if args.command == "script":
            script = make_script(args, hosts)
            out = Path(args.out or "script.md")
            out.write_text(script.to_markdown(), encoding="utf-8")
            print(out)
            log("Edit the script if you like, then run: open-podcast-generator render " + str(out))
            return 0
        if args.command == "render":
            text = Path(args.script).expanduser().read_text(encoding="utf-8")
            script = parse_script(text, [h.name for h in hosts] if (args.host or args.hosts_file) else None)
            return do_render(script, hosts, args)
        script = make_script(args, hosts)
        out = Path(args.out or f"podcast-{slugify(script.title)}").expanduser()
        out.mkdir(parents=True, exist_ok=True)
        (out / "script.md").write_text(script.to_markdown(), encoding="utf-8")
        args.out = str(out)
        return do_render(script, hosts, args)
    except (ConfigError, SourceError, ScriptError, LLMError, MediaError, MirageaError, OSError) as exc:
        log(f"error: {exc}")
        return 2
    except KeyboardInterrupt:
        log("Interrupted. Re-run the same command to resume; finished lines are kept.")
        return 130


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
