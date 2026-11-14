import pathlib
import re

from odoo.tests import TransactionCase, tagged

# Odoo 20 ships no Font Awesome: icons are Material Symbols ligatures
# (<i class="oi" data-icon="name"/>, <button icon="name"/>). Font Awesome
# classes render nothing and "fa-name" icon attributes show a stray "-".
FA_PATTERNS = (re.compile(r'\bfa fa-'), re.compile(r'icon(?:=|: )"fa[- ]'))
SUFFIXES = {".xml", ".js"}


@tagged("post_install", "-at_install")
class TestIconNames(TransactionCase):

    def test_qing_modules_do_not_use_font_awesome(self):
        addons = pathlib.Path(__file__).resolve().parents[2]
        offenders = []
        for module in sorted(addons.glob("qing_*")):
            for path in module.rglob("*"):
                if path.suffix not in SUFFIXES or "/static/lib/" in path.as_posix():
                    continue
                text = path.read_text(encoding="utf-8", errors="ignore")
                if any(pattern.search(text) for pattern in FA_PATTERNS):
                    offenders.append(str(path.relative_to(addons)))
        self.assertFalse(offenders, "Font Awesome icons found: %s" % offenders)
