"""OpenAI client: one JSON request, standard library only.

The desk computes numbers; this asks a model to read those numbers and argue
about them. Two rules shape the client:

- **A key never reaches an error message.** Nothing here echoes the
  Authorization header or the request body into an exception.
- **A failure is not a result.** A missing key, a refused call, or an answer
  that is not the JSON asked for raises ``LLMError``; the caller keeps its
  deterministic verdict instead of inventing one.

And one lesson the latency audit of 2026-10-28 wrote here: a thinking model
behind a gateway spends one to several minutes generating invisible reasoning
tokens before the answer exists. Requested without streaming, the connection
sits dead-silent the whole time, so any socket timeout kills a call that was
about to succeed — and retrying it tripled the wait, which is where the
"Analisa dengan AI makan 10-15 menit" experience came from. The client now
asks for ``stream: true`` first: chunks start flowing in seconds, the socket
timeout only ever measures a stalled stream, and the total wait becomes the
model's real working time instead of a multiple of the timeout.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MODEL = "gpt-4o-mini"
DEFAULT_BASE = "https://api.openai.com/v1"
# Streaming: the gap between two chunks. A healthy stream ticks every few
# hundred milliseconds, so this only fires when the gateway truly stalls —
# not while the model is thinking, because thinking arrives as chunks too.
_TIMEOUT = 60
_ATTEMPTS = 2


class LLMError(RuntimeError):
    """The model was not configured, refused, or answered unusably."""


class _StreamUnsupported(Exception):
    """The gateway refused ``stream``; fall back to one blocking request."""


ENV_FILE = ROOT / ".env"
_SHELL = dict(os.environ)          # what the process started with
_FILE_KEYS: set[str] = set()
_dotenv_stamp: tuple[float, int] | None = None


def _stamp() -> tuple[float, int] | None:
    """Modification time plus size: a same-tick rewrite still changes one of them."""
    try:
        info = ENV_FILE.stat()
    except OSError:
        return None
    return (info.st_mtime, info.st_size)


def _read_env_file(path: Path) -> dict[str, str]:
    """Parse a .env file, tolerating what editors actually save.

    Handles a UTF-8 BOM (Notepad), CRLF, ``export KEY=value``, quoted values and
    a trailing ``# comment`` — each of which silently breaks a naive parser and
    leaves the operator staring at "AI mati" with a file that looks correct.
    """
    try:
        raw = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return {}
    values: dict[str, str] = {}
    for line in raw.splitlines():
        text = line.strip()
        if not text or text.startswith("#") or "=" not in text:
            continue
        if text.lower().startswith("export "):
            text = text[7:].strip()
        key, _, value = text.partition("=")
        value = value.strip()
        if len(value) > 1 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        else:
            marker = value.find(" #")
            if marker > 0:
                value = value[:marker].strip()
        values[key.strip()] = value
    return values


def _load_dotenv() -> None:
    """Fold ``katalis/.env`` into the environment; a value from the shell wins."""
    global _dotenv_stamp
    _dotenv_stamp = _stamp()
    if _dotenv_stamp is None:
        return
    for key, value in _read_env_file(ENV_FILE).items():
        if key in _SHELL:
            continue                 # an exported variable outranks the file
        os.environ[key] = value
        _FILE_KEYS.add(key)


def _maybe_reload() -> None:
    """Re-read .env when it changed, so saving the file needs no restart.

    The first version read the file once at import, which meant editing it while
    the server ran left the AI off until the process was restarted — a confusing
    silence rather than an error.
    """
    if _stamp() != _dotenv_stamp:
        _load_dotenv()


_load_dotenv()


def _value(name: str) -> str:
    _maybe_reload()
    return (os.environ.get(name) or "").strip()


def api_key() -> str:
    return _value("OPENAI_API_KEY")


def model() -> str:
    return _value("OPENAI_MODEL") or DEFAULT_MODEL


def base_url() -> str:
    return (_value("OPENAI_BASE_URL") or DEFAULT_BASE).rstrip("/")


def _looks_openai(model_name: str) -> bool:
    lowered = model_name.lower()
    return any(token in lowered for token in ("gpt", "chatgpt", "o1", "o3", "o4", "davinci"))


def _commented_base_url() -> bool:
    """True when the file carries a commented OPENAI_BASE_URL — the commonest slip."""
    try:
        lines = ENV_FILE.read_text(encoding="utf-8-sig", errors="replace").splitlines()
    except OSError:
        return False
    return any(line.strip().startswith("#") and "OPENAI_BASE_URL" in line for line in lines)


def diagnose() -> dict:
    """Why the AI layer is off — with the key's length, never the key."""
    _maybe_reload()
    exists = ENV_FILE.exists()
    parsed = _read_env_file(ENV_FILE) if exists else {}
    key, model_name, endpoint = api_key(), model(), base_url()

    warnings: list[str] = []
    if not exists:
        warnings.append("katalis/.env tidak ditemukan; buat dari .env.example atau pakai variabel lingkungan.")
    elif "OPENAI_API_KEY" not in parsed:
        warnings.append("Baris OPENAI_API_KEY tidak ada di .env.")
    elif not parsed.get("OPENAI_API_KEY"):
        warnings.append("OPENAI_API_KEY ada tetapi kosong.")
    if _commented_base_url():
        warnings.append("Baris OPENAI_BASE_URL masih diawali '#', jadi diabaikan. "
                        "Hapus tanda # di awal baris itu.")
    if key and "api.openai.com" in endpoint and not _looks_openai(model_name):
        warnings.append(f"Model '{model_name}' bukan model OpenAI, tetapi endpoint masih "
                        f"api.openai.com. Isi OPENAI_BASE_URL dengan alamat gateway Anda.")
    if key and "api.openai.com" not in endpoint and not endpoint.endswith("/v1"):
        warnings.append(f"Endpoint '{endpoint}' tidak diakhiri '/v1'. Klien memanggil "
                        f"<endpoint>/chat/completions, jadi gateway yang memakai /v1 perlu ditulis.")
    if key and "api.openai.com" in endpoint and len(key) < 40:
        warnings.append(f"Kunci hanya {len(key)} karakter; kunci OpenAI jauh lebih panjang. "
                        "Pastikan tidak terpotong saat menempel.")

    return {
        "env_file": str(ENV_FILE),
        "env_exists": exists,
        "key_in_file": "OPENAI_API_KEY" in parsed,
        "key_length": len(key),
        "model": model_name,
        "endpoint": endpoint,
        "commented_base_url": _commented_base_url(),
        "warnings": warnings,
    }


