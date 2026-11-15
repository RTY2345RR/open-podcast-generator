import json
import subprocess
from pathlib import Path

import httpx
import pytest

from open_podcast_generator.media import ffmpeg_exe


def tone_wav(freq: int, seconds: float) -> bytes:
    """A short sine tone as WAV bytes (stands in for a voiced line)."""
    return subprocess.run(
        [ffmpeg_exe(), "-loglevel", "error", "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={seconds}",
         "-ac", "1", "-ar", "24000", "-f", "wav", "-"], capture_output=True, check=True).stdout


class FakeMiragea:
    """Mock of the voice API: POST /audio/generations -> task -> audio_url download."""

    def __init__(self, fail_texts=()):
        self.created = []
        self.keys = []
        self.polls = 0
        self.fail_texts = set(fail_texts)
        self.tasks = {}

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "POST" and path.endswith("/audio/generations"):
            body = json.loads(request.content)
            key = request.headers.get("Idempotency-Key")
            assert key, "Idempotency-Key header is required"
            assert request.headers["Authorization"] == "Bearer test-key"
            assert "Mozilla" in request.headers["User-Agent"]
            self.created.append(body)
            self.keys.append(key)
            tid = f"t{len(self.created)}"
            self.tasks[tid] = body
            return httpx.Response(202, json={"task_id": tid, "status": "queued", "poll_after_seconds": 0.01})
        if request.method == "GET" and "/tasks/" in path:
            tid = path.rsplit("/", 1)[1]
            self.polls += 1
            body = self.tasks[tid]
            if body["text"] in self.fail_texts:
                return httpx.Response(200, json={"task_id": tid, "status": "failed",
                                                 "error": {"code": "synthesis_failed", "message": "bad clip"}})
            return httpx.Response(200, json={"task_id": tid, "status": "succeeded", "kind": "audio_generation",
                                             "result": {"audio_url": f"https://cdn.example/{tid}.wav"}})
        if path.endswith("/capabilities"):
            return httpx.Response(200, json={"audio": {"models": [{"id": "expressive"}]}})
        if path.endswith("/actors"):
            return httpx.Response(200, json={"data": [{"id": "a1", "name": "Narrator"}], "has_more": False})
        if request.url.host == "cdn.example":
            assert "Authorization" not in request.headers, "API key must not leak to download host"
            n = int(path.strip("/t").split(".")[0])
            return httpx.Response(200, content=tone_wav(200 + 40 * n, 0.6 + 0.1 * n))
        return httpx.Response(404, json={"error": {"code": "not_found", "message": path}})


@pytest.fixture
def fake():
    return FakeMiragea()


@pytest.fixture
def client(fake):
    from open_podcast_generator.config import MirageaSettings
    from open_podcast_generator.miragea import MirageaClient

    c = MirageaClient(MirageaSettings("test-key", "https://api.example/v1"),
                      transport=httpx.MockTransport(fake.handler), sleep=lambda s: None)
    yield c
    c.close()


SCRIPT = """---
title: Why the sky is blue
description: Two hosts explain Rayleigh scattering.
---
## Cold open
Alex: So why is the sky blue?
Sam: Sunlight scatters off air molecules (laughs) and blue scatters the most.
[pause 0.5]
## The physics
Alex: Shorter wavelengths scatter more.
Sam: Exactly, roughly with the fourth power of frequency.
"""


@pytest.fixture
def script_text():
    return SCRIPT
