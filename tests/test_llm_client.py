import asyncio
from types import SimpleNamespace as NS

import pytest

from llm_stub import StubServer
from naollm.config import LLMConfig
from naollm.llm.client import LLMUnavailable, OpenAIChatClient, ToolCallAccumulator


def tc(index=None, id=None, name=None, arguments=None):
    return NS(index=index, id=id, function=NS(name=name, arguments=arguments))


def test_accumulator_openai_fragments():
    acc = ToolCallAccumulator()
    acc.add([tc(0, "call_a", "dance", "")])
    acc.add([tc(0, None, None, '{"na')])
    acc.add([tc(0, None, None, 'me": "macarena"}')])
    acc.add([tc(1, "call_b", "set_eyes", '{"color": "pink"}')])
    calls = acc.result()
    assert [(c.id, c.name, c.arguments) for c in calls] == [
        ("call_a", "dance", '{"name": "macarena"}'),
        ("call_b", "set_eyes", '{"color": "pink"}'),
    ]


def test_accumulator_ollama_complete_calls_same_index():
    acc = ToolCallAccumulator()
    acc.add([tc(0, "call_1", "look", '{"direction": "left"}')])
    acc.add([tc(0, "call_2", "play_gesture", '{"name": "wave"}')])
    assert [c.name for c in acc.result()] == ["look", "play_gesture"]


def test_accumulator_without_index_or_id():
    acc = ToolCallAccumulator()
    acc.add([tc(None, None, "get_status", "{}")])
    calls = acc.result()
    assert calls[0].name == "get_status" and calls[0].id == "call_0"


def collect(client, tools=None):
    async def run():
        events = []
        async for ev in client.stream([{"role": "user", "content": "hi"}], tools):
            events.append(ev)
        return events
    return asyncio.run(run())


OPENAI_DELTAS = [
    {"role": "assistant", "content": "Klar! "},
    {"content": "Los geht's."},
    {"tool_calls": [{"index": 0, "id": "call_x", "type": "function",
                     "function": {"name": "dance", "arguments": ""}}]},
    {"tool_calls": [{"index": 0, "function": {"arguments": '{"name": "macarena"}'}}]},
]

OLLAMA_DELTAS = [
    {"role": "assistant", "content": "",
     "tool_calls": [{"id": "call_1", "index": 0, "type": "function",
                     "function": {"name": "look", "arguments": '{"direction":"left"}'}},
                    {"id": "call_2", "index": 1, "type": "function",
                     "function": {"name": "play_gesture", "arguments": '{"name":"wave"}'}}]},
]


@pytest.mark.parametrize("deltas,expected_text,expected_calls", [
    (OPENAI_DELTAS, "Klar! Los geht's.", [("dance", '{"name": "macarena"}')]),
    (OLLAMA_DELTAS, "", [("look", '{"direction":"left"}'), ("play_gesture", '{"name":"wave"}')]),
])
def test_stream_against_stub_endpoint(deltas, expected_text, expected_calls):
    with StubServer(deltas) as server:
        client = OpenAIChatClient(LLMConfig(base_url=server.base_url, timeout_s=5))
        tools = [{"type": "function", "function": {"name": "dance", "parameters": {"type": "object"}}}]
        events = collect(client, tools)
        body = server.bodies[0]
    assert "".join(e.text for e in events if e.kind == "text") == expected_text
    done = events[-1]
    assert done.kind == "done"
    assert [(c.name, c.arguments) for c in done.calls] == expected_calls
    assert body["stream"] is True and body["tool_choice"] == "auto" and body["model"] == "gemma4:e4b"


def test_no_tools_means_no_tool_choice():
    with StubServer([{"content": "Hallo."}]) as server:
        client = OpenAIChatClient(LLMConfig(base_url=server.base_url, timeout_s=5))
        collect(client, None)
        assert "tools" not in server.bodies[0] and "tool_choice" not in server.bodies[0]


def test_timeout_raises_unavailable():
    with StubServer([{"content": "zu spät"}], delay_s=1.0) as server:
        client = OpenAIChatClient(LLMConfig(base_url=server.base_url, timeout_s=0.3))
        with pytest.raises(LLMUnavailable):
            collect(client)


def test_connection_refused_raises_unavailable():
    client = OpenAIChatClient(LLMConfig(base_url="http://127.0.0.1:9/v1", timeout_s=2))
    with pytest.raises(LLMUnavailable):
        collect(client)
