"""Local demo UI: static files + workspace JSON + Hermes chat proxy."""

from __future__ import annotations

import errno
import json
import os
import re
import sys
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterator
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.infrastructure.database import DEFAULT_DB_PATH, Database
from src.infrastructure.excel_loader import load_pantry
from src.services.workflow_service import WorkflowService

WEB_DIR = ROOT / "web"
XLSX = ROOT / "data" / "despensa_dona_maria.xlsx"
DEMO_PROMPT = ROOT / "scripts" / "demo-prompt.txt"
HERMES_MD = ROOT / ".hermes.md"
HERMES_HOME = Path.home() / ".hermes"
HERMES_ENV = HERMES_HOME / ".env"
HERMES_CONFIG = HERMES_HOME / "config.yaml"
PLACEHOLDER_KEYS = {"", "sabor-da-maria-local", "change-me-local-dev"}

MIME = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
}

TOOL_LABELS = {
    "web_search": "Busca na internet",
    "web_extract": "Leitura da página",
    "search": "Busca na internet",
    "search_web": "Busca na internet",
    "describe": "Leitura da página",
    "extract": "Leitura da página",
    "register_recipe": "Cadastro da receita",
    "record_feedback": "Feedback do prato",
    "record_kitchen_capability": "Resposta da cozinha",
    "confirm_ingredient_match": "Ingrediente",
    "submit_market_quotes": "Cotações",
    "prepare_recipe_pricing": "Precificação",
    "accept_recipe": "Aceite no cardápio",
    "get_workspace_status": "Status da despensa",
    "initialize_workspace": "Carregar despensa",
    "reset_workspace": "Reset do workspace",
}
TOOL_ALIASES = {
    "search": "web_search",
    "websearch": "web_search",
    "search_web": "web_search",
    "describe": "web_extract",
    "extract": "web_extract",
    "web_fetch": "web_extract",
    "fetch": "web_extract",
}
HERMES_META_TOOLS = {"tool_search", "tool_describe", "tool_call", "_thinking"}
SKIP_TOOLS = {
    "",
    "tool",
    "thinking",
    "terminal",
    "bash",
    "shell",
    "execute",
    "python",
    "sleep",
    "wait",
}
TOOL_STATUS_LABELS = {
    "web_search": "Buscando na internet…",
    "web_extract": "Lendo a receita…",
    "register_recipe": "Cadastrando o prato…",
    "record_feedback": "Anotando seu interesse…",
    "record_kitchen_capability": "Anotando a cozinha…",
    "confirm_ingredient_match": "Conferindo ingrediente…",
    "submit_market_quotes": "Buscando preços…",
    "prepare_recipe_pricing": "Fechando o custo…",
    "accept_recipe": "Colocando no cardápio…",
    "get_workspace_status": "Olhando a despensa…",
    "initialize_workspace": "Carregando a despensa…",
    "reset_workspace": "Resetando o workspace…",
}
TERMINAL_EVENTS = {
    "done",
    "run.completed",
    "run.failed",
}

_OPERATIONAL_NARRATION_RE = re.compile(
    r"^(?:(?:ótimo|perfeito|certo|excelente)[!.]?\s+)?"
    r"(?:deix[ae]\s+(?:eu|me)\b|(?:agora\s+)?vou\b)",
    re.IGNORECASE,
)
_INTERNAL_TECHNICAL_RE = re.compile(
    r"(?:problema:|erro:|entendi[,.]? o servidor|o servidor (?:está|pediu)|"
    r"o serviço (?:do |de )?sabor da maria (?:está|ficou))|"
    r"\b(?:schema|market_quotes|servidor mcp|web search|tool retornou|"
    r"parâmetros?)\b",
    re.IGNORECASE,
)


def _is_internal_narration(text: str) -> bool:
    """Keep operational scratchpad/tool recovery out of Dona Maria's chat."""
    cleaned = (text or "").strip()
    if _INTERNAL_TECHNICAL_RE.search(cleaned):
        return True
    return bool(
        len(cleaned) <= 240
        and "\n" not in cleaned
        and _OPERATIONAL_NARRATION_RE.match(cleaned)
    )

_workflow_lock = threading.Lock()
_hermes_stream_lock = threading.Lock()
_workflow: WorkflowService | None = None
_sessions_api: bool | None = None


def _load_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("'").strip('"')
    return values


def _load_config_yaml_key() -> str:
    if not HERMES_CONFIG.is_file():
        return ""
    for raw in HERMES_CONFIG.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith("API_SERVER_KEY:"):
            return line.split(":", 1)[1].strip().strip("'").strip('"')
    return ""


def _usable_key(value: str | None) -> str:
    cleaned = (value or "").strip().strip("'").strip('"')
    if cleaned in PLACEHOLDER_KEYS:
        return ""
    return cleaned


