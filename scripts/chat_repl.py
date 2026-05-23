"""Text-only REPL for the LLM agent. No voice — just stdin/stdout.

Drives Groq's chat-completions API directly so we can exercise tool calling
without spinning up the full LiveKit voice pipeline. Tool calls and their
responses print inline so each turn is auditable.

Usage:
    uv run python scripts/chat_repl.py
    # or feed scripted prompts:
    echo "I'd like to book a cardiologist next Tuesday" | uv run python scripts/chat_repl.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timezone

import httpx
import structlog
from openai import AsyncOpenAI

# Make `from config import ...` and `from agent...` work when run as a script.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.llm_agent import LLM_MODEL, SYSTEM_PROMPT  # noqa: E402
from agent.tools import PLAIN_TOOLS, set_patient_context  # noqa: E402
from config import get_settings  # noqa: E402

DEFAULT_PATIENT_PHONE = os.getenv("DEMO_PATIENT_PHONE", "+14155551234")
MAX_TOOL_ROUNDS = 8


TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "list_available_slots",
            "description": (
                "List up to 5 available appointment slots. All filters optional. "
                "Returns a human-readable summary; each line has slot_id, time, "
                "doctor name, and specialty."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "specialty": {
                        "type": "string",
                        "description": "One of: General Medicine, Cardiology, Pediatrics",
                    },
                    "doctor_name": {
                        "type": "string",
                        "description": "Full or partial doctor name; case-insensitive substring",
                    },
                    "date": {
                        "type": "string",
                        "description": "Single-day filter in YYYY-MM-DD format",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "book_appointment",
            "description": (
                "Book a slot for the caller. Always confirm the date/time/doctor "
                "with the caller before calling this."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "slot_id": {
                        "type": "string",
                        "description": "Exact slot_id from list_available_slots — never guess",
                    },
                    "patient_phone": {
                        "type": "string",
                        "description": "Caller's phone, E.164 (e.g. +14155551234)",
                    },
                },
                "required": ["slot_id", "patient_phone"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "reschedule_appointment",
            "description": "Move an existing appointment to a new slot.",
            "parameters": {
                "type": "object",
                "properties": {
                    "appointment_id": {"type": "string"},
                    "new_slot_id": {"type": "string"},
                },
                "required": ["appointment_id", "new_slot_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cancel_appointment",
            "description": (
                "Cancel an existing appointment. Always confirm with the caller first."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "appointment_id": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["appointment_id", "reason"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_patient_appointments",
            "description": "List the caller's appointments (any status).",
            "parameters": {
                "type": "object",
                "properties": {
                    "patient_phone": {"type": "string"},
                },
                "required": ["patient_phone"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_alternatives",
            "description": "Return up to 3 nearby slots when the requested one is taken.",
            "parameters": {
                "type": "object",
                "properties": {
                    "slot_id": {"type": "string"},
                },
                "required": ["slot_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "recall_memory",
            "description": (
                "Look up prior durable notes about the current caller. "
                "Patient is implicit. Returns up to 4 most relevant prior notes."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "What to look up"},
                },
                "required": ["query"],
            },
        },
    },
]


def _configure_logging() -> None:
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="%H:%M:%S"),
            structlog.processors.add_log_level,
            structlog.dev.ConsoleRenderer(colors=False),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(20),  # INFO
        # Send structured logs to stderr so the transcript on stdout stays clean.
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
    )


def _system_prompt(patient_phone: str) -> str:
    today = datetime.now(timezone.utc).strftime("%A, %B %d, %Y")
    return (
        SYSTEM_PROMPT
        + "\n\nCaller context:\n"
        + f"- today: {today} (UTC)\n"
        + f"- patient_phone: {patient_phone}  (pass this when a tool needs patient_phone)\n"
    )


async def _run_tool_calls(client_msg, messages: list[dict]) -> None:
    """Execute tool calls from an assistant message and append results to history."""
    for tc in client_msg.tool_calls:
        name = tc.function.name
        try:
            args = json.loads(tc.function.arguments or "{}")
        except json.JSONDecodeError:
            args = {}
        print(f"  ↳ tool {name}({json.dumps(args)})")
        fn = PLAIN_TOOLS.get(name)
        if fn is None:
            result = f"unknown tool: {name}"
        else:
            result = await fn(**args)
        # Print first line of the tool response inline for readability
        first_line = str(result).splitlines()[0] if result else ""
        print(f"  ↳ → {first_line}")
        if str(result).count("\n") > 0:
            for line in str(result).splitlines()[1:]:
                print(f"        {line}")
        messages.append(
            {
                "role": "tool",
                "tool_call_id": tc.id,
                "content": str(result),
            }
        )


async def chat_loop(patient_phone: str) -> None:
    settings = get_settings()
    if not settings.groq_api_key:
        print("ERROR: GROQ_API_KEY is not set in .env", file=sys.stderr)
        sys.exit(2)

    # Look up or create the patient so recall_memory has an id to scope by.
    async with httpx.AsyncClient(base_url=settings.backend_url, timeout=10) as c:
        r = await c.get("/patients", params={"phone": patient_phone})
        if r.status_code == 200 and r.json():
            patient = r.json()[0]
        else:
            r = await c.post(
                "/patients",
                json={"name": "Caller", "phone": patient_phone, "preferred_language": "en"},
            )
            patient = r.json() if r.status_code == 201 else None

    set_patient_context(
        {
            "id": patient.get("id") if patient else None,
            "phone": patient_phone,
            "name": patient.get("name") if patient else None,
            "preferred_language": patient.get("preferred_language") if patient else "en",
        }
    )

    # Groq exposes an OpenAI-compatible chat-completions API.
    groq = AsyncOpenAI(
        api_key=settings.groq_api_key,
        base_url="https://api.groq.com/openai/v1",
    )

    messages: list[dict] = [{"role": "system", "content": _system_prompt(patient_phone)}]

    print("=== 2careAi chat REPL ===")
    print(f"patient_phone: {patient_phone}")
    print(f"model: {LLM_MODEL}")
    print("Type a message; Ctrl+D / EOF to quit.\n")

    while True:
        try:
            user_input = input("you> ").strip()
        except EOFError:
            print()
            break
        if not user_input:
            continue

        messages.append({"role": "user", "content": user_input})

        for round_idx in range(MAX_TOOL_ROUNDS):
            resp = await groq.chat.completions.create(
                model=LLM_MODEL,
                messages=messages,
                tools=TOOL_SCHEMAS,
                tool_choice="auto",
                temperature=0.2,
            )
            msg = resp.choices[0].message

            if msg.tool_calls:
                messages.append(
                    {
                        "role": "assistant",
                        "content": msg.content,
                        "tool_calls": [
                            {
                                "id": tc.id,
                                "type": "function",
                                "function": {
                                    "name": tc.function.name,
                                    "arguments": tc.function.arguments,
                                },
                            }
                            for tc in msg.tool_calls
                        ],
                    }
                )
                await _run_tool_calls(msg, messages)
                continue  # let the LLM respond to tool output

            content = msg.content or ""
            print(f"\nagent> {content}\n")
            messages.append({"role": "assistant", "content": content})
            break
        else:
            print("\nagent> (gave up after too many tool rounds)\n")


def main() -> None:
    _configure_logging()
    phone = os.getenv("DEMO_PATIENT_PHONE", DEFAULT_PATIENT_PHONE)
    asyncio.run(chat_loop(phone))


if __name__ == "__main__":
    main()