def status() -> dict:
    """What the UI needs to say about the AI layer — never the key itself."""
    key, model_name, endpoint = api_key(), model(), base_url()
    detail = diagnose()
    host = urllib.parse.urlparse(endpoint).netloc or endpoint
    return {
        "configured": bool(key),
        "model": model_name if key else None,
        "endpoint": host,
        "mode": "openai" if key else "deterministic",
        "warnings": detail["warnings"],
        "hint": None if key else
                "Isi OPENAI_API_KEY di katalis/.env; berkas itu dibaca ulang tanpa restart.",
    }


def _request(payload: dict, key: str, accept: str) -> urllib.request.Request:
    return urllib.request.Request(
        f"{base_url()}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
            "Accept": accept,
            "User-Agent": "katalis/1.2",
        },
        method="POST",
    )


def _post_stream(payload: dict, key: str, on_event=None) -> tuple[str, dict, str | None]:
    """One streaming chat completion; returns (content, usage, model).

    SSE chunks carry the answer piecemeal in ``delta.content`` while the
    model's invisible reasoning arrives as ``delta.reasoning_content`` and is
    dropped — only the finished answer matters here. A gateway that answers
    JSON despite ``stream`` is honoured as a plain completion; one that
    refuses the parameter raises ``_StreamUnsupported`` so the caller can drop
    it without spending a retry.
    """
    streamed = {**payload, "stream": True}
    # Ask for usage in the final chunk; gateways that don't know the option
    # say so in a 400 and we simply drop it and retry the stream.
    if "stream_options" not in streamed:
        streamed["stream_options"] = {"include_usage": True}
    try:
        response = urllib.request.urlopen(
            _request(streamed, key, "text/event-stream"), timeout=_TIMEOUT)
    except urllib.error.HTTPError as exc:
        detail = _error_message(exc).lower()
        if exc.code == 400 and "stream" in detail:
            if "stream_options" in detail:
                streamed.pop("stream_options", None)
                return _post_stream(streamed, key, on_event)
            raise _StreamUnsupported from None
        raise
    with response:
        ctype = (response.headers.get("Content-Type") or "").lower()
        if "text/event-stream" not in ctype:
            # Not SSE after all: the body is a normal completion object.
            body = json.loads(response.read().decode("utf-8"))
            return _content_of(body), body.get("usage") or {}, body.get("model")
        parts: list[str] = []
        usage: dict = {}
        answer_model: str | None = None
        wrote = False
        last_emit = 0.0

        def fire(name: str) -> None:
            nonlocal last_emit
            if on_event:
                try:
                    on_event(name)
                    last_emit = time.monotonic()
                except Exception:          # noqa: BLE001 — progress must never break the call
                    pass

        for raw in response:
            line = raw.decode("utf-8", errors="replace").strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                chunk = json.loads(data)
            except json.JSONDecodeError:
                continue                    # a keepalive or split frame; skip
            if isinstance(chunk.get("usage"), dict) and chunk["usage"]:
                usage = chunk["usage"]
            if not answer_model and isinstance(chunk.get("model"), str):
                answer_model = chunk["model"]
            for choice in chunk.get("choices") or []:
                delta = choice.get("delta") or {}
                piece = delta.get("content")
                if piece:
                    if not wrote:
                        wrote = True
                        fire("writing")
                    parts.append(piece)
                elif (delta.get("reasoning_content") or delta.get("reasoning")) and not wrote:
                    # A long thinking phase must keep the caller's job record
                    # fresh, so the "thinking" event repeats while it runs —
                    # the app refreshes its timestamp and the UI its counter.
                    if time.monotonic() - last_emit >= 30:
                        fire("thinking")
        return "".join(parts), usage, answer_model


