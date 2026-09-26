import asyncio
import hashlib
import os
import re
import time
import uuid
from typing import Any

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from fact_extraction import extract_facts
from memory import build_memory

load_dotenv()

app = FastAPI(title="Personal Agent Router")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
TRAINING_COACH_URL = os.getenv("TRAINING_COACH_URL", "http://127.0.0.1:8000/chat")
HOME_ASSISTANT_URL = os.getenv("HOME_ASSISTANT_URL", "http://homeassistant.local:8123")
HOME_ASSISTANT_TOKEN = os.getenv("HOME_ASSISTANT_TOKEN")
HOME_ASSISTANT_LANGUAGE = os.getenv("HOME_ASSISTANT_LANGUAGE", "es")
MEMORY_HISTORY_LIMIT = int(os.getenv("MEMORY_HISTORY_LIMIT", "10"))

# Persistent memory (Supabase-backed if configured, no-op otherwise). Built
# once at import time so the DB connection/embedding model is reused across
# requests.
memory = build_memory()

SESSION_HEADER_NAMES = (
    "x-conversation-id",
    "x-session-id",
    "x-openwebui-conversation-id",
    "x-openwebui-session-id",
    "conversation-id",
    "session-id",
)

# Open WebUI sends auxiliary (non-chat) completions for titles/tags/follow-ups.
# These carry the full history as a prompt and must not reach any backend agent.
UTILITY_MARKERS = (
    "generate a concise",
    "generate 1-3 broad tags",
    "suggest 3-5 relevant follow-up",
    "generate a suitable emoji",
)


def is_utility_request(messages: list) -> bool:
    for message in messages:
        content = str(message.get("content", "")).lower()
        if any(marker in content for marker in UTILITY_MARKERS):
            return True
    return False


def derive_stable_conversation_id(messages: list) -> str:
    # Open WebUI resends the full growing history each turn, so the first
    # user message is a stable anchor for the whole conversation thread.
    for message in messages:
        if message.get("role") == "user":
            digest = hashlib.sha256(str(message.get("content", "")).encode("utf-8")).hexdigest()[:16]
            return f"personal-agent-{digest}"
    return f"personal-agent-{uuid.uuid4()}"


def resolve_conversation_id(request: dict, http_request: Request, messages: list) -> str:
    request_conversation_id = request.get("conversation_id")
    if request_conversation_id:
        return request_conversation_id

    for header_name in SESSION_HEADER_NAMES:
        header_value = http_request.headers.get(header_name)
        if header_value:
            return header_value

    return derive_stable_conversation_id(messages)


def detect_route(message: str) -> str:
    text = message.lower()

    training_keywords = [
        "entrenamiento",
        "entreno",
        "carrera",
        "rutina",
        "sesion",
        "sesión",
        "plan",
        "ritmo",
        "volumen",
        "recuperacion",
        "recuperación",
        "riesgo",
        "lesion",
        "lesión",
        "bici",
        "ciclismo",
        "natacion",
        "swim",
        "run",
        "bike",
        "training",
        "workout",
        "athlete",
        "actividad",
        "actividades",
        "activity",
        "distancia",
        "kilometro",
        "kilómetro",
        "kilometros",
        "kilómetros",
        "km",
        "pace",
        "fatiga",
        "descanso",
        "rpe",
        "dolor",
        "molestia",
        "sincroniza",
        "sincronizar",
        "desnivel",
        "elevacion",
        "elevación",
        "trail",
        "maraton",
        "maratón",
        "ctl",
        "atl",
        "tsb",
        "intervalos",
        "series",
        "fondo",
        "zona",
        "zonas",
        "umbral",
        "frecuencia cardiaca",
        "frecuencia cardíaca",
        "pulsaciones",
    ]

    home_keywords = [
        "casa",
        "luz",
        "temperatura",
        "calefaccion",
        "calefacción",
        "ventilador",
        "persiana",
        "cortina",
        "encender",
        "apagar",
        "home assistant",
        "lights",
        "temperature",
        "living room",
        "garage",
    ]

    # Match at word start (digits allowed, e.g. "10km") so "plan" doesn't fire on "explanation".
    def matches(keywords: list[str]) -> bool:
        return any(re.search(rf"(?<![^\W\d_]){re.escape(keyword)}", text) for keyword in keywords)

    if matches(training_keywords):
        return "training"
    if matches(home_keywords):
        return "home"
    return "general"


ROUTE_LABELS = ("training", "home", "general")


