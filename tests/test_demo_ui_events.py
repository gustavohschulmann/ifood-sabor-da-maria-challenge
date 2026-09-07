from scripts.demo_ui import (
    TurnAssembler,
    _assistant_text_from_completed,
    _messages_from_transcript,
    _parse_sse_chunks,
    _iter_sse,
    canonical_tool_name,
    iter_browser_events,
    map_hermes_event,
    merge_assistant_text,
)


class _LineResponse:
    def __init__(self, lines: list[bytes]) -> None:
        self._lines = iter(lines)

    def readline(self) -> bytes:
        return next(self._lines, b"")

    def read(self, _size: int) -> bytes:
        raise AssertionError("SSE must not wait for a fixed-size read")


def test_tool_search_is_status_not_tool() -> None:
    events = map_hermes_event(
        "tool.started",
        '{"tool_name":"tool_search","preview":"tool search"}',
    )
    assert events[0]["event"] == "status"
    assert events[0]["data"]["phase"] == "thinking"
    assert all(item["event"] != "tool" for item in events)
    assert canonical_tool_name("tool_search") == "tool_search"


def test_register_recipe_stays_visible() -> None:
    events = map_hermes_event(
        "tool.started",
        '{"tool_name":"register_recipe","args":{"recipe":{"name":"Parmegiana"}}}',
    )
    kinds = [item["event"] for item in events]
    assert "status" in kinds
    assert "tool" in kinds
    tool = next(item for item in events if item["event"] == "tool")
    assert tool["data"]["name"] == "register_recipe"
    assert "Parmegiana" in tool["data"]["summary"]


def test_assistant_completed_emits_replace_snapshot() -> None:
    text = _assistant_text_from_completed(
        {"content": "Olha o que o prato usa da sua despensa. Você tem frigideira?"}
    )
    assert "frigideira" in text
    events = map_hermes_event("assistant.completed", '{"content":"Oi"}')
    assert events[0]["event"] == "delta"
    assert events[0]["data"]["replace"] is True
    assert events[0]["data"]["text"] == "Oi"


def test_run_completed_emits_done() -> None:
    events = map_hermes_event("run.completed", '{"content":"Final"}')
    assert events[-1]["event"] == "done"
    assert events[-1]["data"]["complete"] is True
    snapshot = next(item for item in events if item["event"] == "delta")
    assert snapshot["data"]["replace"] is True
    assert snapshot["data"]["text"] == "Final"


def test_merge_keeps_full_text_over_broken_snapshot() -> None:
    full = (
        "Peito de frango: R$ 18,90/kg (precisa 70g = R$ 1,33)\n"
        "- Queijo mussarela ralado: R$ 52,90/kg"
    )
    fragment = "$ 1,33)\n- Queijo mussarela ralado: R$ 52,90/kg"
    assert merge_assistant_text(full, fragment, snapshot=True) == full


def test_merge_appends_incremental_deltas() -> None:
    assert (
        merge_assistant_text("Você tem tempo", " de fazer esse prato?")
        == "Você tem tempo de fazer esse prato?"
    )


def test_merge_does_not_drop_syllables_already_in_the_message() -> None:
    started = "Vou começar verificando o workspace e depois buscar uma Farinha de ros"
    assert merge_assistant_text(started, "ca") == started + "ca"
    greeting = "Oi, Dona Maria! Você"
    assert merge_assistant_text(greeting, " quer") == "Oi, Dona Maria! Você quer"
    assert merge_assistant_text(greeting, " ") == "Oi, Dona Maria! Você "
    saffron = "Vou começar, agora aça"
    assert merge_assistant_text(saffron, "frão") == saffron + "frão"
    assert merge_assistant_text(saffron, "çafrão") == saffron + "çafrão"


def test_assembler_ignores_fragment_snapshot() -> None:
    assembler = TurnAssembler()
    events = []
    events.extend(iter_browser_events("assistant.delta", '{"delta":"Custo do prato: R$ 1,33) e queijo"}', assembler))
    events.extend(
        iter_browser_events(
            "assistant.completed",
            '{"content":"$ 1,33) e queijo"}',
            assembler,
        )
    )
    texts = [item["data"]["text"] for item in events if item["event"] == "delta"]
    assert texts[-1].startswith("Custo do prato")
    assert not texts[-1].startswith("$ 1,33)")


