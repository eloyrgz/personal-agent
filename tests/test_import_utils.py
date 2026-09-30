import sys
import unittest
from pathlib import Path


TRAINING_COACH_DIR = Path(__file__).resolve().parents[1] / "training-coach-agent"
sys.path.insert(0, str(TRAINING_COACH_DIR))

from import_utils import ensure_submodule_importable


class ImportUtilsTests(unittest.TestCase):
    def test_submodule_path_is_added_once(self):
        expected = str(TRAINING_COACH_DIR / "plan_generator")
        while expected in sys.path:
            sys.path.remove(expected)

        ensure_submodule_importable("plan_generator")
        ensure_submodule_importable("plan_generator")

        self.assertEqual(sys.path.count(expected), 1)
        self.assertEqual(sys.path[0], expected)


if __name__ == "__main__":
    unittest.main()