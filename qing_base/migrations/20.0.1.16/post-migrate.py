from odoo import SUPERUSER_ID, api
from odoo.addons.qing_base.hooks import disable_publisher_warranty_notification


def migrate(cr, version):
    disable_publisher_warranty_notification(api.Environment(cr, SUPERUSER_ID, {}))
