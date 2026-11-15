"""Small synchronous client for the Miragea voice API (https://miragea.ai/v1).

Endpoints used: GET /capabilities, GET /actors, POST /audio/generations
(with an Idempotency-Key, so a retried request never creates a duplicate job)
and GET /tasks/{id}. Full reference: https://miragea.ai/v1/guide
"""

from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import quote, urlsplit

import httpx

from . import __version__
from .config import API_KEY_PAGE, MirageaSettings

# Some CDNs reject default library user agents, so identify as a normal client.
USER_AGENT = (
    f"Mozilla/5.0 (compatible; open-podcast-generator/{__version__}; "
    "+https://github.com/RTY2345RR/open-podcast-generator)"
)
RETRYABLE_HTTP = (500, 502, 503, 504)
HINTS = {
    400: "Fix the invalid parameter (voice, model, speed or text).",
    401: f"The API key was rejected. Check MIRAGEA_API_KEY or create a key at {API_KEY_PAGE}.",
    403: "This account has no API access right now. Check API access for your Miragea account.",
    404: "Task not found for this account.",
    409: "Idempotency key reused with different input. Delete the line's entry in state.json and re-run.",
    429: "Rate limited or the account limit was reached. Wait and re-run: finished lines are kept.",
}


class MirageaError(Exception):
    def __init__(self, code: str, message: str, *, http_status: Optional[int] = None,
                 retryable: bool = False, task_id: Optional[str] = None, hint: Optional[str] = None) -> None:
        super().__init__(message)
        self.code, self.message, self.http_status = code, message, http_status
        self.retryable, self.task_id, self.hint = retryable, task_id, hint

    def __str__(self) -> str:
        parts = [f"[{self.code}] {self.message}"]
        if self.http_status:
            parts.append(f"(HTTP {self.http_status})")
        if self.hint:
            parts.append(self.hint)
        if self.task_id:
            parts.append(f"task_id={self.task_id}: re-run the same command to resume it.")
        return " ".join(parts)


class MirageaClient:
    def __init__(self, settings: MirageaSettings, *, transport: Optional[httpx.BaseTransport] = None,
                 sleep: Callable[[float], None] = time.sleep, max_retries: int = 3) -> None:
        self.settings = settings
        self._sleep = sleep
        self._retries = max_retries
        self._transport = transport
        self._http = httpx.Client(
            base_url=settings.base_url,
            headers={"Authorization": f"Bearer {settings.api_key}", "Accept": "application/json",
                     "User-Agent": USER_AGENT},
            timeout=settings.http_timeout,
            follow_redirects=False,  # never forward the bearer token elsewhere
            transport=transport,
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "MirageaClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def _request(self, method: str, path: str, *, json_body: Optional[dict] = None,
                 params: Optional[dict] = None, idempotency_key: Optional[str] = None) -> dict[str, Any]:
        headers = {"Idempotency-Key": idempotency_key} if idempotency_key else None
        attempt = 0
        while True:
            try:
                resp = self._http.request(method, path, json=json_body, params=params, headers=headers)
            except httpx.HTTPError as exc:
                if attempt < self._retries:  # safe: GETs are read-only, POSTs reuse their idempotency key
                    attempt += 1
                    self._sleep(1.5 * attempt)
                    continue
                raise MirageaError("request_failed", f"Could not reach the voice API ({type(exc).__name__}).",
                                   retryable=True) from None
            if resp.status_code in RETRYABLE_HTTP and attempt < self._retries:
                attempt += 1
                self._sleep(1.5 * attempt)
                continue
            return self._parse(resp)

    def _parse(self, resp: httpx.Response) -> dict[str, Any]:
        try:
            body = resp.json()
        except ValueError:
            body = None
        if 200 <= resp.status_code < 300 and isinstance(body, dict):
            return body
        err = body.get("error") if isinstance(body, dict) else None
        if isinstance(err, dict):
            code = str(err.get("code") or "api_error")
            message = str(err.get("message") or f"HTTP {resp.status_code}")
            retryable = bool(err.get("retryable"))
        else:
            code, message, retryable = "http_error", f"Voice API returned HTTP {resp.status_code}.", resp.status_code in RETRYABLE_HTTP
        message = message.replace(self.settings.api_key, "***") if self.settings.api_key else message
        raise MirageaError(code, message, http_status=resp.status_code, retryable=retryable,
                           hint=HINTS.get(resp.status_code))

    def capabilities(self) -> dict[str, Any]:
        return self._request("GET", "/capabilities")

    def list_actors(self, limit: int = 20, offset: int = 0) -> dict[str, Any]:
        return self._request("GET", "/actors", params={"limit": limit, "offset": offset})

    def create_speech(self, payload: dict[str, Any], idempotency_key: str) -> dict[str, Any]:
        return self._request("POST", "/audio/generations", json_body=payload, idempotency_key=idempotency_key)

    def get_task(self, task_id: str) -> dict[str, Any]:
        return self._request("GET", "/tasks/" + quote(task_id, safe=""))

    def wait(self, task_id: str, timeout: Optional[float] = None) -> dict[str, Any]:
        deadline = time.monotonic() + (timeout or self.settings.wait_timeout)
        while True:
            task = self.get_task(task_id)
            if task.get("status") in ("succeeded", "failed"):
                return task
            if time.monotonic() >= deadline:
                raise MirageaError("wait_timeout", f"Task is still {task.get('status')}.", retryable=True,
                                   task_id=task_id)
            pause = task.get("poll_after_seconds")
            self._sleep(float(pause) if isinstance(pause, (int, float)) and pause > 0 else 3.0)

    def download(self, url: str, dest: Path) -> Path:
        """Download a result file. The API key is never sent to this URL."""
        if urlsplit(url).scheme not in ("http", "https"):
            raise MirageaError("invalid_result", "The task returned an invalid audio_url.")
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".part")
        try:
            with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=120, follow_redirects=True,
                              transport=self._transport) as plain:
                with plain.stream("GET", url) as resp:
                    if resp.status_code >= 400:
                        raise MirageaError("download_failed", f"Audio download returned HTTP {resp.status_code}.",
                                           retryable=True)
                    with tmp.open("wb") as fh:
                        for chunk in resp.iter_bytes():
                            fh.write(chunk)
        except httpx.HTTPError as exc:
            tmp.unlink(missing_ok=True)
            raise MirageaError("download_failed", f"Audio download failed ({type(exc).__name__}).",
                               retryable=True) from None
        tmp.replace(dest)
        return dest


def new_key() -> str:
    return str(uuid.uuid4())