def _hydrate_env() -> None:
    for key, value in _load_dotenv(HERMES_ENV).items():
        if key == "API_SERVER_KEY" and not _usable_key(os.environ.get(key)):
            os.environ[key] = value
            continue
        os.environ.setdefault(key, value)
    config_key = _load_config_yaml_key()
    if config_key and not _usable_key(os.environ.get("API_SERVER_KEY")):
        os.environ["API_SERVER_KEY"] = config_key


def hermes_url() -> str:
    explicit = os.environ.get("HERMES_API_URL")
    if explicit:
        return explicit.rstrip("/")
    host = os.environ.get("API_SERVER_HOST", "127.0.0.1")
    if host in {"0.0.0.0", "::"}:
        host = "127.0.0.1"
    port = os.environ.get("API_SERVER_PORT", "8642")
    return f"http://{host}:{port}"


def hermes_key() -> str:
    return (
        _usable_key(os.environ.get("API_SERVER_KEY"))
        or _usable_key(os.environ.get("HERMES_API_KEY"))
        or _usable_key(_load_dotenv(HERMES_ENV).get("API_SERVER_KEY"))
        or _usable_key(_load_config_yaml_key())
    )


def demo_prompt() -> str:
    return DEMO_PROMPT.read_text(encoding="utf-8").strip()


def protocol_prompt() -> str:
    if HERMES_MD.is_file():
        return HERMES_MD.read_text(encoding="utf-8").strip()
    return ""


def get_workflow() -> WorkflowService:
    global _workflow
    if _workflow is None:
        db_path = ROOT / os.environ.get("SABOR_DB_PATH", DEFAULT_DB_PATH)
        _workflow = WorkflowService(Database(db_path))
    return _workflow


def workspace_payload() -> dict[str, Any]:
    with _workflow_lock:
        status = get_workflow().get_workspace_status()
    return {
        "initialized": status.initialized,
        "remaining_budget": status.remaining_budget,
        "initial_budget": status.initial_budget,
        "committed_budget": status.committed_budget,
        "accepted_menu": [item.model_dump() for item in status.accepted_menu],
        "candidates": [
            {
                "recipe_id": recipe.recipe_id,
                "recipe_name": recipe.recipe_name,
                "accepted": recipe.accepted,
                "interested": recipe.interested,
                "feasibility_status": recipe.feasibility_status,
            }
            for recipe in status.recipes
            if not recipe.accepted
        ],
    }


def reset_workspace_state() -> dict[str, Any]:
    pantry = load_pantry(XLSX)
    with _workflow_lock:
        get_workflow().reset_workspace(pantry)
    return workspace_payload()


def short_tool_name(name: str) -> str:
    cleaned = (name or "tool").strip()
    lowered = cleaned.lower()
    if lowered.startswith("tool "):
        rest = cleaned[5:].strip()
        if rest.lower() not in {"search", "describe", "call"}:
            cleaned = rest
            lowered = cleaned.lower()
    if lowered.startswith("tool_"):
        rest = cleaned[5:].strip()
        if rest.lower() not in {"search", "describe", "call"}:
            cleaned = rest
    for prefix in (
        "sabor_da_maria__",
        "sabor_da_maria_",
        "mcp_sabor_da_maria_",
        "mcp__",
        "mcp_",
    ):
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix) :]
            break
    if "__" in cleaned:
        cleaned = cleaned.split("__")[-1]
    return cleaned


def canonical_tool_name(name: str) -> str:
    short = short_tool_name(name)
    return TOOL_ALIASES.get(short.lower(), short)


def is_hermes_meta_tool(name: str) -> bool:
    raw = (name or "").strip().lower().replace(" ", "_")
    short = short_tool_name(name).lower()
    return raw in HERMES_META_TOOLS or short in HERMES_META_TOOLS


def skip_tool(name: str) -> bool:
    short = canonical_tool_name(name).lower()
    return not short or short.startswith("_") or short in SKIP_TOOLS


def is_hidden_tool(name: str) -> bool:
    return is_hermes_meta_tool(name) or skip_tool(name)


def tool_status_label(name: str) -> str:
    canonical = canonical_tool_name(name)
    return TOOL_STATUS_LABELS.get(
        canonical,
        TOOL_STATUS_LABELS.get(canonical.lower(), "Pensando…"),
    )


