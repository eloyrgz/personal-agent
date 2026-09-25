"""LLM-based durable fact extraction for the general (OpenAI) route.

Runs as a fire-and-forget background task after a general-route reply is
sent, so it never adds latency to the user-facing response. Uses a small,
cheap prompt asking the model to return only facts worth remembering long
term (preferences, personal details, ongoing plans), or nothing at all.
"""

import os

import httpx

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")

NO_FACTS_MARKER = "NONE"

EXTRACTION_PROMPT = (
    "You extract durable facts worth remembering long-term about the user "
    "from a single exchange with an assistant. Only include facts that are "
    "likely to stay true and useful in future, unrelated conversations: "
    "stable preferences, personal details, ongoing plans/goals, recurring "
    "constraints. Do NOT include small talk, one-off questions, or anything "
    "already obvious/generic.\n"
    "Respond with one fact per line, in the same language as the user "
    f'message. If there is nothing worth remembering, respond with exactly "{NO_FACTS_MARKER}".\n\n'
    "User: {user_message}\n"
    "Assistant: {assistant_reply}"
)


async def extract_facts(user_message: str, assistant_reply: str) -> list[str]:
    """Return a list of durable facts worth remembering, or an empty list."""
    if not OPENAI_API_KEY:
        return []

    prompt = EXTRACTION_PROMPT.format(user_message=user_message, assistant_reply=assistant_reply)

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                f"{OPENAI_BASE_URL}/chat/completions",
                headers={
                    "Authorization": f"******",
                    "Content-Type": "application/json",
                },
                json={
                    "model": OPENAI_MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0,
                    "max_tokens": 200,
                },
            )
        if response.status_code != 200:
            return []

        choices = response.json().get("choices", [])
        if not choices:
            return []

        text = choices[0]["message"]["content"].strip()
        if not text or text.upper() == NO_FACTS_MARKER:
            return []

        return [line.strip("-• ").strip() for line in text.splitlines() if line.strip()]
    except (httpx.HTTPError, KeyError, ValueError) as e:
        print(f"⚠️ personal-agent: fact extraction failed: {e}")
        return []
