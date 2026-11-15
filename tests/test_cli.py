import json

import httpx
import pytest

from open_podcast_generator import cli
from open_podcast_generator.config import MirageaSettings
from open_podcast_generator.miragea import MirageaClient


def test_version(capsys):
    with pytest.raises(SystemExit):
        cli.main(["--version"])
    assert "0.1.0" in capsys.readouterr().out


def test_render_dry_run(tmp_path, capsys, script_text):
    f = tmp_path / "script.md"
    f.write_text(script_text)
    assert cli.main(["render", str(f), "--dry-run"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["voice_requests"] == 4 and data["chapters"] == ["Cold open", "The physics"]


def test_missing_key_message(tmp_path, monkeypatch, capsys, script_text):
    monkeypatch.delenv("MIRAGEA_API_KEY", raising=False)
    f = tmp_path / "script.md"
    f.write_text(script_text)
    assert cli.main(["render", str(f), "-o", str(tmp_path / "o")]) == 2
    assert "MIRAGEA_API_KEY" in capsys.readouterr().err


def test_make_without_llm_key_suggests_render(tmp_path, monkeypatch, capsys):
    for k in ("LLM_API_KEY", "OPENAI_API_KEY", "LLM_BASE_URL", "OPENAI_BASE_URL"):
        monkeypatch.delenv(k, raising=False)
    f = tmp_path / "notes.txt"
    f.write_text("word " * 40)
    assert cli.main(["make", str(f)]) == 2
    assert "render" in capsys.readouterr().err


def test_make_full_flow(tmp_path, monkeypatch, fake):
    llm_reply = "## Hook\nAlex: Why is the sky blue?\nSam: Rayleigh scattering.\n## Wrap\nAlex: Thanks for listening."

    def handler(request):
        if request.url.host == "llm.example":
            return httpx.Response(200, json={"choices": [{"message": {"content": llm_reply}}]})
        return fake.handler(request)

    transport = httpx.MockTransport(handler)
    real_client = httpx.Client

    def patched(*a, **kw):
        kw["transport"] = transport
        return real_client(*a, **kw)

    monkeypatch.setattr(httpx, "Client", patched)
    monkeypatch.setenv("MIRAGEA_API_KEY", "test-key")
    monkeypatch.setenv("MIRAGEA_BASE_URL", "https://api.example/v1")
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    f = tmp_path / "notes.txt"
    f.write_text("The sky is blue because of Rayleigh scattering. " * 10)
    out = tmp_path / "out"
    rc = cli.main(["make", str(f), "--llm-base-url", "https://llm.example/v1", "-o", str(out), "--title", "Sky"])
    assert rc == 0
    assert (out / "episode.mp3").exists() and (out / "script.md").read_text().count("Alex:") == 2
    assert len(fake.created) == 3


def test_voices(monkeypatch, capsys, fake):
    real = MirageaClient.__init__

    def init(self, settings, **kw):
        kw["transport"] = httpx.MockTransport(fake.handler)
        real(self, settings, **kw)

    monkeypatch.setattr(MirageaClient, "__init__", init)
    monkeypatch.setenv("MIRAGEA_API_KEY", "test-key")
    assert cli.main(["voices"]) == 0
    assert "Narrator" in capsys.readouterr().out