def tool_label(name: str) -> str:
    short = canonical_tool_name(name)
    return TOOL_LABELS.get(short, TOOL_LABELS.get(short.lower(), short.replace("_", " ")))


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def summarize_tool(name: str, args: Any, preview: str | None) -> str:
    text = str(preview or "").strip()
    lowered = text.lower()
    canonical = canonical_tool_name(name)
    if lowered.startswith("tool "):
        rest = lowered[5:].strip().replace(" ", "_")
        rest_canonical = TOOL_ALIASES.get(rest, rest)
        if rest_canonical in {canonical.lower(), short_tool_name(name).lower()}:
            text = ""
    if lowered in {
        canonical.lower(),
        short_tool_name(name).lower(),
        tool_label(name).lower(),
    }:
        text = ""
    if text:
        return text[:160]
    data = _as_dict(args)
    if canonical in {"web_search", "search_web"}:
        return str(data.get("query") or data.get("q") or "")[:160]
    if canonical == "register_recipe":
        recipe = data.get("recipe")
        if isinstance(recipe, dict):
            return str(recipe.get("name") or "")[:160]
    if canonical == "accept_recipe":
        return str(
            data.get("recipe_name")
            or data.get("selected_label")
            or data.get("recipe_id")
            or ""
        )[:160]
    if canonical == "record_feedback":
        interested = data.get("interested")
        if interested is True:
            return "quer testar"
        if interested is False:
            return "não quer testar"
    for key in ("name", "recipe_id", "ingredient", "query"):
        if data.get(key):
            return str(data[key])[:160]
    return ""


def _headers() -> dict[str, str]:
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    key = hermes_key()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    return headers


