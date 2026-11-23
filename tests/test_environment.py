from injecteval.environment import TOOL_NAMES, TOOL_SCHEMAS, Environment


def make_env():
    return Environment.from_dict({
        "emails": [{"id": 1, "from": "a@x.example", "subject": "Hi", "body": "Hello there"}],
        "files": {"notes.txt": "secret"},
        "web": {"https://x.example": "<p>page</p>"},
    })


def test_every_schema_has_a_handler():
    env = Environment()
    for name in TOOL_NAMES:
        assert callable(getattr(env, f"_tool_{name}"))
    assert len(TOOL_SCHEMAS) == len(TOOL_NAMES)


def test_reading_tools_return_content():
    env = make_env()

    assert "[1] from a@x.example - Hi" in env.execute("list_emails", {}).result
    assert "Hello there" in env.execute("read_email", {"id": "1"}).result
    assert env.execute("read_file", {"path": "notes.txt"}).result == "secret"
    assert env.execute("fetch_url", {"url": "https://x.example"}).result == "<p>page</p>"


def test_outbound_tools_only_record():
    env = make_env()

    env.execute("send_email", {"to": "b@y.example", "subject": "s", "body": "b"})
    env.execute("http_post", {"url": "https://y.example", "body": "data"})

    assert env.sent_emails == [{"to": "b@y.example", "subject": "s", "body": "b"}]
    assert env.http_posts == [{"url": "https://y.example", "body": "data"}]


def test_write_and_delete_change_state():
    env = make_env()

    env.execute("write_file", {"path": "new.txt", "content": "x"})
    env.execute("delete_file", {"path": "notes.txt"})

    assert env.files == {"new.txt": "x"}
    assert env.deleted_files == ["notes.txt"]


def test_errors_are_returned_to_the_agent_not_raised():
    env = make_env()

    missing = env.execute("read_file", {"path": "nope.txt"})
    unknown = env.execute("launch_missiles", {})
    bad_args = env.execute("read_file", {"wrong": "x"})
    not_a_dict = env.execute("list_files", None)

    assert missing.error and "not found" in missing.result
    assert unknown.error and "unknown tool" in unknown.result
    assert bad_args.error and "invalid arguments" in bad_args.result
    assert not not_a_dict.error


def test_private_attributes_cannot_be_called_as_tools():
    env = make_env()

    call = env.execute("execute", {"name": "delete_file"})

    assert call.error


def test_every_call_is_recorded_in_order():
    env = make_env()
    env.execute("list_files", {})
    env.execute("read_file", {"path": "nope"})

    assert [call.name for call in env.calls] == ["list_files", "read_file"]
