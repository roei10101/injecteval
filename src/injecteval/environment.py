"""A simulated workspace (mailbox, files, web) exposed to the agent as tools.

Nothing here touches the real world: emails are never sent, URLs are never fetched.
Every call is recorded so graders can inspect exactly what the agent tried to do.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolCall:
    name: str
    arguments: dict[str, Any]
    result: str = ""
    error: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "arguments": self.arguments, "result": self.result, "error": self.error}


def _tool(name: str, description: str, properties: dict[str, str], required: list[str] | None = None) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": {key: {"type": "string", "description": text} for key, text in properties.items()},
                "required": list(properties) if required is None else required,
            },
        },
    }


TOOL_SCHEMAS: list[dict] = [
    _tool("list_emails", "List the emails in the user's inbox (id, sender, subject).", {}),
    _tool("read_email", "Read the full body of one email.", {"id": "Email id"}),
    _tool("send_email", "Send an email on behalf of the user.",
          {"to": "Recipient address", "subject": "Subject line", "body": "Email body"}),
    _tool("list_files", "List the files in the user's workspace.", {}),
    _tool("read_file", "Read a file from the user's workspace.", {"path": "File path"}),
    _tool("write_file", "Create or overwrite a file in the user's workspace.",
          {"path": "File path", "content": "New file content"}),
    _tool("delete_file", "Permanently delete a file from the user's workspace.", {"path": "File path"}),
    _tool("fetch_url", "Fetch a web page and return its text.", {"url": "Address of the page"}),
    _tool("http_post", "Send an HTTP POST request to a URL.", {"url": "Target URL", "body": "Request body"}),
]

TOOL_NAMES = [schema["function"]["name"] for schema in TOOL_SCHEMAS]

# Tools through which data can leave the user's workspace.
OUTBOUND_TOOLS = ("send_email", "http_post")


@dataclass
class Environment:
    emails: list[dict[str, str]] = field(default_factory=list)
    files: dict[str, str] = field(default_factory=dict)
    web: dict[str, str] = field(default_factory=dict)
    calls: list[ToolCall] = field(default_factory=list)
    sent_emails: list[dict[str, str]] = field(default_factory=list)
    http_posts: list[dict[str, str]] = field(default_factory=list)
    deleted_files: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Environment:
        return cls(
            emails=[{key: str(value) for key, value in email.items()} for email in data.get("emails", [])],
            files={str(path): str(content) for path, content in data.get("files", {}).items()},
            web={str(url): str(content) for url, content in data.get("web", {}).items()},
        )

    def execute(self, name: str, arguments: dict[str, Any] | None) -> ToolCall:
        """Run one tool call. Errors are returned to the agent as text, never raised."""
        arguments = arguments if isinstance(arguments, dict) else {}
        call = ToolCall(name=name, arguments=arguments)
        handler = getattr(self, f"_tool_{name}", None) if name in TOOL_NAMES else None
        try:
            if handler is None:
                raise ToolError(f"unknown tool '{name}'")
            call.result = handler(**{key: str(value) for key, value in arguments.items()})
        except ToolError as error:
            call.result, call.error = f"Error: {error}", True
        except TypeError:
            call.result, call.error = f"Error: invalid arguments for {name}: {json.dumps(arguments)}", True
        self.calls.append(call)
        return call

    # --- tools ---------------------------------------------------------------------------

    def _tool_list_emails(self) -> str:
        if not self.emails:
            return "The inbox is empty."
        return "\n".join(f"[{e['id']}] from {e['from']} - {e['subject']}" for e in self.emails)

    def _tool_read_email(self, id: str) -> str:  # noqa: A002 - the tool argument is called "id"
        for email in self.emails:
            if email["id"] == id.strip():
                return f"From: {email['from']}\nSubject: {email['subject']}\n\n{email['body']}"
        raise ToolError(f"no email with id '{id}'")

    def _tool_send_email(self, to: str, subject: str, body: str) -> str:
        self.sent_emails.append({"to": to, "subject": subject, "body": body})
        return f"Email sent to {to}."

    def _tool_list_files(self) -> str:
        return "\n".join(sorted(self.files)) if self.files else "The workspace is empty."

    def _tool_read_file(self, path: str) -> str:
        if path not in self.files:
            raise ToolError(f"file not found: {path}")
        return self.files[path]

    def _tool_write_file(self, path: str, content: str) -> str:
        self.files[path] = content
        return f"Wrote {len(content)} characters to {path}."

    def _tool_delete_file(self, path: str) -> str:
        if path not in self.files:
            raise ToolError(f"file not found: {path}")
        del self.files[path]
        self.deleted_files.append(path)
        return f"Deleted {path}."

    def _tool_fetch_url(self, url: str) -> str:
        if url not in self.web:
            raise ToolError(f"could not fetch {url} (404)")
        return self.web[url]

    def _tool_http_post(self, url: str, body: str) -> str:
        self.http_posts.append({"url": url, "body": body})
        return "200 OK"


class ToolError(Exception):
    """A tool failed in a way the agent should be told about."""
