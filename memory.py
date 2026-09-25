"""Persistent memory for the Personal Agent router, backed by Supabase.

Two layers, mirroring the pattern already used by training-coach-agent's
SupabaseAgentMemory (via the shared `pgvector-agent-memory` library):

- Conversation history: every turn (all routes) is logged to
  `conversation_messages` so recent context can be reconstructed, e.g. for
  the general (OpenAI) route which otherwise has no memory at all.
- Long-term semantic memory: durable facts about the user are embedded and
  stored in `agent_memories`, recalled later via cosine-similarity search.

If no Supabase URI is configured, `build_memory()` returns a `NullMemory`
that implements the same interface as a set of no-ops, so the router keeps
working without persistence (mirrors the existing "Home Assistant not
configured" graceful degradation in app.py).
"""

import os

from pgvector_agent_memory import PgVectorMemory

SUPABASE_DB_URI_ENV_VARS = ("SUPABASE_POOLER_DB_URI", "SUPABASE_DB_URI")


class NullMemory:
    """No-op stand-in used when Supabase isn't configured."""

    def log_message(self, conversation_id: str, role: str, content: str, route: str = "general") -> None:
        return None

    def get_recent_messages(self, conversation_id: str, limit: int = 10) -> list[dict]:
        return []

    def save_fact(self, content: str, conversation_id: str | None = None) -> None:
        return None

    def search_facts(self, query: str, threshold: float = 0.4, limit: int = 5) -> list[dict]:
        return []


class PersonalAgentMemory(PgVectorMemory):
    def __init__(self):
        print("personal-agent: initializing memory connected to Supabase...")
        super().__init__(db_uri_env_vars=SUPABASE_DB_URI_ENV_VARS)

    def log_message(self, conversation_id: str, role: str, content: str, route: str = "general") -> None:
        """Append a single turn to the conversation history."""
        try:
            with self._cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO conversation_messages (conversation_id, role, route, content)
                    VALUES (%s, %s, %s, %s);
                    """,
                    (conversation_id, role, route, content),
                )
        except Exception as e:
            print(f"⚠️ personal-agent: failed to log message to Supabase: {e}")

    def get_recent_messages(self, conversation_id: str, limit: int = 10) -> list[dict]:
        """Return the last `limit` turns for a conversation, oldest first."""
        try:
            with self._cursor() as cur:
                cur.execute(
                    """
                    SELECT role, content FROM conversation_messages
                    WHERE conversation_id = %s
                    ORDER BY created_at DESC
                    LIMIT %s;
                    """,
                    (conversation_id, limit),
                )
                rows = [dict(row) for row in cur.fetchall()]
                return list(reversed(rows))
        except Exception as e:
            print(f"⚠️ personal-agent: failed to fetch recent messages from Supabase: {e}")
            return []

    def save_fact(self, content: str, conversation_id: str | None = None) -> None:
        """Embed and store a durable fact for later semantic recall."""
        try:
            vector = self.encode(content)
            with self._cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO agent_memories (conversation_id, content, embedding)
                    VALUES (%s, %s, %s);
                    """,
                    (conversation_id, content, str(vector)),
                )
        except Exception as e:
            print(f"⚠️ personal-agent: failed to save memory to Supabase: {e}")

    def search_facts(self, query: str, threshold: float = 0.4, limit: int = 5) -> list[dict]:
        """Semantic search over previously stored facts."""
        return self.semantic_search(
            table="agent_memories",
            embedding_column="embedding",
            text_query=query,
            select_columns="content, created_at::text as created_at",
            threshold=threshold,
            limit=limit,
        )


def build_memory():
    """Return a real Supabase-backed memory if configured, else a no-op."""
    if any(os.getenv(var) for var in SUPABASE_DB_URI_ENV_VARS):
        return PersonalAgentMemory()
    print("personal-agent: no Supabase DB URI configured, persistent memory disabled.")
    return NullMemory()
