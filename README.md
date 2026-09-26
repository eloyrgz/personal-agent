# Personal Agent

This is the independent router layer for the Personal Agent concept described in the project notes.

It is intentionally kept separate from the existing Training Coach repository and acts as a single entrypoint for:

- general conversation via OpenAI
- training questions via the Training Coach API
- home automation requests via Home Assistant
- future memory and context persistence

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

For the local systemd-managed stack, register the Training Coach API unit once
(the router, bridge, and Telegram bot units are already installed):

```bash
ln -s "$PWD/training-coach-agent/training-coach-api.service" "$HOME/.config/systemd/user/training-coach-api.service"
systemctl --user daemon-reload
systemctl --user enable training-coach-api.service
```

Then run `./stack.sh start`, `./stack.sh restart`, `./stack.sh stop`, or
`./stack.sh status` from this directory. The script manages the Training Coach
API, Personal Agent router, Training Coach bridge, and Telegram bot; it checks
API readiness before starting the dependent services. Open WebUI is a separate
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

This project does not alter the existing Training Coach repo or Telegram configuration. It instead provides a new orchestration layer with its own Supabase-backed memory.
