import json

import httpx
import pytest

from injecteval.agent import run_agent
from injecteval.defenses import get_defense
from injecteval.environment import Environment
from injecteval.models import (
    ModelError,
    ModelResponse,
    OpenAICompatibleModel,
    _parse_arguments,
    build_model,
)


class ScriptedModel:
    """Returns pre-baked responses and remembers what it was shown."""

    name = "scripted"

    def __init__(self, responses):
        self.responses = list(responses)
        self.seen = []

    def complete(self, messages, tools):
        self.seen.append([dict(message) for message in messages])
        return self.responses.pop(0)


def call(name, **arguments):
    return ModelResponse(tool_calls=[{"id": "c1", "name": name, "arguments": arguments}])


def test_agent_executes_tools_and_returns_the_final_answer():
    env = Environment(files={"a.txt": "42"})
    model = ScriptedModel([call("read_file", path="a.txt"), ModelResponse(content="The answer is 42")])

    transcript = run_agent(model, env, "What is in a.txt?", get_defense("none"))

    assert transcript.final_answer == "The answer is 42"
    assert transcript.steps == 2 and transcript.stopped_reason == "completed"
    assert [m["role"] for m in transcript.messages] == ["system", "user", "assistant", "tool", "assistant"]
    assert transcript.messages[3] == {"role": "tool", "tool_call_id": "c1", "content": "42"}


def test_defense_rewrites_what_the_model_sees_but_not_what_is_recorded():
    env = Environment(files={"a.txt": "42"})
    model = ScriptedModel([call("read_file", path="a.txt"), ModelResponse(content="done")])

    run_agent(model, env, "task", get_defense("spotlight"))

    tool_message = model.seen[1][-1]
    assert tool_message["content"] == "<<UNTRUSTED_DATA>>\n42\n<<END_UNTRUSTED_DATA>>"
    assert env.calls[0].result == "42"


def test_agent_stops_at_max_steps():
    env = Environment()
    model = ScriptedModel([call("list_files") for _ in range(5)])

    transcript = run_agent(model, env, "loop forever", get_defense("none"), max_steps=3)

    assert transcript.stopped_reason == "max_steps"
    assert transcript.steps == 3 and len(env.calls) == 3
    assert transcript.final_answer == ""


def test_mock_models_differ_only_in_following_the_injection(by_id):
    scenario = by_id["action-delete-file"]
    outcomes = {}
    for mode in ("mock:robust", "mock:vulnerable"):
        env = Environment.from_dict(scenario.build_environment())
        run_agent(build_model(mode, scenario), env, scenario.user_task, get_defense("none"))
        outcomes[mode] = env.deleted_files

    assert outcomes == {"mock:robust": [], "mock:vulnerable": ["backups/db-2026-01.sql"]}


def test_vulnerable_mock_stays_clean_when_the_payload_never_reaches_it(by_id):
    scenario = by_id["action-delete-file"]
    env = Environment.from_dict(scenario.build_environment(attacked=False))

    run_agent(build_model("mock:vulnerable", scenario), env, scenario.user_task, get_defense("none"))

    assert env.deleted_files == []


def openai_model(handler):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return OpenAICompatibleModel("test-model", "http://server.example/v1/", client=client)


def test_openai_client_sends_the_request_and_parses_tool_calls():
    captured = {}

    def handler(request):
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {
            "content": None,
            "tool_calls": [{"id": "abc", "function": {"name": "read_file", "arguments": '{"path": "a.txt"}'}}],
        }}]})

    response = openai_model(handler).complete([{"role": "user", "content": "hi"}], [{"type": "function"}])

    assert captured["url"] == "http://server.example/v1/chat/completions"
    assert captured["body"]["model"] == "test-model" and captured["body"]["temperature"] == 0.0
    assert response.content == ""
    assert response.tool_calls == [{"id": "abc", "name": "read_file", "arguments": {"path": "a.txt"}}]


def test_openai_client_parses_a_plain_answer():
    model = openai_model(lambda request: httpx.Response(200, json={"choices": [{"message": {"content": "hello"}}]}))
    assert model.complete([], []) == ModelResponse(content="hello", tool_calls=[])


@pytest.mark.parametrize("response", [
    httpx.Response(500, text="boom"),
    httpx.Response(200, json={"unexpected": "shape"}),
    httpx.Response(200, text="not json"),
])
def test_openai_client_turns_backend_failures_into_model_errors(response):
    with pytest.raises(ModelError):
        openai_model(lambda request: response).complete([], [])


@pytest.mark.parametrize("raw, expected", [
    ('{"a": 1}', {"a": 1}),
    ({"a": 1}, {"a": 1}),
    ("not json", {}),
    ("[1, 2]", {}),
    ("", {}),
    (None, {}),
])
def test_tool_arguments_are_parsed_defensively(raw, expected):
    assert _parse_arguments(raw) == expected


def test_build_model_resolves_providers(by_id, monkeypatch):
    scenario = by_id["benign-email-reply"]
    monkeypatch.delenv("INJECTEVAL_BASE_URL", raising=False)

    assert build_model("ollama:llama3.2", scenario).base_url == "http://localhost:11434/v1"
    assert build_model("custom:m", scenario, base_url="http://h/v1").base_url == "http://h/v1"
    with pytest.raises(ValueError, match="provider:model"):
        build_model("llama3.2", scenario)
    with pytest.raises(ValueError, match="unknown provider"):
        build_model("nowhere:m", scenario)
