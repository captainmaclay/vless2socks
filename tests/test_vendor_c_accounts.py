"""Vendor C profile number and account resolution tests from proxy ports."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from vendor_c_accounts import (
    get_default_account_num,
    get_default_profile_num,
    get_vendor_c_account_for_port,
    get_vendor_c_config_candidates,
    parse_vendor_c_config,
)


class TestVendorCDefaultAccountNum(unittest.TestCase):
    def test_string_ports_from_get_listen(self):
        self.assertEqual(get_default_account_num("1015"), 1)
        self.assertEqual(get_default_account_num("1016"), 2)
        self.assertEqual(get_default_account_num("1020"), 3)
        self.assertEqual(get_default_account_num("1081"), 1)

    def test_profile_num_alias(self):
        self.assertEqual(get_default_profile_num("1015"), 1)
        self.assertEqual(get_default_profile_num("1016"), 2)
        self.assertEqual(get_default_profile_num(1020), 3)

    def test_int_ports_unchanged(self):
        self.assertEqual(get_default_account_num(1015), 1)
        self.assertEqual(get_default_account_num(1016), 2)
        self.assertEqual(get_default_account_num(1020), 3)
        self.assertEqual(get_default_account_num(1021), 4)
        self.assertEqual(get_default_account_num(1030), 5)
        self.assertEqual(get_default_account_num(1081), 1)
        self.assertEqual(get_default_account_num(1000), 1)

    def test_config_override_wins(self):
        self.assertEqual(get_default_account_num("1081", {"vendor_c_account_num": 3}), 3)
        self.assertEqual(get_default_account_num("1081", {"claude_account_num": "4"}), 4)

    def test_bad_port_falls_back_to_first_account(self):
        self.assertEqual(get_default_account_num(""), 1)
        self.assertEqual(get_default_account_num(None), 1)
        self.assertEqual(get_default_account_num("abc"), 1)

    def test_config_candidates(self):
        candidates_1 = get_vendor_c_config_candidates(1015)
        self.assertTrue(any(".claude.json" in str(c) for c in candidates_1))
        candidates_2 = get_vendor_c_config_candidates(1016)
        self.assertTrue(any(".claude-secondary" in str(c) for c in candidates_2))
        candidates_custom = get_vendor_c_config_candidates(1020, account_num=3)
        self.assertTrue(any(".claude-3" in str(c) for c in candidates_custom))


if __name__ == "__main__":
    unittest.main()
