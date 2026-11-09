from odoo import api, SUPERUSER_ID
from odoo.addons.qing_base.hooks import initialize_brand_title


def migrate(cr, version):
    initialize_brand_title(api.Environment(cr, SUPERUSER_ID, {}))