async def classify_route_via_llm(latest_message: str, recent_context: str) -> str:
    """Fallback classifier for messages the keyword router can't confidently place.

    Only called when no keyword matched, so it doesn't add cost/latency to the
    common case. Any failure (no API key, timeout, bad response) falls back to
    "general", matching prior (pre-fallback) behavior.
    """
    if not OPENAI_API_KEY:
        return "general"

    prompt = (
        "Classify the LATEST user message into exactly one category: training, home, or general.\n"
        "- training: endurance/sports training, activities, workouts, recovery, injury risk, "
        "fitness metrics (CTL/ATL/TSB, pace, distance, heart rate, etc.)\n"
        "- home: smart home control (lights, temperature, blinds, Home Assistant)\n"
        "- general: anything else\n"
        "Use the previous conversation only to resolve short follow-ups that have no topic "
        "of their own (e.g. 'and yesterday?'). If the latest message is about a different "
        "topic than before, classify it by its own content.\n"
        "Respond with only the single category word, nothing else.\n\n"
        f"Previous conversation:\n{recent_context[-1500:] or '(none)'}\n\n"
        f"Latest message: {latest_message[-1000:]}"
    )

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                f"{OPENAI_BASE_URL}/chat/completions",
                headers={
                    "Authorization": f"Bearer {OPENAI_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": OPENAI_MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0,
                    "max_tokens": 5,
                },
            )
        if response.status_code == 200:
            choices = response.json().get("choices", [])
            if choices:
                label = choices[0]["message"]["content"].strip().lower()
                if label in ROUTE_LABELS:
                    return label
    except (httpx.HTTPError, KeyError, ValueError):
        pass

    return "general"


def format_facts(facts: list[dict]) -> str:
    return "\n".join(f"- {fact['content']}" for fact in facts)


def build_general_messages(user_message: str, history: list[dict], facts: list[dict]) -> list[dict]:
    """Assemble the OpenAI messages array: recalled facts + prior history + current turn."""
    openai_messages = []
    if facts:
        openai_messages.append(
            {
                "role": "system",
                "content": f"Known facts about the user, recalled from memory:\n{format_facts(facts)}",
            }
        )

    openai_messages.extend(history)
    openai_messages.append({"role": "user", "content": user_message})
    return openai_messages


async def call_openai(messages: list[dict]) -> str:
    if not OPENAI_API_KEY:
        raise HTTPException(status_code=500, detail="OPENAI_API_KEY is not configured")

    async with httpx.AsyncClient(timeout=120.0) as client:
        response = await client.post(
            f"{OPENAI_BASE_URL}/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": OPENAI_MODEL,
                "messages": messages,
            },
        )

    if response.status_code != 200:
        raise HTTPException(
            status_code=response.status_code,
            detail=f"OpenAI request failed: {response.text}",
        )

    data = response.json()
    choices = data.get("choices", [])
    if not choices:
        raise HTTPException(status_code=500, detail="OpenAI returned no response choices")
    return choices[0]["message"]["content"]


async def call_training_coach(
    message: str, conversation_id: str, history: list[dict], facts: list[dict]
) -> str:
    # Send the shared cross-route history + recalled facts so the coach sees
    # the same memory as the general route instead of only its own turns.
    payload: dict[str, Any] = {
        "message": message,
        "conversation_id": conversation_id,
        "history": history,
    }
    if facts:
        payload["context"] = f"Known facts about the user, recalled from memory:\n{format_facts(facts)}"

    async with httpx.AsyncClient(timeout=120.0) as client:
        response = await client.post(TRAINING_COACH_URL, json=payload)

    if response.status_code != 200:
        raise HTTPException(
            status_code=response.status_code,
            detail=f"Training Coach request failed: {response.text}",
        )

    result = response.json()
    return result.get("reply", "No reply returned by Training Coach.")


# Our conversation id -> HA Assist conversation id, so HA can resolve follow-ups
# ("apágala") within the same chat.
_ha_conversation_ids: dict[str, str] = {}


