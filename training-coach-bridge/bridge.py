from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import httpx
import sys
import time
import uuid
from pathlib import Path

_PROJECT_ROOT = str(Path(__file__).resolve().parents[1])
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from personal_agent_common.conversation import is_utility_request, resolve_conversation_id

app = FastAPI()

TRAINING_COACH_URL = "http://127.0.0.1:8000/chat"
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

    conversation_id = resolve_conversation_id(request, http_request.headers, messages, "openwebui")

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
