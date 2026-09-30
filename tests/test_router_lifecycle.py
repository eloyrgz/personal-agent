import asyncio
import unittest
from unittest.mock import Mock, patch

import app


class RouterLifecycleTests(unittest.TestCase):
    def test_lifespan_closes_persistent_memory(self):
        memory = Mock()

        async def exercise_lifespan():
            with patch.object(app, "memory", memory):
                async with app.lifespan(app.app):
                    pass

        asyncio.run(exercise_lifespan())

        memory.close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()