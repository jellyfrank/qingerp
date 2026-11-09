from odoo import api, SUPERUSER_ID
from odoo.addons.qing_sms_aliyun.hooks import migrate_legacy


def migrate(cr, version):
    migrate_legacy(api.Environment(cr, SUPERUSER_ID, {}))
