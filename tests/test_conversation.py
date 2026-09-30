import unittest

from personal_agent_common.conversation import (
    derive_stable_conversation_id,
    is_utility_request,
    resolve_conversation_id,
)


class ConversationHelpersTests(unittest.TestCase):
    def test_stable_id_uses_first_user_message_and_prefix(self):
        messages = [
            {"role": "system", "content": "context"},
            {"role": "user", "content": "What did I train yesterday?"},
            {"role": "assistant", "content": "..."},
        ]

        first = derive_stable_conversation_id(messages, "personal-agent")
        second = derive_stable_conversation_id(messages, "personal-agent")

        self.assertEqual(first, second)
        self.assertRegex(first, r"^personal-agent-[0-9a-f]{16}$")

    def test_request_id_precedes_header_and_derived_id(self):
        messages = [{"role": "user", "content": "hello"}]

        self.assertEqual(
            resolve_conversation_id(
                {"conversation_id": "request-id"},
                {"x-session-id": "header-id"},
                messages,
                "openwebui",
            ),
            "request-id",
        )
        self.assertEqual(
            resolve_conversation_id({}, {"x-session-id": "header-id"}, messages, "openwebui"),
            "header-id",
        )
        self.assertRegex(
            resolve_conversation_id({}, {}, messages, "openwebui"),
            r"^openwebui-[0-9a-f]{16}$",
        )

    def test_utility_requests_are_filtered(self):
        self.assertTrue(is_utility_request([{"role": "user", "content": "Generate a concise title"}]))
        self.assertFalse(is_utility_request([{"role": "user", "content": "What should I train today?"}]))


if __name__ == "__main__":
    unittest.main()