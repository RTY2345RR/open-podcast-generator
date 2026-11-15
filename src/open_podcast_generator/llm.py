"""Write a two-host podcast script with any OpenAI-compatible chat completions endpoint."""

from __future__ import annotations

from typing import Optional

import httpx

from .config import Host, LLMSettings
from .script import Script, ScriptError, parse_script
from .sources import Source

WORDS_PER_MINUTE = 150
MAX_SOURCE_CHARS = 60_000

STYLES = {
    "conversational": "a relaxed, curious conversation between friends who know the subject",
    "explainer": "a clear explainer: one host asks the questions a smart beginner would ask, the other answers with examples",
    "debate": "a friendly debate where the hosts take different sides, then find common ground",
    "news": "a brisk news briefing: what happened, why it matters, what to watch next",
    "study": "a study session that teaches the material, checks understanding and recaps key points",
}

SYSTEM_PROMPT = """You write scripts for an audio podcast with {n} hosts. The script will be read aloud by voice clones, so:
- Write only words that are spoken. No stage directions, sound effects, emojis, markdown emphasis, URLs or bullet lists.
- Every spoken line is `Name: text` on its own line, using exactly these host names: {names}.
- Split the episode into 3-7 chapters. Start each chapter with a line `## Chapter title`.
- Alternate speakers naturally. Keep most lines to 1-3 sentences; vary rhythm with short reactions ("Right.", "Wait, really?").
- Stay faithful to the source. Do not invent facts, numbers or quotes. Say when something is uncertain.
- Open with a hook, not a greeting formula. Close with a short recap and a sign-off.
- Write numbers, abbreviations and symbols the way they should be spoken.
Return only the script, starting with the first `## ` chapter heading."""

USER_PROMPT = """Episode language: {language}
Style: {style}
Target length: about {words} words (~{minutes} minutes of audio).
Hosts:
{hosts}
{extra}
Source title: {title}
Source ({kind}):
<<<
{text}
>>>"""


class LLMError(Exception):
    pass


def build_messages(source: Source, hosts: list[Host], *, minutes: float = 5, language: str = "English",
                   style: str = "conversational", instructions: str = "") -> list[dict[str, str]]:
    names = ", ".join(h.name for h in hosts)
    host_lines = "\n".join(f"- {h.name}: {h.role or 'co-host'}" for h in hosts)
    text = source.text
    if len(text) > MAX_SOURCE_CHARS:
        text = text[:MAX_SOURCE_CHARS] + "\n[source truncated]"
    return [
        {"role": "system", "content": SYSTEM_PROMPT.format(n=len(hosts), names=names)},
        {"role": "user", "content": USER_PROMPT.format(
            language=language, style=STYLES.get(style, style), words=int(minutes * WORDS_PER_MINUTE),
            minutes=f"{minutes:g}", hosts=host_lines,
            extra=f"Extra instructions: {instructions}\n" if instructions else "",
            title=source.title, kind=source.kind, text=text)},
    ]


def chat(settings: LLMSettings, messages: list[dict[str, str]], *,
         transport: Optional[httpx.BaseTransport] = None, timeout: float = 300.0) -> str:
    headers = {"Content-Type": "application/json", "User-Agent": "open-podcast-generator"}
    if settings.api_key:
        headers["Authorization"] = f"Bearer {settings.api_key}"
    body = {"model": settings.model, "messages": messages, "temperature": settings.temperature}
    try:
        with httpx.Client(timeout=timeout, transport=transport) as client:
            resp = client.post(settings.base_url + "/chat/completions", json=body, headers=headers)
    except httpx.HTTPError as exc:
        raise LLMError(f"Could not reach the LLM at {settings.base_url} ({type(exc).__name__}).") from None
    if resp.status_code >= 400:
        detail = resp.text[:300].replace(settings.api_key, "***") if settings.api_key else resp.text[:300]
        hint = " Set LLM_API_KEY (or OPENAI_API_KEY)." if resp.status_code == 401 else ""
        raise LLMError(f"LLM returned HTTP {resp.status_code}: {detail}{hint}")
    try:
        content = resp.json()["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError):
        raise LLMError("The LLM response had no choices[0].message.content.") from None
    if not isinstance(content, str) or not content.strip():
        raise LLMError("The LLM returned an empty script.")
    # Reasoning models sometimes wrap output in <think> blocks or code fences.
    if "</think>" in content:
        content = content.split("</think>", 1)[1]
    return content.replace("```markdown", "").replace("```", "").strip()


def write_script(source: Source, hosts: list[Host], settings: LLMSettings, *, minutes: float = 5,
                 language: str = "English", style: str = "conversational", instructions: str = "",
                 transport: Optional[httpx.BaseTransport] = None) -> Script:
    messages = build_messages(source, hosts, minutes=minutes, language=language, style=style,
                              instructions=instructions)
    raw = chat(settings, messages, transport=transport)
    try:
        script = parse_script(raw, [h.name for h in hosts])
    except ScriptError:
        # One repair attempt: show the model its output and the format rule.
        messages += [{"role": "assistant", "content": raw},
                     {"role": "user", "content": "Rewrite it in the required format: `## Chapter` headings and "
                      f"`Name: text` lines using only {', '.join(h.name for h in hosts)}."}]
        script = parse_script(chat(settings, messages, transport=transport), [h.name for h in hosts])
    script.title = source.title if source.title and source.title != "Untitled" else script.title
    script.meta.update({"source": source.origin, "language": language, "style": style})
    return script
