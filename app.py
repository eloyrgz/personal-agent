import hashlib
import os
import time
import uuid
from typing import Any

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

load_dotenv()

app = FastAPI(title="Personal Agent Router")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
TRAINING_COACH_URL = os.getenv("TRAINING_COACH_URL", "http://127.0.0.1:8000/chat")
HOME_ASSISTANT_URL = os.getenv("HOME_ASSISTANT_URL", "http://homeassistant.local:8123")
HOME_ASSISTANT_TOKEN = os.getenv("HOME_ASSISTANT_TOKEN")
HOME_ASSISTANT_LANGUAGE = os.getenv("HOME_ASSISTANT_LANGUAGE", "es")

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

    if any(keyword in text for keyword in training_keywords):
        return "training"
    if any(keyword in text for keyword in home_keywords):
        return "home"
    return "general"


ROUTE_LABELS = ("training", "home", "general")


async def classify_route_via_llm(conversation_text: str) -> str:
    """Fallback classifier for messages the keyword router can't confidently place.

    Only called when no keyword matched, so it doesn't add cost/latency to the
    common case. Any failure (no API key, timeout, bad response) falls back to
    "general", matching prior (pre-fallback) behavior.
    """
    if not OPENAI_API_KEY:
        return "general"

    prompt = (
        "Classify the user's message into exactly one category: training, home, or general.\n"
        "- training: endurance/sports training, activities, workouts, recovery, injury risk, "
        "fitness metrics (CTL/ATL/TSB, pace, distance, heart rate, etc.)\n"
        "- home: smart home control (lights, temperature, blinds, Home Assistant)\n"
        "- general: anything else\n"
        "Respond with only the single category word, nothing else.\n\n"
        f"Message: {conversation_text[-1000:]}"
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


async def call_openai(message: str) -> str:
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
                "messages": [{"role": "user", "content": message}],
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


async def call_training_coach(message: str, conversation_id: str) -> str:
    async with httpx.AsyncClient(timeout=120.0) as client:
        response = await client.post(
            TRAINING_COACH_URL,
            json={"message": message, "conversation_id": conversation_id},
        )

    if response.status_code != 200:
        raise HTTPException(
            status_code=response.status_code,
            detail=f"Training Coach request failed: {response.text}",
        )

    result = response.json()
    return result.get("reply", "No reply returned by Training Coach.")


async def call_home_assistant(message: str) -> str:
    if not HOME_ASSISTANT_URL or not HOME_ASSISTANT_TOKEN:
        return "Home Assistant is not configured yet."

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
                json={"text": message, "language": HOME_ASSISTANT_LANGUAGE},
            )
    except httpx.HTTPError as exc:
        print(f"personal-agent: Home Assistant request failed: {exc}")
        return "No pude conectar con Home Assistant en este momento."

    if response.status_code != 200:
        print(f"personal-agent: Home Assistant returned {response.status_code}: {response.text}")
        return "Home Assistant devolvió un error al procesar la solicitud."

    data = response.json()
    speech = (
        data.get("response", {})
        .get("speech", {})
        .get("plain", {})
        .get("speech")
    )
    return speech or "Home Assistant no devolvió una respuesta."


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

    conversation_id = resolve_conversation_id(request, http_request, messages)
    # Open WebUI resends the full history each turn, so route on the whole
    # thread, not just the latest message, to keep topic-less follow-ups
    # (e.g. "y el desnivel?") on the same agent as the rest of the conversation.
    conversation_text = " ".join(str(message.get("content", "")) for message in messages)
    route = detect_route(conversation_text)
    if route == "general":
        # Keyword router found no match — ask the LLM for a second opinion
        # instead of assuming "general" outright.
        route = await classify_route_via_llm(conversation_text)

    if route == "training":
        response_text = await call_training_coach(str(user_message), conversation_id)
    elif route == "home":
        response_text = await call_home_assistant(str(user_message))
    else:
        try:
            response_text = await call_openai(str(user_message))
        except HTTPException as exc:
            print(f"personal-agent: general route failed: {exc.detail}")
            response_text = (
                "Lo siento, no pude procesar tu mensaje en este momento. "
                "Intenta de nuevo en un momento."
            )

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
