import json

import httpx
import pytest

from open_podcast_generator.config import LLMSettings, load_hosts
from open_podcast_generator.llm import LLMError, build_messages, write_script
from open_podcast_generator.sources import Source

SRC = Source("Rayleigh scattering explains the blue sky. " * 20, "Blue sky", "https://example.com", "web")
GOOD = "<think>plan</think>```markdown\n## Hook\nAlex: Why is the sky blue?\nSam: Scattering.\n## Wrap\nAlex: Thanks!\n```"


def settings():
    return LLMSettings("https://llm.example/v1", "sk-test", "gpt-test")


def test_prompt_mentions_hosts_and_length():
    msgs = build_messages(SRC, load_hosts(), minutes=10, style="debate")
    assert "Alex, Sam" in msgs[0]["content"]
    assert "1500 words" in msgs[1]["content"] and "debate" in msgs[1]["content"]


def test_write_script_parses_and_strips_wrappers():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        seen["auth"] = request.headers["Authorization"]
        assert request.url.path == "/v1/chat/completions"
        return httpx.Response(200, json={"choices": [{"message": {"content": GOOD}}]})

    s = write_script(SRC, load_hosts(), settings(), transport=httpx.MockTransport(handler))
    assert seen["body"]["model"] == "gpt-test" and seen["auth"] == "Bearer sk-test"
    assert [c.title for c in s.chapters] == ["Hook", "Wrap"]
    assert s.title == "Blue sky" and s.meta["source"] == "https://example.com"


def test_write_script_repairs_bad_format_once():
    replies = iter(["Here is a podcast about the sky without names.", GOOD])
    calls = []

    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": next(replies)}}]})

    s = write_script(SRC, load_hosts(), settings(), transport=httpx.MockTransport(handler))
    assert len(calls) == 2 and len(s.lines) == 3


def test_llm_http_error_redacts_key():
    transport = httpx.MockTransport(lambda r: httpx.Response(401, text="bad key sk-test"))
    with pytest.raises(LLMError) as err:
        write_script(SRC, load_hosts(), settings(), transport=transport)
    assert "sk-test" not in str(err.value) and "LLM_API_KEY" in str(err.value)
