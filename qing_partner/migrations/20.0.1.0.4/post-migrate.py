from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    old_root = env.ref('qing_partner.menu_partner_root', raise_if_not_found=False)
    if old_root and not old_root.parent_id:
        old_root.unlink()