def _content_of(body: dict) -> str:
    message = (body.get("choices") or [{}])[0].get("message") or {}
    return message.get("content") or ""


def _post_blocking(payload: dict, key: str) -> tuple[str, dict, str | None]:
    """One plain (non-streaming) completion for gateways without SSE."""
    with urllib.request.urlopen(_request(payload, key, "application/json"),
                                timeout=_TIMEOUT) as response:
        body = json.loads(response.read().decode("utf-8"))
    return _content_of(body), body.get("usage") or {}, body.get("model")


def _complete(payload: dict, key: str, on_event=None) -> tuple[str, dict, str | None]:
    """Stream first; a gateway that cannot stream gets one blocking request."""
    try:
        return _post_stream(payload, key, on_event)
    except _StreamUnsupported:
        return _post_blocking(payload, key)


def _error_message(exc: urllib.error.HTTPError) -> str:
    """OpenAI's own message, which names the model or the quota — never the key."""
    try:
        body = json.loads(exc.read().decode("utf-8"))
        detail = body.get("error") or {}
        return f"HTTP {exc.code}: {detail.get('message') or detail.get('type') or 'permintaan ditolak'}"
    except Exception:  # noqa: BLE001
        return f"HTTP {exc.code}"


def _extract_json(text: str) -> dict:
    """Parse a JSON object from a reply that may be fenced or padded with prose.

    A gateway without native JSON mode answers in markdown often enough that
    failing on the first character would waste an otherwise usable answer.
    """
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[A-Za-z0-9_-]*\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start >= 0 and end > start:
            return json.loads(cleaned[start:end + 1])
        raise


def chat_json(system: str, user: str, *, model_name: str | None = None,
              temperature: float = 0.2, on_event=None) -> dict:
    """One chat completion, parsed as a JSON object.

    ``on_event`` (optional) is called once with ``"thinking"`` when the model
    starts producing reasoning and with ``"writing"`` when the visible answer
    begins — the dashboard uses it to keep the progress line honest.

    Retries a refusal that looks transient (429/5xx/timeout). A 4xx that names a
    problem with the request fails immediately — except a refusal of
    ``response_format`` or ``stream``, which is retried once without it: many
    gateways serve models that have neither native JSON mode nor SSE.
    """
    key = api_key()
    if not key:
        raise LLMError("OPENAI_API_KEY belum diisi")

    payload = {
        "model": model_name or model(),
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": temperature,
        "response_format": {"type": "json_object"},
    }

    failure = "unknown failure"
    for attempt in range(_ATTEMPTS):
        if attempt:
            time.sleep(1.5 * attempt)
        try:
            content, usage, answer_model = _complete(payload, key, on_event)
        except urllib.error.HTTPError as exc:
            failure = _error_message(exc)
            lowered = failure.lower()
            if exc.code == 400 and "response_format" in lowered and "response_format" in payload:
                payload.pop("response_format")      # the model has no JSON mode; ask in prose
                continue
            if exc.code in (408, 409, 429) or exc.code >= 500:
                continue
            raise LLMError(failure) from None
        except (urllib.error.URLError, TimeoutError) as exc:
            failure = f"tidak bisa menghubungi {status()['endpoint']}: {exc.reason if hasattr(exc, 'reason') else exc}"
            continue
        except json.JSONDecodeError as exc:
            failure = f"jawaban bukan JSON: {exc}"
            continue

        if not content:
            failure = "jawaban streaming kosong"
            continue

        try:
            parsed = _extract_json(content)
        except json.JSONDecodeError as exc:
            failure = f"isi jawaban bukan JSON: {exc}"
            continue
        if not isinstance(parsed, dict):
            failure = "jawaban bukan objek JSON"
            continue
        parsed["_model"] = answer_model or payload["model"]
        parsed["_usage"] = usage or {}
        return parsed

    raise LLMError(failure)
