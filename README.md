# Personal Agent

This is the independent router layer for the Personal Agent concept described in the project notes.

It is intentionally kept separate from the existing Training Coach repository and acts as a single entrypoint for:

- general conversation via OpenAI
- training questions via the Training Coach API
- home automation requests via Home Assistant
- persisted conversation history and long-term facts

## Local setup

```bash
cd /home/eloy/personal-agent
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

## Run

```bash
source .venv/bin/activate
uvicorn app:app --host 0.0.0.0 --port 8101
```

The refactoring status, pending work, and deferred improvements are tracked in
[`docs/refactoring-review.md`](docs/refactoring-review.md).
The endpoint contracts are documented in
[`docs/API_CONTRACT.md`](docs/API_CONTRACT.md).

For the local systemd-managed stack, register the two units tracked in this
repository once:

```bash
ln -s "$PWD/training-coach-agent/training-coach-api.service" "$HOME/.config/systemd/user/training-coach-api.service"
ln -s "$PWD/personal-agent-mcp-bridge.service" "$HOME/.config/systemd/user/personal-agent-mcp-bridge.service"
systemctl --user daemon-reload
systemctl --user enable training-coach-api.service
systemctl --user enable personal-agent-mcp-bridge.service
```

The remaining units used by `stack.sh` are installed separately on the host:
`personal-agent-router.service`, `training-coach-bridge.service`, and
`training-coach-telegram.service`. They are intentionally not duplicated in
this repository; verify that they exist with:

```bash
systemctl --user list-unit-files \
  personal-agent-router.service \
  training-coach-bridge.service \
  training-coach-telegram.service
```

Set `MCP_BRIDGE_TOKEN` in `.env` to a long random secret. MCP clients must send
it as `Authorization: Bearer <token>`; `/health` remains available without it.
Leaving the token empty disables `/mcp` authentication and exposes the public
endpoint, so only do this temporarily for testing.

Then run `./stack.sh start`, `./stack.sh restart`, `./stack.sh stop`, or
`./stack.sh status` from this directory. Run `./stack.sh check` to verify that
all five services are active, their HTTP endpoints answer, and the MCP bridge
initializes and advertises `ask_personal_agent`. It reports each failing service
or endpoint separately. This check does not send a prompt to the language model.
The script manages the Training Coach
API, Personal Agent router and MCP bridge, Training Coach bridge, and Telegram
bot; it checks API readiness before starting the dependent services. Open WebUI is a separate
container and is not restarted by this command. The local router service uses
`training-coach-agent/.venv`, which has the embedding dependencies installed.

## Open WebUI setup

Use an external connection with:

- URL: http://host.containers.internal:8101/v1
- model: personal-agent

## Remote access

Open WebUI is reachable over HTTPS from outside the LAN at
`https://webui.err-hass.duckdns.org/`, proxied by the existing Home Assistant
Nginx add-on on the Raspberry Pi via SNI on port 443 (same port already used
for Home Assistant remote access). See `/memories/repo/infra-raspberry-pi.md`
for the add-on/config details.

## Current behavior

The router is intentionally simple at this stage:

- training-related messages are sent to the Training Coach API
- home-related messages are forwarded to Home Assistant's Conversation API (`/api/conversation/process`), which resolves entities/areas and executes the intent
- all other messages are answered by OpenAI, with recent conversation history and semantically recalled long-term facts included as context

## Persistent memory (Supabase)

Personal Agent persists its own memory in the same Supabase Postgres project
already used by `training-coach-agent`, using the shared
[`pgvector-agent-memory`](https://github.com/eloyrgz/pgvector-agent-memory)
library (`memory.py`):

- **Conversation history** — every turn, across all routes, is logged to
  `conversation_messages`. The general (OpenAI) route uses this to give the
  model real multi-turn context instead of only the latest message.
- **Long-term semantic memory** — after each general-route reply, a small
  background LLM call (`fact_extraction.py`) pulls out durable facts worth
  remembering (preferences, personal details, ongoing plans), embeds them,
  and stores them in `agent_memories`. Future general-route messages recall
  relevant facts via cosine-similarity search and feed them back to OpenAI as
  context.

Run `sql/personal_agent_schema.sql` once against Supabase to create the
required tables/extension, and set `SUPABASE_DB_URI` and/or
`SUPABASE_POOLER_DB_URI` in `.env`. If neither is set, persistence is
disabled automatically (no-op fallback) and the router behaves as before.

## Notes

The router keeps its own memory tables in Supabase; training questions are
delegated to the separate Training Coach API.
