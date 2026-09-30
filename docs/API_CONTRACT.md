# API Contract

This document describes the local contracts used by Open WebUI, the Personal
Agent router, the Training Coach API, and the MCP bridge.

## Personal Agent OpenAI-Compatible API

Base URL: `http://127.0.0.1:8101/v1`

### `GET /models`

Returns the available model:

```json
{
  "object": "list",
  "data": [{
    "id": "personal-agent",
    "object": "model",
    "owned_by": "eloy"
  }]
}
```

### `POST /chat/completions`

Request:

```json
{
  "model": "personal-agent",
  "messages": [
    {"role": "user", "content": "What should I train today?"}
  ],
  "stream": false
}
```

The latest user message is routed to general conversation, Home Assistant, or
Training Coach. Open WebUI utility requests for titles, tags, follow-ups, and
emojis receive an empty successful completion.

The response uses the OpenAI chat-completion shape:

```json
{
  "id": "chatcmpl-...",
  "object": "chat.completion",
  "model": "personal-agent",
  "choices": [{
    "index": 0,
    "message": {"role": "assistant", "content": "..."},
    "finish_reason": "stop"
  }]
}
```

Conversation identity is resolved in this order:

1. JSON `conversation_id`
2. Supported conversation/session headers
3. A stable ID derived from the first user message

## Training Coach API

Base URL: `http://127.0.0.1:8000`

When `COACH_API_KEY` is configured, protected endpoints require:

```text
Authorization: Bearer <COACH_API_KEY>
```

### `GET /health`

Returns `{"status":"ok"}` and does not require authentication.

### `POST /chat`

Request:

```json
{
  "message": "How hard should I train today?",
  "conversation_id": "demo",
  "history": null,
  "context": null
}
```

`history`, when supplied, replaces the server-side in-memory history for the
request. The response is:

```json
{
  "reply": "...",
  "conversation_id": "demo"
}
```

### `POST /chat/reset?conversation_id=demo`

Clears the selected in-memory conversation and returns:

```json
{"status":"cleared","conversation_id":"demo"}
```

### `POST /sync`

Request:

```json
{"days": 7}
```

`days` must be between 1 and 3650. Successful responses contain `status`,
`days`, and a completion `message`.

## Training Coach OpenAI-Compatible Bridge

Base URL: `http://127.0.0.1:8100/v1`

The bridge exposes model ID `training-coach` and accepts the same
`/chat/completions` request shape. It forwards the latest user message to the
Training Coach API and derives an `openwebui-<hash>` conversation ID when the
caller does not provide one.

## MCP Bridge

Base URL: `http://127.0.0.1:8102`

- `GET /health` is public and returns the service status.
- `/mcp` exposes the `ask_personal_agent` tool.
- When `MCP_BRIDGE_TOKEN` is configured, `/mcp` requires
  `Authorization: Bearer <MCP_BRIDGE_TOKEN>`.

The bridge accepts a tool message and forwards it to the Personal Agent
OpenAI-compatible endpoint. The stack check verifies that the tool is
advertised and that the configured token works.

## Error Handling

- Malformed or missing request data returns HTTP 400 or 422.
- Missing or invalid configured bearer credentials return HTTP 401.
- Downstream agent failures preserve the downstream status where possible and
  otherwise return HTTP 500 or 502.
- The Personal Agent retries transient OpenAI transport and server failures up
  to three attempts; client errors are not retried.