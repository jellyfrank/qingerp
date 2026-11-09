from odoo import SUPERUSER_ID, api
from odoo.addons.qing_base.hooks import initialize_report_print_preview


def migrate(cr, version):
    # Set the product default only when no administrator choice exists yet.
    initialize_report_print_preview(api.Environment(cr, SUPERUSER_ID, {}))
