from odoo import api, SUPERUSER_ID
from odoo.addons.qing_sms_stock.hooks import post_init_hook


def migrate(cr, version):
    post_init_hook(api.Environment(cr, SUPERUSER_ID, {}))
