import pathlib
import re

from odoo.tests import TransactionCase, tagged

# Odoo 20 renders button icons as Material Symbols ligatures; a Font Awesome
# name such as "fa-download" shows a stray "-" next to the glyph.
FA_ICON = re.compile(r'icon="fa[- ]')


@tagged("post_install", "-at_install")
class TestViewIcons(TransactionCase):

    def test_button_icons_use_material_symbols(self):
        module = pathlib.Path(__file__).resolve().parent.parent
        offenders = [
            str(path.relative_to(module))
            for path in module.rglob("*.xml")
            if FA_ICON.search(path.read_text(encoding="utf-8"))
        ]
        self.assertFalse(offenders, "Font Awesome button icons: %s" % offenders)
