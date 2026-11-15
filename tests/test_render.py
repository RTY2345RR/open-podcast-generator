import json
import xml.dom.minidom

import pytest

from open_podcast_generator.config import load_hosts
from open_podcast_generator.media import ffmpeg_exe
from open_podcast_generator.miragea import MirageaError
from open_podcast_generator.render import RenderOptions, plan, render
from open_podcast_generator.script import parse_script


def test_plan_payloads(script_text):
    pieces = plan(parse_script(script_text), load_hosts(), RenderOptions(model="expressive"))
    assert len(pieces) == 4
    p = pieces[0].payload
    assert p["text"] == "So why is the sky blue?" and p["format"] == "wav" and p["model"] == "expressive"
    assert "bdl" in p["reference_audio_url"] and p["reference_text"]
    assert "slt" in pieces[1].payload["reference_audio_url"]


def test_plan_rejects_unknown_speaker():
    from open_podcast_generator.config import ConfigError

    with pytest.raises(ConfigError):
        plan(parse_script("Zed: hello there"), load_hosts(), RenderOptions())


def test_render_end_to_end(tmp_path, client, fake, script_text):
    script = parse_script(script_text)
    out = tmp_path / "ep"
    opts = RenderOptions(base_url="https://pod.example/ep1", show_title="Sky Show", author="Me", episode=3)
    result = render(script, load_hosts(), client, out, opts, log=lambda m: None)
    assert len(fake.created) == 4 and len(set(fake.keys)) == 4
    for name in ("episode.mp3", "transcript.srt", "transcript.vtt", "transcript.txt", "transcript.json",
                 "chapters.json", "show-notes.md", "feed-item.xml", "feed.xml", "script.md"):
        assert (out / name).exists(), name
    assert result.duration > 3
    chapters = json.loads((out / "chapters.json").read_text())["chapters"]
    assert [c["title"] for c in chapters] == ["Cold open", "The physics"]
    assert chapters[0]["startTime"] == 0 and chapters[1]["startTime"] > 1
    segs = json.loads((out / "transcript.json").read_text())["segments"]
    assert [s["speaker"] for s in segs] == ["Alex", "Sam", "Alex", "Sam"]
    assert all(a["endTime"] <= b["startTime"] for a, b in zip(segs, segs[1:]))
    srt = (out / "transcript.srt").read_text()
    assert srt.startswith("1\n00:00:00,300 --> ") and "Alex: So why is the sky blue?" in srt
    assert "<v Sam>" in (out / "transcript.vtt").read_text()
    feed = (out / "feed.xml").read_text()
    xml.dom.minidom.parseString(feed)  # well-formed
    assert 'url="https://pod.example/ep1/episode.mp3"' in feed and "<itunes:episode>3</itunes:episode>" in feed
    assert "podcast:chapters" in feed and "podcast:transcript" in feed
    assert "0:00 Cold open" in (out / "show-notes.md").read_text()
    # ID3 chapters are embedded in the MP3.
    import subprocess

    info = subprocess.run([ffmpeg_exe(), "-hide_banner", "-i", str(out / "episode.mp3")], capture_output=True).stderr.decode()
    assert "Chapter #0:1" in info and "Sky Show" in info


def test_rerun_reuses_clips_and_only_revoices_edits(tmp_path, client, fake, script_text):
    out = tmp_path / "ep"
    render(parse_script(script_text), load_hosts(), client, out, RenderOptions(), log=lambda m: None)
    assert len(fake.created) == 4
    render(parse_script(script_text), load_hosts(), client, out, RenderOptions(), log=lambda m: None)
    assert len(fake.created) == 4  # nothing new submitted
    edited = script_text.replace("Shorter wavelengths scatter more.", "Short waves scatter more.")
    result = render(parse_script(edited), load_hosts(), client, out, RenderOptions(), log=lambda m: None)
    assert len(fake.created) == 5 and result.voiced == 1 and result.reused == 3


def test_failed_line_reports_and_resumes(tmp_path, client, fake, script_text):
    fake.fail_texts = {"Shorter wavelengths scatter more."}
    out = tmp_path / "ep"
    with pytest.raises(MirageaError) as err:
        render(parse_script(script_text), load_hosts(), client, out, RenderOptions(), log=lambda m: None)
    assert "1 of 4 lines" in str(err.value)
    fake.fail_texts = set()
    result = render(parse_script(script_text), load_hosts(), client, out, RenderOptions(), log=lambda m: None)
    assert result.voiced == 1 and (out / "episode.mp3").exists()


def test_resumes_pending_task_id_without_resubmitting(tmp_path, client, fake, script_text):
    out = tmp_path / "ep"
    script = parse_script(script_text)
    pieces = plan(script, load_hosts(), RenderOptions())
    # Simulate an interrupted run: task created, state saved, clip never downloaded.
    task = client.create_speech(pieces[0].payload, "k-1")
    out.mkdir()
    (out / "state.json").write_text(json.dumps({"version": 1, "clips": {pieces[0].key: {"idempotency_key": "k-1", "task_id": task["task_id"]}}}))
    render(script, load_hosts(), client, out, RenderOptions(), log=lambda m: None)
    assert len(fake.created) == 4  # 1 pre-existing + 3 new, not 5
