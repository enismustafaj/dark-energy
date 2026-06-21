"""LLM-backed chat agent for the dashboard sidebar."""

from __future__ import annotations

import json
import os
from typing import Literal

import httpx
from pydantic import BaseModel, Field

from ..config import get_settings


class ChatTurn(BaseModel):
    role: Literal["user", "agent"]
    text: str = Field(max_length=4000)


class ChatReply(BaseModel):
    message: str
    source: Literal["openai", "fallback"] = "fallback"


SYSTEM_PROMPT = """You are EnergyIntelligence's residential energy assistant.
Use only the provided household, recommendation, and action context.
If selected_recommendation is present, answer only about that recommendation.
Explain energy recommendations in practical customer language.
When the user asks you to take action on a recommendation, do not ask for confirmation.
State that you are proceeding with the available control action, briefly explain what it will do, and let the backend report the final execution result.
Never describe actions as mocked, simulated, fake, demo-only, or test actions.
Present completed actions as normal customer-facing control or service operations.
Do not tell the user to click another button.
Keep replies concise, usually 2-4 sentences.
"""


def fallback_reply(user_text: str, context: dict) -> ChatReply:
    actions = context.get("available_actions", [])
    if actions:
        labels = ", ".join(a["label"] for a in actions[:3])
        return ChatReply(
            message=(
                "I can help with this recommendation, but the OpenAI chat backend is not configured. "
                f"Available actions for this household include: {labels}."
            )
        )
    return ChatReply(
        message=(
            "I can help explain this household's energy recommendations, but the OpenAI chat backend is not configured."
        )
    )


async def complete_chat(user_text: str, history: list[ChatTurn], context: dict) -> ChatReply:
    if not os.environ.get("OPENAI_API_KEY"):
        return fallback_reply(user_text, context)

    settings = get_settings()
    input_items = [
        {
            "role": "system",
            "content": SYSTEM_PROMPT,
        },
        {
            "role": "user",
            "content": (
                "Context JSON:\n"
                f"{json.dumps(context, ensure_ascii=False)}\n\n"
                "Use this context to answer the chat. Do not expose raw JSON."
            ),
        },
    ]
    input_items.extend(
        {"role": "assistant" if turn.role == "agent" else "user", "content": turn.text}
        for turn in history[-10:]
    )
    input_items.append({"role": "user", "content": user_text})

    payload = {
        "model": settings.chat_model,
        "input": input_items,
        "temperature": 0.3,
        "max_output_tokens": 350,
    }
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                "https://api.openai.com/v1/responses",
                headers={
                    "Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            response.raise_for_status()
    except Exception:
        return fallback_reply(user_text, context)

    text = _extract_output_text(response.json())
    return ChatReply(message=text or fallback_reply(user_text, context).message, source="openai")


def _extract_output_text(data: dict) -> str:
    if isinstance(data.get("output_text"), str):
        return data["output_text"].strip()
    parts: list[str] = []
    for item in data.get("output", []):
        for content in item.get("content", []):
            if content.get("type") == "output_text" and content.get("text"):
                parts.append(content["text"])
    return "\n".join(parts).strip()
