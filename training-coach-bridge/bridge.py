from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import hashlib
import httpx
import time
import uuid

app = FastAPI()

TRAINING_COACH_URL = "http://127.0.0.1:8000/chat"
SESSION_HEADER_NAMES = (
    "x-conversation-id",
    "x-session-id",
    "x-openwebui-conversation-id",
    "x-openwebui-session-id",
    "conversation-id",
    "session-id",
)

# Open WebUI sends auxiliary (non-chat) completions for titles/tags/follow-ups.
# These carry the full history as a prompt and must not reach the coach.
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
            return f"openwebui-{digest}"
    return f"openwebui-{uuid.uuid4()}"


def resolve_conversation_id(request: dict, http_request: Request, messages: list):
    # Preserve a real ID if Open WebUI or a custom proxy supplies one.
    request_conversation_id = request.get("conversation_id")
    if request_conversation_id:
        return request_conversation_id

    for header_name in SESSION_HEADER_NAMES:
        header_value = http_request.headers.get(header_name)
        if header_value:
            return header_value

    # No explicit session key, so derive a stable one from the conversation
    # content itself instead of generating a new ID on every turn.
    return derive_stable_conversation_id(messages)


@app.get("/v1/models")
async def models():
    return {
        "object": "list",
        "data": [
            {
                "id": "training-coach",
                "object": "model",
                "created": int(time.time()),
                "owned_by": "eloy"
            }
        ]
    }


@app.post("/v1/chat/completions")
async def chat_completions(request: dict, http_request: Request):
    messages = request.get("messages", [])
    print("HEADERS:", dict(http_request.headers))
    if not messages:
        return JSONResponse(
            status_code=400,
            content={"error": "No messages provided"}
        )

    if is_utility_request(messages):
        return {
            "id": f"chatcmpl-{uuid.uuid4()}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": "training-coach",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": ""},
                    "finish_reason": "stop"
                }
            ]
        }

    # Último mensaje enviado por el usuario
    user_message = None
    for message in reversed(messages):
        if message.get("role") == "user":
            user_message = message.get("content")
            break

    if not user_message:
        return JSONResponse(
            status_code=400,
            content={"error": "No user message found"}
        )

    conversation_id = resolve_conversation_id(request, http_request, messages)

    payload = {
        "message": user_message,
        "conversation_id": conversation_id
    }

    async with httpx.AsyncClient(timeout=120.0) as client:
        response = await client.post(
            TRAINING_COACH_URL,
            json=payload
        )

    if response.status_code != 200:
        return JSONResponse(
            status_code=response.status_code,
            content={
                "error": "Training Coach returned an error",
                "details": response.text
            }
        )

    result = response.json()

    return {
        "id": f"chatcmpl-{uuid.uuid4()}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": "training-coach",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": result["reply"]
                },
                "finish_reason": "stop"
            }
        ]
    }
