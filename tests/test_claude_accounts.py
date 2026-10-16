"""Claude account number from a proxy port: the GUI passes ports as strings."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from claude_accounts import get_default_account_num


class TestDefaultAccountNum(unittest.TestCase):
    def test_string_ports_from_get_listen(self):
        self.assertEqual(get_default_account_num("1015"), 1)
        self.assertEqual(get_default_account_num("1016"), 2)
        self.assertEqual(get_default_account_num("1020"), 3)
        self.assertEqual(get_default_account_num("1081"), 1)   # clean fallback instead of absurd 67

    def test_int_ports_unchanged(self):
        self.assertEqual(get_default_account_num(1015), 1)
        self.assertEqual(get_default_account_num(1016), 2)
        self.assertEqual(get_default_account_num(1020), 3)
        self.assertEqual(get_default_account_num(1021), 4)
        self.assertEqual(get_default_account_num(1030), 5)
        self.assertEqual(get_default_account_num(1081), 1)
        self.assertEqual(get_default_account_num(1000), 1)

    def test_config_override_wins(self):
        self.assertEqual(get_default_account_num("1081", {"claude_account_num": 3}), 3)
        self.assertEqual(get_default_account_num("1081", {"claude_account_num": "4"}), 4)

    def test_bad_port_falls_back_to_first_account(self):
        self.assertEqual(get_default_account_num(""), 1)
        self.assertEqual(get_default_account_num(None), 1)
        self.assertEqual(get_default_account_num("abc"), 1)


if __name__ == "__main__":
    unittest.main()
