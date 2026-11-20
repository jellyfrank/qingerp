from odoo import SUPERUSER_ID, api

from odoo.addons.qing_base.hooks import initialize_assistant_brand


def migrate(cr, version):
    initialize_assistant_brand(api.Environment(cr, SUPERUSER_ID, {}))
