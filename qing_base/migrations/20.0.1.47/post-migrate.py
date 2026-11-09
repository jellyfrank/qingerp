from odoo import SUPERUSER_ID, api

from odoo.addons.qing_base.hooks import import_legacy_mommy_base_settings


def migrate(cr, version):
    import_legacy_mommy_base_settings(api.Environment(cr, SUPERUSER_ID, {}))
