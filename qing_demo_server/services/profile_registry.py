"""Discover trusted, locally deployed profiles without requiring a database."""
import json
import logging
import re
from pathlib import Path

from odoo.modules.module import get_module_path, get_modules, get_manifest

_logger = logging.getLogger(__name__)
BUILTINS = (
    dict(code="empty", name="空数据库 / Empty Database", version="1.0",
         native_demo=False, root_module=None, sequence=0),
    dict(code="odoo_us", name="Odoo 标准演示 / English · US · USD", version="1.0",
         language="en_US", country_code="US", currency_code="USD",
         native_demo=True, root_module=None, sequence=10),
)


def validate_profile(data, module):
    if not isinstance(data, dict):
        raise ValueError("Profile must be a JSON object")
    for key in ("code", "name", "version", "language", "country_code", "currency_code"):
        if not isinstance(data.get(key), str) or not data[key].strip():
            raise ValueError("Missing profile field: %s" % key)
    if "name_zh" in data and (not isinstance(data["name_zh"], str) or not data["name_zh"].strip()):
        raise ValueError("Invalid translated profile name")
    if not re.fullmatch(r"[a-z][a-z0-9_]*", data["code"]):
        raise ValueError("Invalid profile code")
    if data.get("root_module") != module or data.get("native_demo") is not True:
        raise ValueError("Profile must belong to its root module and enable native demo")
    if type(data.get("sequence", 20)) is not int:
        raise ValueError("Profile sequence must be an integer")
    if not re.fullmatch(r"[A-Z]{2}", data["country_code"]):
        raise ValueError("Invalid country code")
    if not re.fullmatch(r"[A-Z]{3}", data["currency_code"]):
        raise ValueError("Invalid currency code")
    return dict(data, sequence=data.get("sequence", 20))


def discover_profiles():
    profiles = {item["code"]: dict(item) for item in BUILTINS}
    for module in sorted(get_modules()):
        path = get_module_path(module)
        if not path:
            continue
        descriptor = Path(path) / "qing_demo_profile.json"
        if not descriptor.is_file():
            continue
        try:
            if not get_manifest(module).get("installable", True):
                continue
            data = validate_profile(json.loads(descriptor.read_text(encoding="utf-8")), module)
            if data["code"] in profiles:
                raise ValueError("Duplicate or reserved profile code: %s" % data["code"])
            profiles[data["code"]] = data
        except (OSError, ValueError, TypeError):
            _logger.exception("Ignoring invalid demo profile in %s", module)
    return sorted(profiles.values(), key=lambda item: (item["sequence"], item["code"]))


def get_profile(code):
    for profile in discover_profiles():
        if profile["code"] == code:
            return profile
    raise ValueError("未知或不可用的演示环境 / Unknown demo profile")
