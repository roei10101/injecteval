"""The agent loop: give the model a task and tools, execute what it asks for, record everything."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from .defenses import Defense
from .environment import TOOL_SCHEMAS, Environment
from .models import Model


@dataclass
class Transcript:
    messages: list[dict[str, Any]] = field(default_factory=list)
    final_answer: str = ""
    steps: int = 0
    stopped_reason: str = "completed"  # completed | max_steps


def run_agent(model: Model, env: Environment, user_task: str, defense: Defense, max_steps: int = 12) -> Transcript:
    transcript = Transcript(messages=[
        {"role": "system", "content": defense.system_prompt()},
        {"role": "user", "content": user_task},
    ])
    for _ in range(max_steps):
        response = model.complete(transcript.messages, TOOL_SCHEMAS)
        transcript.steps += 1

        if not response.tool_calls:
            transcript.messages.append({"role": "assistant", "content": response.content})
            transcript.final_answer = response.content
            return transcript

        transcript.messages.append({
            "role": "assistant",
            "content": response.content,
            "tool_calls": [
                {"id": call["id"], "type": "function",
                 "function": {"name": call["name"], "arguments": json.dumps(call["arguments"], ensure_ascii=False)}}
                for call in response.tool_calls
            ],
        })
        for call in response.tool_calls:
            executed = env.execute(call["name"], call["arguments"])
            visible = defense.transform_tool_output(call["name"], executed.result, user_task)
            transcript.messages.append({"role": "tool", "tool_call_id": call["id"], "content": visible})

    transcript.stopped_reason = "max_steps"
    return transcript