def hermes_request(
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    timeout: int = 30,
) -> tuple[int, Any]:
    payload = None if body is None else json.dumps(body).encode("utf-8")
    request = Request(
        f"{hermes_url()}{path}",
        data=payload,
        headers=_headers(),
        method=method,
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read()
            if not raw:
                return response.status, {}
            try:
                return response.status, json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError:
                return response.status, raw.decode("utf-8", errors="replace")
    except HTTPError as error:
        raw = error.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = {"error": raw or error.reason}
        return error.code, parsed
    except URLError as error:
        return 0, {"error": str(error.reason or error)}


def hermes_health() -> dict[str, Any]:
    status, body = hermes_request("GET", "/health", timeout=3)
    if status == 0:
        return {
            "ok": False,
            "hermes": False,
            "error": (
                "Hermes API não está no ar em "
                f"{hermes_url()}. Rode ./scripts/demo-ui.sh "
                "com API_SERVER_ENABLED=true."
            ),
        }
    if status != 200:
        return {
            "ok": False,
            "hermes": False,
            "error": f"Hermes respondeu {status}: {body}",
        }
    if not hermes_key():
        return {
            "ok": False,
            "hermes": False,
            "error": (
                "API_SERVER_KEY não encontrada. Defina com "
                "`hermes config set API_SERVER_KEY ...` e reinicie o gateway."
            ),
        }
    auth_status, _ = hermes_request("GET", "/v1/models", timeout=5)
    if auth_status in {401, 403}:
        return {
            "ok": False,
            "hermes": False,
            "error": (
                "A API_SERVER_KEY da UI não bate com a do gateway. "
                "A UI agora lê ~/.hermes/config.yaml; reinicie "
                "./scripts/demo-ui.sh."
            ),
        }
    return {"ok": True, "hermes": True}


def sessions_supported() -> bool:
    global _sessions_api
    if _sessions_api is not None:
        return _sessions_api
    status, body = hermes_request("GET", "/v1/capabilities", timeout=5)
    if status == 200 and isinstance(body, dict):
        features = body.get("features") or {}
        endpoints = body.get("endpoints") or {}
        if endpoints.get("session_chat_stream") or features.get(
            "session_chat_stream"
        ):
            _sessions_api = True
            return True
        if features.get("session_chat_stream") is False:
            _sessions_api = False
            return False
    _sessions_api = status not in {0, 404, 405}
    return _sessions_api


def _error_code(data: Any) -> str:
    if not isinstance(data, dict):
        return ""
    nested = data.get("error")
    if isinstance(nested, dict):
        return str(nested.get("code") or "")
    return str(data.get("code") or "")


def _session_id_from(data: Any) -> str:
    if not isinstance(data, dict):
        return ""
    session = data.get("session") or data
    if not isinstance(session, dict):
        return ""
    return str(session.get("id") or session.get("session_id") or "")


def create_session() -> str:
    prompt = protocol_prompt()
    bodies: list[dict[str, Any]] = [
        {
            "title": f"Sabor da Maria {uuid.uuid4().hex[:8]}",
            "source": "api_server",
        },
        {"source": "api_server"},
    ]
    if prompt:
        for body in bodies:
            body["system_prompt"] = prompt
    if sessions_supported():
        last_status = 0
        last_detail: Any = None
        for body in bodies:
            status, data = hermes_request("POST", "/api/sessions", body, timeout=10)
            session_id = _session_id_from(data)
            if status in {200, 201} and session_id:
                return session_id
            last_status, last_detail = status, data
            code = _error_code(data)
            if status in {404, 405}:
                break
            if status == 400 and code == "invalid_title":
                continue
            raise RuntimeError(
                f"Não criou sessão Hermes ({status}): {last_detail}"
            )
        if last_status not in {0, 404, 405}:
            raise RuntimeError(
                f"Não criou sessão Hermes ({last_status}): {last_detail}"
            )
    return f"sabor_{uuid.uuid4().hex}"


def _parse_sse_chunks(raw: bytes) -> Iterator[tuple[str, str]]:
    event = "message"
    data_lines: list[str] = []
    keepalive = False
    for line in raw.decode("utf-8", errors="replace").splitlines():
        if line == "":
            if data_lines:
                yield event, "\n".join(data_lines)
            elif keepalive:
                yield "keepalive", ""
            event = "message"
            data_lines = []
            keepalive = False
            continue
        if line.startswith(":"):
            keepalive = True
            continue
        if line.startswith("event:"):
            event = line[6:].strip()
            continue
        if line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
    if data_lines:
        yield event, "\n".join(data_lines)
    elif keepalive:
        yield "keepalive", ""


def _tool_name(payload: dict[str, Any]) -> str:
    return str(
        payload.get("tool_name")
        or payload.get("tool")
        or payload.get("name")
        or ""
    )


def _status_event(phase: str, label: str) -> dict[str, Any]:
    return {"event": "status", "data": {"phase": phase, "label": label}}


def _snapshot_delta(text: str, *, commit: bool = False) -> dict[str, Any]:
    data: dict[str, Any] = {"text": text, "replace": True}
    if commit:
        data["commit"] = True
    return {"event": "delta", "data": data}


def _tool_payload(payload: dict[str, Any], status: str) -> dict[str, Any] | None:
    name = _tool_name(payload)
    if is_hidden_tool(name):
        return None
    canonical = canonical_tool_name(name)
    mapped = "started" if status in {"running", "started"} else status
    return {
        "id": str(payload.get("id") or payload.get("tool_call_id") or ""),
        "name": canonical,
        "label": tool_label(canonical),
        "status": mapped,
        "summary": summarize_tool(
            canonical,
            payload.get("args") or payload.get("arguments"),
            payload.get("preview") or payload.get("label"),
        ),
    }


def _map_tool_event(payload: dict[str, Any], status: str) -> list[dict[str, Any]]:
    name = _tool_name(payload)
    if is_hidden_tool(name):
        return [_status_event("thinking", "Pensando…")]
    out: list[dict[str, Any]] = []
    if status in {"started", "running"}:
        out.append(_status_event("tool", tool_status_label(name)))
    else:
        out.append(_status_event("thinking", "Pensando…"))
    mapped = _tool_payload(payload, status)
    if mapped:
        out.append({"event": "tool", "data": mapped})
    return out


def _assistant_delta_text(payload: dict[str, Any]) -> tuple[str, bool]:
    """Return (text, cumulative). Incremental `delta` wins over `content`."""
    raw = payload.get("delta")
    token = ""
    if isinstance(raw, str):
        token = raw
    elif isinstance(raw, dict):
        token = str(raw.get("content") or raw.get("text") or "")
    if token:
        return token, False
    content = payload.get("content")
    if isinstance(content, str) and content:
        return content, True
    text = payload.get("text")
    if isinstance(text, str) and text:
        return text, False
    return "", False


def map_hermes_event(event: str, data: str) -> list[dict[str, Any]]:
    try:
        payload = json.loads(data) if data else {}
    except json.JSONDecodeError:
        payload = {"text": data}
    if not isinstance(payload, dict):
        payload = {"text": data}

    if event in {"keepalive", "run.started", "message.started"}:
        return [_status_event("thinking", "Pensando…")]
    if event in {"assistant.delta", "message.delta"}:
        text, cumulative = _assistant_delta_text(payload)
        if not text:
            return []
        data_out: dict[str, Any] = {"text": text}
        if cumulative:
            data_out["replace"] = True
        return [{"event": "delta", "data": data_out}]
    if event in {"tool.started", "tool.completed", "tool.failed"}:
        return _map_tool_event(payload, event.split(".", 1)[-1])
    if event == "hermes.tool.progress":
        return _map_tool_event(payload, str(payload.get("status") or "running"))
    if event == "assistant.completed":
        text = _assistant_text_from_completed(payload)
        return [_snapshot_delta(text, commit=True)] if text else []
    if event in {"error", "run.failed"}:
        out = [{
            "event": "error",
            "data": {"message": "Não consegui concluir agora. Tente novamente."},
        }]
        if event == "run.failed":
            text = _assistant_text_from_completed(payload)
            if text:
                out.insert(0, _snapshot_delta(text))
            out.append(
                {
                    "event": "done",
                    "data": {
                        "session_id": payload.get("session_id"),
                        "complete": True,
                    },
                }
            )
        return out
    if event in TERMINAL_EVENTS:
        out: list[dict[str, Any]] = []
        text = _assistant_text_from_completed(payload)
        if text:
            out.append(_snapshot_delta(text))
        out.append(
            {
                "event": "done",
                "data": {
                    "session_id": payload.get("session_id"),
                    "complete": True,
                },
            }
        )
        return out
    if event == "message" and payload.get("object") == "chat.completion.chunk":
        out = []
        choices = payload.get("choices") or []
        if choices:
            delta = (choices[0].get("delta") or {}).get("content") or ""
            if delta:
                out.append({"event": "delta", "data": {"text": delta}})
            if choices[0].get("finish_reason"):
                out.append({"event": "done", "data": {"complete": True}})
        return out
    return []


def _session_stream_request(session_id: str, message: str) -> Request:
    body: dict[str, Any] = {"input": message}
    prompt = protocol_prompt()
    if prompt:
        body["instructions"] = prompt
        body["system_message"] = prompt
    return Request(
        f"{hermes_url()}/api/sessions/{session_id}/chat/stream",
        data=json.dumps(body).encode("utf-8"),
        headers=_headers(),
        method="POST",
    )


def _completions_stream_request(session_id: str, message: str) -> Request:
    prompt = protocol_prompt()
    messages: list[dict[str, str]] = []
    if prompt:
        messages.append({"role": "system", "content": prompt})
    messages.append({"role": "user", "content": message})
    return Request(
        f"{hermes_url()}/v1/chat/completions",
        data=json.dumps(
            {
                "model": "hermes-agent",
                "stream": True,
                "messages": messages,
            }
        ).encode("utf-8"),
        headers={**_headers(), "X-Hermes-Session-Id": session_id},
        method="POST",
    )


def _iter_sse(response: Any) -> Iterator[tuple[str, str]]:
    """Yield complete SSE frames without waiting for an arbitrary byte size.

    ``HTTPResponse.read(1024)`` may block until 1 KiB accumulates. Hermes emits
    many small events, so that turned a live stream into long, bursty updates.
    Reading protocol lines preserves frame boundaries and releases each event
    as soon as its terminating blank line arrives.
    """
    frame_lines: list[bytes] = []
    while True:
        line = response.readline()
        if not line:
            break
        frame_lines.append(line)
        if line in {b"\n", b"\r\n"}:
            yield from _parse_sse_chunks(b"".join(frame_lines))
            frame_lines = []
    if frame_lines:
        yield from _parse_sse_chunks(b"".join(frame_lines) + b"\n")


def _assistant_text_from_completed(payload: dict[str, Any]) -> str:
    text = str(payload.get("content") or payload.get("text") or "").strip()
    if text:
        return text
    message = payload.get("message")
    if isinstance(message, str) and message.strip():
        return message.strip()
    if isinstance(message, dict):
        nested = str(message.get("content") or message.get("text") or "").strip()
        if nested:
            return nested
    messages = payload.get("messages")
    if not isinstance(messages, list):
        return ""
    for item in reversed(messages):
        if not isinstance(item, dict):
            continue
        if str(item.get("role") or "") != "assistant":
            continue
        content = item.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()
        if isinstance(content, list):
            parts = [
                str(part.get("text") or "")
                for part in content
                if isinstance(part, dict)
            ]
            joined = "".join(parts).strip()
            if joined:
                return joined
    return ""


def _messages_from_transcript(body: Any) -> list[Any]:
    if isinstance(body, list):
        return body
    if not isinstance(body, dict):
        return []
    messages = body.get("messages") or body.get("items") or body.get("data")
    return messages if isinstance(messages, list) else []

def _message_text(item: Any) -> str:
    if not isinstance(item, dict):
        return ""

    content = item.get("content")

    if isinstance(content, str):
        return content.strip()

    if isinstance(content, list):
        parts: list[str] = []

        for part in content:
            if isinstance(part, str):
                parts.append(part)
                continue

            if not isinstance(part, dict):
                continue

            text = (
                part.get("text")
                or part.get("content")
            )

            if isinstance(text, str):
                parts.append(text)

        return "".join(parts).strip()

    text = item.get("text")

    if text is not None:
        return str(text).strip()

    return ""

def session_transcript(
    session_id: str,
) -> dict[str, Any]:

    status, body = hermes_request(
        "GET",
        (
            f"/api/sessions/"
            f"{session_id}/messages"
            "?limit=200"
        ),
        timeout=10,
    )

    raw_messages = (
        _messages_from_transcript(body)
    )

    messages: list[
        dict[str, str]
    ] = []

    for item in raw_messages:
        if not isinstance(
            item,
            dict,
        ):
            continue

        role = str(
            item.get("role")
            or ""
        ).strip().lower()

        if role not in {
            "user",
            "assistant",
        }:
            continue

        #
        # Hermes pode persistir
        # mensagens internas que não
        # deveriam aparecer na UI.
        #
        if (
            str(
                item.get(
                    "display_kind"
                )
                or ""
            ).lower()
            == "hidden"
        ):
            continue

        text = _message_text(
            item
        )

        if not text:
            continue
        if role == "assistant" and _is_internal_narration(text):
            continue

        messages.append(
            {
                "id": str(
                    item.get("id")
                    or item.get(
                        "message_id"
                    )
                    or ""
                ),
                "role": role,
                "content": text,
            }
        )

    latest_assistant = next(
        (
            item["content"]
            for item in reversed(
                messages
            )
            if item["role"]
            == "assistant"
        ),
        "",
    )

    error = ""

    if status != 200:
        if isinstance(
            body,
            dict,
        ):
            error = str(
                body.get("error")
                or body.get(
                    "message"
                )
                or ""
            )

        elif body:
            error = str(body)

    return {
        "ok": status == 200,
        "session_id": session_id,
        "messages": messages,

        #
        # Pode manter por
        # compatibilidade com código
        # antigo.
        #
        "text": latest_assistant,

        "error": error,
    }


def merge_assistant_text(
    current: str, incoming: str, *, snapshot: bool = False
) -> str:
    """Join Hermes speech like the CLI: append tokens, keep cumulative text.

    Do not treat a token as a duplicate just because those letters already
    appear somewhere in the message ("ca" in "verificando", " " after the
    first space). That is what ate syllables in the UI.
    """
    cur = current or ""
    inc = incoming or ""
    if not inc:
        return cur
    if not cur:
        return inc
    if inc == cur:
        return cur
    if inc.startswith(cur):
        return inc
    if cur.startswith(inc):
        return cur
    if snapshot:
        if inc in cur:
            return cur
        if cur in inc:
            return inc
        return cur
    if cur.endswith(inc):
        return cur
    return cur + inc


def _unfinished_speech(text: str) -> bool:
    stripped = (text or "").rstrip()
    if not stripped:
        return False
    return stripped[-1] not in ".!?…:\""


def _looks_new_utterance(chunk: str) -> bool:
    stripped = (chunk or "").lstrip("\n")
    if not stripped:
        return False
    first = stripped[0]
    if chunk.startswith("\n") and first.isupper():
        return True
    return first.isupper() and len(stripped) >= 12


def _complete_token_prefix(text: str) -> str:
    """Hide a trailing partial token until a boundary or final snapshot."""
    if not text or text[-1].isspace() or text[-1] in ".,;:!?…)]}\"'":
        return text
    boundary = max(text.rfind(" "), text.rfind("\n"), text.rfind("\t"))
    if boundary < 0:
        return ""
    return text[:boundary].rstrip()


class TurnAssembler:
    """One Hermes run: one bubble per assistant message, not per tool."""

    def __init__(self) -> None:
        self.segments: list[str] = [""]
        self.awaiting_next_message = False
        self.tool_boundary_pending = False
        self.visible_segments: set[int] = set()
        self.published_text: dict[int, str] = {}
        self.saw_tool = False
        self.terminal_segment: int | None = None
        self.tool_sequence = 0
        self.active_tool_ids: dict[str, list[str]] = {}

    @property
    def index(self) -> int:
        return len(self.segments) - 1

    def _open_next_message_if_needed(self, chunk: str) -> None:
        if self.tool_boundary_pending and self.segments[-1]:
            self.segments.append("")
            self.tool_boundary_pending = False
            self.awaiting_next_message = False
            self.terminal_segment = self.index
            return
        if not self.awaiting_next_message or not self.segments[-1]:
            return
        if _unfinished_speech(self.segments[-1]) and not _looks_new_utterance(chunk):
            self.awaiting_next_message = False
            return
        self.segments.append("")
        self.awaiting_next_message = False

    def mark_tool_boundary(self) -> None:
        """The next assistant delta belongs to a post-tool message."""
        self.saw_tool = True
        self.terminal_segment = None
        if self.segments[-1].strip():
            self.tool_boundary_pending = True

    def timeline_tool_id(
        self, name: str, status: str, provided_id: str = ""
    ) -> str:
        """Give repeated tool calls distinct IDs while updating completions."""
        canonical = canonical_tool_name(name)
        if provided_id:
            return provided_id
        active = self.active_tool_ids.setdefault(canonical, [])
        if status in {"started", "running"}:
            self.tool_sequence += 1
            generated = f"{canonical}:{self.tool_sequence}"
            active.append(generated)
            return generated
        if active:
            return active.pop(0)
        self.tool_sequence += 1
        return f"{canonical}:{self.tool_sequence}"

    def ingest_delta(self, chunk: str) -> tuple[int, str] | None:
        if not chunk:
            return None
        self._open_next_message_if_needed(chunk)
        merged = merge_assistant_text(self.segments[-1], chunk)
        if merged == self.segments[-1]:
            return None
        self.segments[-1] = merged
        if self.saw_tool and self.terminal_segment is None:
            self.terminal_segment = self.index
        return self.index, merged

    def ingest_snapshot(
        self, text: str, *, commit: bool = False
    ) -> tuple[int, str] | None:
        if not text:
            return None
        idx = self.index
        merged = merge_assistant_text(self.segments[idx], text, snapshot=True)
        changed = merged != self.segments[idx]
        self.segments[idx] = merged
        if commit and self.segments[idx].strip():
            self.awaiting_next_message = True
        if changed:
            return idx, merged
        return None

    def has_text(self) -> bool:
        return any(segment.strip() for segment in self.segments)

    def has_terminal_response(self) -> bool:
        """Require useful assistant text after the most recent tool call."""
        if not self.saw_tool:
            return any(
                segment.strip() and not _is_internal_narration(segment)
                for segment in self.segments
            )
        if self.terminal_segment is None:
            return False
        text = self.segments[self.terminal_segment]
        return bool(text.strip() and not _is_internal_narration(text))


def iter_browser_events(
    hermes_event: str, data: str, assembler: TurnAssembler
) -> Iterator[dict[str, Any]]:
    if hermes_event == "tool.started":
        assembler.mark_tool_boundary()
    elif hermes_event == "hermes.tool.progress":
        try:
            progress = json.loads(data) if data else {}
        except json.JSONDecodeError:
            progress = {}
        if str(progress.get("status") or "").lower() in {"started", "running"}:
            assembler.mark_tool_boundary()

    for mapped in map_hermes_event(hermes_event, data):
        kind = mapped["event"]
        payload = dict(mapped["data"])
        if kind == "tool":
            payload["id"] = assembler.timeline_tool_id(
                str(payload.get("name") or "tool"),
                str(payload.get("status") or "running"),
                str(payload.get("id") or ""),
            )
            mapped = {"event": kind, "data": payload}
        if kind == "delta":
            text = str(payload.get("text") or "")
            commit_requested = bool(payload.get("commit")) or (
                hermes_event in TERMINAL_EVENTS
            )
            if payload.get("replace"):
                result = assembler.ingest_snapshot(
                    text, commit=bool(payload.get("commit"))
                )
            else:
                result = assembler.ingest_delta(text)
            if result is None:
                if not commit_requested or not assembler.segments[-1].strip():
                    continue
                idx, merged = assembler.index, assembler.segments[-1]
            else:
                idx, merged = result
            if _is_internal_narration(merged):
                continue
            visible_text = (
                merged
                if commit_requested
                else _complete_token_prefix(merged)
            )
            # Hold only the first few characters, long enough to recognize
            # internal narration such as "Deixa eu...". Once a segment is
            # visible, publish every cumulative update immediately.
            ready_to_stream = (
                idx in assembler.visible_segments
                or len(visible_text.strip()) >= 16
                or "\n" in visible_text
            )
            if (
                not visible_text
                or (not commit_requested and not ready_to_stream)
                or assembler.published_text.get(idx) == visible_text
            ):
                continue
            assembler.visible_segments.add(idx)
            assembler.published_text[idx] = visible_text
            yield {
                "event": "delta",
                "data": {
                    "text": visible_text,
                    "replace": True,
                    "segment": idx,
                },
            }
            continue
        if kind == "done":
            for idx, text in enumerate(assembler.segments):
                if (
                    idx in assembler.visible_segments
                    or not text.strip()
                    or _is_internal_narration(text)
                ):
                    continue
                assembler.visible_segments.add(idx)
                yield {
                    "event": "delta",
                    "data": {"text": text, "replace": True, "segment": idx},
                }
            if payload.get("complete") and not assembler.has_terminal_response():
                # An upstream terminal marker is not enough: after tools, the
                # user must receive a real final response before replying.
                continue
        yield mapped


def stream_hermes_chat(session_id: str, message: str) -> Iterator[bytes]:
    global _sessions_api

    def frame(event: str, payload: dict[str, Any]) -> bytes:
        return (
            f"event: {event}\n"
            f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
        ).encode("utf-8")

    def pump(response: Any) -> bool:
        assembler = TurnAssembler()
        completed = False
        for event, data in _iter_sse(response):
            for mapped in iter_browser_events(event, data, assembler):
                yield frame(mapped["event"], mapped["data"])
                if mapped["event"] == "done" and mapped["data"].get("complete"):
                    completed = True
                    return True
        return completed

    yield frame("session", {"session_id": session_id})
    last_error: str | None = None
    fallback = not sessions_supported()
    completed = False
    with _hermes_stream_lock:
        if sessions_supported():
            try:
                with urlopen(
                    _session_stream_request(session_id, message), timeout=600
                ) as response:
                    completed = yield from pump(response)
                    if completed:
                        return
            except HTTPError as error:
                raw = error.read().decode("utf-8", errors="replace")
                last_error = f"Hermes {error.code}: {raw[:400] or error.reason}"
                if error.code in {404, 405}:
                    _sessions_api = False
                    fallback = True
                    last_error = None
                else:
                    yield frame(
                        "error",
                        {"message": "Não consegui concluir agora. Tente novamente."},
                    )
                    yield frame(
                        "done", {"session_id": session_id, "complete": False}
                    )
                    return
            except URLError as error:
                last_error = (
                    "Não conectou no Hermes API "
                    f"({hermes_url()}): {error.reason}"
                )
                yield frame(
                    "error",
                    {"message": "Não consegui concluir agora. Tente novamente."},
                )
                yield frame("done", {"session_id": session_id, "complete": False})
                return
        if fallback:
            try:
                with urlopen(
                    _completions_stream_request(session_id, message), timeout=600
                ) as response:
                    completed = yield from pump(response)
                    if completed:
                        return
            except HTTPError as error:
                raw = error.read().decode("utf-8", errors="replace")
                last_error = f"Hermes {error.code}: {raw[:400] or error.reason}"
            except URLError as error:
                last_error = (
                    "Não conectou no Hermes API "
                    f"({hermes_url()}): {error.reason}"
                )
    if last_error:
        yield frame(
            "error",
            {"message": "Não consegui concluir agora. Tente novamente."},
        )
    elif not completed:
        yield frame(
            "error",
            {
                "message": (
                    "O fluxo do agente encerrou antes de terminar o turno."
                )
            },
        )
    yield frame("done", {"session_id": session_id, "complete": False})


def _read_json(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    length = int(handler.headers.get("Content-Length") or "0")
    raw = handler.rfile.read(length) if length else b"{}"
    if not raw:
        return {}
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("JSON object expected")
    return data


def _send_json(
    handler: BaseHTTPRequestHandler,
    status: int,
    payload: dict[str, Any],
) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _safe_static(path: str) -> Path | None:
    relative = path.lstrip("/") or "index.html"
    target = (WEB_DIR / relative).resolve()
    try:
        target.relative_to(WEB_DIR.resolve())
    except ValueError:
        return None
    if target.is_file():
        return target
    return None


class DemoHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def handle(self) -> None:
        try:
            super().handle()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            return

    def handle_one_request(self) -> None:
        try:
            super().handle_one_request()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            self.close_connection = True

    def log_message(self, format: str, *args: Any) -> None:
        sys.stderr.write("%s - %s\n" % (self.address_string(), format % args))

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/api/health":
            _send_json(self, 200, hermes_health())
            return
        if path == "/api/workspace":
            _send_json(self, 200, workspace_payload())
            return
        if path == "/api/transcript":
            session_id = (parse_qs(parsed.query).get("session_id") or [""])[0].strip()
            if not session_id:
                _send_json(self, 400, {"error": "session_id is required"})
                return
            _send_json(self, 200, session_transcript(session_id))
            return
        target = _safe_static("/index.html" if path == "/" else path)
        if target is None:
            _send_json(self, 404, {"error": "not found"})
            return
        data = target.read_bytes()
        self.send_response(200)
        self.send_header(
            "Content-Type",
            MIME.get(target.suffix, "application/octet-stream"),
        )
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self) -> None:
        path = self.path.split("?", 1)[0]
        streaming = False
        try:
            if path == "/api/reset":
                workspace = reset_workspace_state()
                session_id = create_session()
                _send_json(
                    self,
                    200,
                    {
                        "session_id": session_id,
                        "demo_prompt": demo_prompt(),
                        "workspace": workspace,
                    },
                )
                return
            if path == "/api/session":
                _send_json(self, 200, {"session_id": create_session()})
                return
            if path == "/api/chat":
                body = _read_json(self)
                message = str(body.get("message") or "").strip()
                if not message:
                    _send_json(self, 400, {"error": "message is required"})
                    return
                session_id = str(body.get("session_id") or "").strip()
                if not session_id:
                    session_id = create_session()
                self.send_response(200)
                self.send_header(
                    "Content-Type", "text/event-stream; charset=utf-8"
                )
                self.send_header("Cache-Control", "no-cache, no-transform")
                self.send_header("Connection", "close")
                self.send_header("X-Accel-Buffering", "no")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                streaming = True
                self.close_connection = True
                for chunk in stream_hermes_chat(session_id, message):
                    self.wfile.write(chunk)
                    self.wfile.flush()
                return
        except json.JSONDecodeError:
            if not streaming:
                _send_json(self, 400, {"error": "JSON inválido"})
            return
        except OSError as error:
            if streaming or error.errno in {
                errno.EPIPE,
                errno.ECONNRESET,
                32,
                54,
                104,
            }:
                return
            _send_json(self, 500, {"error": str(error)})
            return
        except Exception as error:
            if streaming:
                return
            _send_json(self, 500, {"error": str(error)})
            return
        _send_json(self, 404, {"error": "not found"})


def main() -> None:
    os.chdir(ROOT)
    _hydrate_env()
    host = os.environ.get("DEMO_UI_HOST", "127.0.0.1")
    port = int(os.environ.get("DEMO_UI_PORT", "8765"))
    server = ThreadingHTTPServer((host, port), DemoHandler)
    print(f"Sabor da Maria UI: http://{host}:{port}", flush=True)
    print(f"Hermes API: {hermes_url()}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nEncerrando.")
        server.server_close()


if __name__ == "__main__":
    main()