def test_first_assistant_message_streams_before_completion() -> None:
    assembler = TurnAssembler()
    events: list[dict] = []
    events.extend(
        iter_browser_events(
            "assistant.delta",
            '{"delta":"Olá, "}',
            assembler,
        )
    )
    assert events == []

    events.extend(
        iter_browser_events(
            "assistant.delta",
            '{"delta":"Dona Maria! Encontrei a receita."}',
            assembler,
        )
    )

    assert events[-1]["event"] == "delta"
    assert events[-1]["data"]["segment"] == 0
    assert events[-1]["data"]["text"] == (
        "Olá, Dona Maria! Encontrei a receita."
    )


def test_internal_narration_is_not_streamed() -> None:
    assembler = TurnAssembler()
    events: list[dict] = []
    events.extend(
        iter_browser_events(
            "assistant.delta",
            '{"delta":"Deixa eu "}',
            assembler,
        )
    )
    events.extend(
        iter_browser_events(
            "assistant.delta",
            '{"delta":"carregar as ferramentas."}',
            assembler,
        )
    )
    events.extend(
        iter_browser_events(
            "assistant.completed",
            '{"content":"Deixa eu carregar as ferramentas."}',
            assembler,
        )
    )

    assert not any(item["event"] == "delta" for item in events)


def test_tool_start_forces_a_new_assistant_segment() -> None:
    assembler = TurnAssembler()
    events: list[dict] = []
    events.extend(
        iter_browser_events(
            "assistant.delta",
            '{"delta":"Esta receita parece uma boa candidata."}',
            assembler,
        )
    )
    events.extend(
        iter_browser_events(
            "assistant.completed",
            '{"content":"Esta receita parece uma boa candidata."}',
            assembler,
        )
    )
    events.extend(
        iter_browser_events(
            "tool.started",
            '{"tool_name":"web_search","args":{"query":"parmegiana"}}',
            assembler,
        )
    )
    events.extend(
        iter_browser_events(
            "assistant.delta",
            '{"delta":"Encontrei os ingredientes completos."}',
            assembler,
        )
    )
    deltas = [item for item in events if item["event"] == "delta"]
    first = [item["data"]["text"] for item in deltas if item["data"]["segment"] == 0]
    assert first[-1] == "Esta receita parece uma boa candidata."
    assert deltas[-1]["data"]["segment"] == 1
    assert deltas[-1]["data"]["text"] == "Encontrei os ingredientes completos."


def test_partial_token_waits_for_a_boundary_before_being_published() -> None:
    assembler = TurnAssembler()

    first = list(
        iter_browser_events(
            "assistant.delta",
            '{"delta":"A receita encontrada tem uma descri"}',
            assembler,
        )
    )
    assert first[-1]["data"]["text"] == "A receita encontrada tem uma"
    assert "descri" not in first[-1]["data"]["text"]

    second = list(
        iter_browser_events(
            "assistant.delta",
            '{"delta":"ção completa. "}',
            assembler,
        )
    )
    assert second[-1]["data"]["text"] == (
        "A receita encontrada tem uma descrição completa. "
    )


def test_partial_tool_narration_never_reaches_the_chat() -> None:
    assembler = TurnAssembler()
    events: list[dict] = []
    events.extend(
        iter_browser_events(
            "assistant.delta",
            '{"delta":"Ótimo! Agora vou cadast"}',
            assembler,
        )
    )
    events.extend(
        iter_browser_events(
            "tool.started",
            '{"tool_name":"register_recipe","args":{}}',
            assembler,
        )
    )

    assert not any(item["event"] == "delta" for item in events)


def test_keepalive_is_thinking_status() -> None:
    events = map_hermes_event("keepalive", "")
    assert events[0]["event"] == "status"
    assert events[0]["data"]["phase"] == "thinking"


def test_parse_keepalive_comment() -> None:
    events = list(_parse_sse_chunks(b": keepalive\n\n"))
    assert events == [("keepalive", "")]


def test_iter_sse_releases_each_small_frame_line_by_line() -> None:
    response = _LineResponse([
        b"event: status\n",
        b'data: {"phase":"thinking"}\n',
        b"\n",
        b"event: delta\n",
        b'data: {"text":"Oi"}\n',
        b"\n",
    ])

    assert list(_iter_sse(response)) == [
        ("status", '{"phase":"thinking"}'),
        ("delta", '{"text":"Oi"}'),
    ]


def test_transcript_picks_last_assistant() -> None:
    messages = _messages_from_transcript(
        {
            "messages": [
                {"role": "user", "content": "Sim"},
                {"role": "assistant", "content": "Você precisa de frigideira?"},
            ]
        }
    )
    assert "frigideira" in _assistant_text_from_completed({"messages": messages})
