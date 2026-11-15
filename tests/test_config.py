import pytest

from open_podcast_generator.config import ConfigError, Host, load_hosts, load_llm_settings


def test_default_hosts_are_male_and_female_demo_voices():
    hosts = load_hosts()
    assert [h.name for h in hosts] == ["Alex", "Sam"]
    assert "bdl" in hosts[0].reference_audio_url and "slt" in hosts[1].reference_audio_url
    assert hosts[0].reference_text


def test_host_specs_and_ref_text():
    hosts = load_hosts(["Ana=https://x.example/a.wav", "Ben=actor:abc"], ["Ana=Hello world"])
    assert hosts[0].voice_payload() == {"reference_audio_url": "https://x.example/a.wav", "reference_text": "Hello world"}
    assert hosts[1].voice_payload() == {"actor_id": "abc"}


def test_hosts_file(tmp_path):
    f = tmp_path / "hosts.json"
    f.write_text('{"hosts": [{"name": "Kim", "voice": "actor:k1", "speed": 1.1, "role": "skeptic"}]}')
    hosts = load_hosts(hosts_file=str(f))
    assert hosts[0].voice_payload() == {"actor_id": "k1", "speed": 1.1}


@pytest.mark.parametrize("spec", ["NoEquals", "Ana=/local/file.wav", "Ana="])
def test_bad_host_specs(spec):
    with pytest.raises(ConfigError):
        load_hosts([spec])


def test_unknown_ref_text_name():
    with pytest.raises(ConfigError):
        load_hosts(["Ana=actor:1"], ["Bob=hi"])


def test_llm_settings_env(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "http://localhost:11434/v1/")
    monkeypatch.setenv("LLM_MODEL", "llama3.1")
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret")
    s = load_llm_settings()
    assert s.base_url == "http://localhost:11434/v1" and s.model == "llama3.1" and s.api_key == "sk-secret"
    assert "sk-secret" not in repr(s)
