from odoo import SUPERUSER_ID, api

from odoo.addons.qing_fapiao.hooks import import_legacy_columns


def migrate(cr, version):
    """Keep request links of tasks imported from mommy_fapiao by qing_fapiao."""
    env = api.Environment(cr, SUPERUSER_ID, {})
    import_legacy_columns(env, "qing_fapiao_order", ["qing_request_id"])