async def call_home_assistant(message: str, conversation_id: str) -> str:
    if not HOME_ASSISTANT_URL or not HOME_ASSISTANT_TOKEN:
        return "Home Assistant is not configured yet."

    payload = {"text": message, "language": HOME_ASSISTANT_LANGUAGE}
    if conversation_id in _ha_conversation_ids:
        payload["conversation_id"] = _ha_conversation_ids[conversation_id]

    # Delegate entity/intent resolution to HA's own Assist conversation agent
    # instead of reimplementing device/area matching here.
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{HOME_ASSISTANT_URL}/api/conversation/process",
                headers={
                    "Authorization": f"Bearer {HOME_ASSISTANT_TOKEN}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
    except httpx.HTTPError as exc:
        print(f"personal-agent: Home Assistant request failed: {exc}")
        return "No pude conectar con Home Assistant en este momento."

    if response.status_code != 200:
        print(f"personal-agent: Home Assistant returned {response.status_code}: {response.text}")
        return "Home Assistant devolvió un error al procesar la solicitud."

    data = response.json()
    if data.get("conversation_id"):
        _ha_conversation_ids[conversation_id] = data["conversation_id"]
    speech = (
        data.get("response", {})
        .get("speech", {})
        .get("plain", {})
        .get("speech")
    )
    return speech or "Home Assistant no devolvió una respuesta."


async def remember_facts(conversation_id: str, user_message: str, assistant_reply: str) -> None:
    """Extract and persist durable facts from a conversation turn (any route).

    Runs as a background task so it never adds latency to the user-facing
    response; failures are logged and otherwise swallowed.
    """
    try:
        facts = await extract_facts(user_message, assistant_reply)
        for fact in facts:
            memory.save_fact(fact, conversation_id=conversation_id)
    except Exception as exc:
        print(f"personal-agent: fact extraction/storage failed: {exc}")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "personal-agent"}


@app.get("/v1/models")
async def models() -> dict[str, Any]:
    return {
        "object": "list",
        "data": [
            {
                "id": "personal-agent",
                "object": "model",
                "created": int(time.time()),
                "owned_by": "eloy",
            }
        ],
    }


@app.post("/v1/chat/completions")
async def chat_completions(request: dict, http_request: Request) -> dict[str, Any]:
    messages = request.get("messages", [])
    if not messages:
        return JSONResponse(
            status_code=400,
            content={"error": "No messages provided"},
        )

    if is_utility_request(messages):
        return {
            "id": f"chatcmpl-{uuid.uuid4()}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": "personal-agent",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": ""},
                    "finish_reason": "stop",
                }
            ],
        }

    user_message = None
    for message in reversed(messages):
        if message.get("role") == "user":
            user_message = message.get("content")
            break

    if not user_message:
        return JSONResponse(
            status_code=400,
            content={"error": "No user message found"},
        )

    user_message = str(user_message)
    conversation_id = resolve_conversation_id(request, http_request, messages)

    # Prior turns (all routes) from persistent memory; fall back to the
    # history Open WebUI resends if persistence is disabled.
    history = memory.get_recent_messages(conversation_id, limit=MEMORY_HISTORY_LIMIT)
    if not history:
        history = [
            {"role": m["role"], "content": str(m.get("content", ""))}
            for m in messages[:-1]
            if m.get("role") in ("user", "assistant")
        ][-MEMORY_HISTORY_LIMIT:]

    # Route on the latest message only, so the conversation can switch between
    # agents; the LLM fallback uses recent context to keep topic-less follow-ups
    # (e.g. "y el desnivel?") on the previous agent.
    route = detect_route(user_message)
    if route == "general":
        recent_context = "\n".join(f"{turn['role']}: {turn['content']}" for turn in history[-6:])
        route = await classify_route_via_llm(user_message, recent_context)

    facts = memory.search_facts(user_message)
    memory.log_message(conversation_id, "user", user_message, route=route)

    if route == "training":
        response_text = await call_training_coach(user_message, conversation_id, history, facts)
    elif route == "home":
        response_text = await call_home_assistant(user_message, conversation_id)
    else:
        try:
            openai_messages = build_general_messages(user_message, history, facts)
            response_text = await call_openai(openai_messages)
        except HTTPException as exc:
            print(f"personal-agent: general route failed: {exc.detail}")
            response_text = (
                "Lo siento, no pude procesar tu mensaje en este momento. "
                "Intenta de nuevo en un momento."
            )

    memory.log_message(conversation_id, "assistant", response_text, route=route)

    # Fire-and-forget: never block the response on fact extraction.
    asyncio.create_task(remember_facts(conversation_id, user_message, response_text))

    return {
        "id": f"chatcmpl-{uuid.uuid4()}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": "personal-agent",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": response_text},
                "finish_reason": "stop",
            }
        ],
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="0.0.0.0", port=int(os.getenv("PERSONAL_AGENT_PORT", "8101")))
