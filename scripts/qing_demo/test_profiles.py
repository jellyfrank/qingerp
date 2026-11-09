"""Execute with Odoo shell; discovery remains database-independent."""
import unittest
from unittest.mock import patch
import json
import tempfile
from pathlib import Path
from odoo.addons.qing_demo_server.services import profile_registry as profiles


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.profile = dict(code="test_cn", name="Test", version="1.0", language="zh_CN",
                            country_code="CN", currency_code="CNY", native_demo=True,
                            root_module="test_cn", sequence=20)

    def test_schema_rejects_bad_types_and_module_injection(self):
        for changes in ({"native_demo": "true"}, {"root_module": "sale"}, {"sequence": "20"},
                        {"code": "../cn"}, {"currency_code": ""}, {"country_code": "china"}, {"name_zh": []}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                profiles.validate_profile(dict(self.profile, **changes), "test_cn")

    def test_discovery_uses_addon_precedence_and_reserved_codes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for module, profile in (
                ("test_cn", self.profile),
                ("reserved", dict(self.profile, code="empty", root_module="reserved")),
                ("bad_json", None),
            ):
                (root / module).mkdir()
                (root / module / "qing_demo_profile.json").write_text(json.dumps(profile))
            with patch.object(profiles, "get_modules", return_value=["test_cn", "reserved", "bad_json"]), \
                 patch.object(profiles, "get_module_path", side_effect=lambda module: str(root / module)), \
                 patch.object(profiles, "get_manifest", return_value={"installable": True}):
                actual = profiles.discover_profiles()
                self.assertEqual([p["code"] for p in actual], ["empty", "odoo_us", "test_cn"])
                self.assertFalse(actual[0]["native_demo"])
                with self.assertRaises(ValueError):
                    profiles.get_profile("unknown")


result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(ProfileTests))
assert result.wasSuccessful()
