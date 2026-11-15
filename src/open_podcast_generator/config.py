"""Settings and host (voice) configuration, read from the environment and CLI flags."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlsplit

DEFAULT_MIRAGEA_BASE_URL = "https://miragea.ai/v1"
API_KEY_PAGE = "https://miragea.ai/workspace/api"
INSTALL_GUIDE = "https://miragea.ai/skills/miragea-audio/install.md"
DEFAULT_LLM_BASE_URL = "https://api.openai.com/v1"
DEFAULT_LLM_MODEL = "gpt-4o-mini"

# Two public-domain-style demo voices from the CMU ARCTIC corpus (see CREDITS.md).
# They make the tool work with zero voice setup; replace them with your own clips.
ARCTIC_REFERENCE_TEXT = "For the twentieth time that evening the two men shook hands."
DEFAULT_HOSTS: list[dict[str, str]] = [
    {
        "name": "Alex",
        "voice": "http://festvox.org/cmu_arctic/cmu_arctic/cmu_us_bdl_arctic/wav/arctic_a0003.wav",
        "reference_text": ARCTIC_REFERENCE_TEXT,
        "role": "curious host who asks the questions",
    },
    {
        "name": "Sam",
        "voice": "http://festvox.org/cmu_arctic/cmu_arctic/cmu_us_slt_arctic/wav/arctic_a0003.wav",
        "reference_text": ARCTIC_REFERENCE_TEXT,
        "role": "expert co-host who explains",
    },
]

MISSING_KEY_MESSAGE = (
    "MIRAGEA_API_KEY is not set. Each host is voiced by the Miragea voice-cloning engine: create a key at "
    f"{API_KEY_PAGE} and run `export MIRAGEA_API_KEY=...` (never commit it)."
)


class ConfigError(Exception):
    """Invalid configuration. Messages never contain secrets."""


def validate_base_url(url: str, name: str = "MIRAGEA_BASE_URL") -> str:
    url = url.strip().rstrip("/")
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc or parts.username or parts.password or parts.query or parts.fragment:
        raise ConfigError(f"{name} must be an http(s) API root such as {DEFAULT_MIRAGEA_BASE_URL}, got {url!r}.")
    return url


@dataclass
class Host:
    """One podcast host and the voice that speaks their lines."""

    name: str
    actor_id: Optional[str] = None
    reference_audio_url: Optional[str] = None
    reference_text: Optional[str] = None
    role: str = ""
    speed: Optional[float] = None

    def __post_init__(self) -> None:
        self.name = self.name.strip()
        if not self.name or ":" in self.name or "=" in self.name:
            raise ConfigError(f"Host name {self.name!r} must be non-empty and contain no ':' or '='.")
        if bool(self.actor_id) == bool(self.reference_audio_url):
            raise ConfigError(f"Host {self.name}: give exactly one voice, either actor:ID or a reference audio URL.")
        if self.reference_audio_url:
            scheme = urlsplit(self.reference_audio_url).scheme
            if scheme not in ("http", "https"):
                raise ConfigError(
                    f"Host {self.name}: the reference audio must be a public http(s) URL the voice engine can "
                    "download (6-15 s of clean speech). Upload a local file to your own storage first."
                )
        if self.speed is not None and not 0.25 <= self.speed <= 4:
            raise ConfigError(f"Host {self.name}: speed must be between 0.25 and 4.")

    @classmethod
    def from_spec(cls, spec: str) -> "Host":
        """Parse `NAME=VOICE`, where VOICE is `actor:ID` or an http(s) URL of a clip to clone."""
        if "=" not in spec:
            raise ConfigError(f"--host expects NAME=VOICE (VOICE is actor:ID or a clip URL), got {spec!r}.")
        name, voice = spec.split("=", 1)
        return cls.from_dict({"name": name, "voice": voice})

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Host":
        voice = str(data.get("voice") or "").strip()
        actor = data.get("actor_id")
        url = data.get("reference_audio_url")
        if voice.startswith("actor:"):
            actor = voice[len("actor:"):].strip()
        elif voice:
            url = voice
        speed = data.get("speed")
        return cls(
            name=str(data.get("name") or ""),
            actor_id=str(actor).strip() if actor else None,
            reference_audio_url=str(url).strip() if url else None,
            reference_text=(str(data["reference_text"]).strip() or None) if data.get("reference_text") else None,
            role=str(data.get("role") or ""),
            speed=float(speed) if speed not in (None, "") else None,
        )

    def voice_payload(self) -> dict[str, Any]:
        if self.actor_id:
            payload: dict[str, Any] = {"actor_id": self.actor_id}
        else:
            payload = {"reference_audio_url": self.reference_audio_url}
            if self.reference_text:
                payload["reference_text"] = self.reference_text
        if self.speed:
            payload["speed"] = self.speed
        return payload

    def describe(self) -> str:
        return f"actor:{self.actor_id}" if self.actor_id else str(self.reference_audio_url)


def default_hosts() -> list[Host]:
    return [Host.from_dict(h) for h in DEFAULT_HOSTS]


def load_hosts(specs: Optional[list[str]] = None, ref_texts: Optional[list[str]] = None,
               hosts_file: Optional[str] = None) -> list[Host]:
    """Hosts from a JSON file, then `--host` flags; the CMU ARCTIC demo pair when neither is given."""
    hosts: list[Host] = []
    if hosts_file:
        raw = json.loads(Path(hosts_file).expanduser().read_text(encoding="utf-8"))
        items = raw.get("hosts", raw) if isinstance(raw, dict) else raw
        if not isinstance(items, list):
            raise ConfigError("The hosts file must be a JSON list of hosts or {\"hosts\": [...]}.")
        hosts = [Host.from_dict(h) for h in items]
    for spec in specs or []:
        host = Host.from_spec(spec)
        hosts = [h for h in hosts if h.name.lower() != host.name.lower()] + [host]
    if not hosts:
        hosts = default_hosts()
    texts: dict[str, str] = {}
    for item in ref_texts or []:
        if "=" not in item:
            raise ConfigError(f"--ref-text expects NAME=TEXT, got {item!r}.")
        name, text = item.split("=", 1)
        texts[name.strip().lower()] = text.strip()
    for host in hosts:
        if host.name.lower() in texts:
            host.reference_text = texts.pop(host.name.lower())
    if texts:
        raise ConfigError(f"--ref-text names unknown hosts: {', '.join(texts)}.")
    names = [h.name.lower() for h in hosts]
    if len(set(names)) != len(names):
        raise ConfigError("Host names must be unique.")
    if not 1 <= len(hosts) <= 4:
        raise ConfigError("Use between 1 and 4 hosts (two is the classic podcast format).")
    return hosts


@dataclass(frozen=True)
class MirageaSettings:
    api_key: str
    base_url: str
    wait_timeout: float = 900.0
    http_timeout: float = 60.0

    def __repr__(self) -> str:
        return f"MirageaSettings(api_key={'***' if self.api_key else '<unset>'}, base_url={self.base_url!r})"

    __str__ = __repr__


def load_miragea_settings() -> MirageaSettings:
    key = os.environ.get("MIRAGEA_API_KEY", "").strip()
    if not key:
        raise ConfigError(MISSING_KEY_MESSAGE)
    return MirageaSettings(
        api_key=key,
        base_url=validate_base_url(os.environ.get("MIRAGEA_BASE_URL", "") or DEFAULT_MIRAGEA_BASE_URL),
        wait_timeout=float(os.environ.get("MIRAGEA_WAIT_TIMEOUT", "") or 900),
    )


@dataclass(frozen=True)
class LLMSettings:
    base_url: str
    api_key: str
    model: str
    temperature: float = 0.8
    extra_headers: dict[str, str] = field(default_factory=dict)

    def __repr__(self) -> str:
        return f"LLMSettings(base_url={self.base_url!r}, model={self.model!r}, api_key={'***' if self.api_key else '<unset>'})"

    __str__ = __repr__


def load_llm_settings(base_url: Optional[str] = None, model: Optional[str] = None,
                      api_key: Optional[str] = None) -> LLMSettings:
    """Any OpenAI-compatible /chat/completions endpoint: OpenAI, OpenRouter, Groq, DeepSeek, Ollama, LM Studio, vLLM."""
    url = base_url or os.environ.get("LLM_BASE_URL") or os.environ.get("OPENAI_BASE_URL") or DEFAULT_LLM_BASE_URL
    key = api_key or os.environ.get("LLM_API_KEY") or os.environ.get("OPENAI_API_KEY") or ""
    return LLMSettings(
        base_url=validate_base_url(url, "LLM_BASE_URL"),
        api_key=key.strip(),
        model=model or os.environ.get("LLM_MODEL") or DEFAULT_LLM_MODEL,
    )
