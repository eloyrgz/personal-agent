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

## Open WebUI setup

Use an external connection with:

- URL: http://host.containers.internal:8101/v1
- model: personal-agent

## Current behavior

The router is intentionally simple at this stage:

- training-related messages are sent to the Training Coach API
- home-related messages are recognized and prepared for future Home Assistant integration
- all other messages are answered by OpenAI

## Notes

This project does not alter the existing Training Coach repo or Telegram configuration. It instead provides a new orchestration layer that can be extended with Supabase memory and HA actions.
