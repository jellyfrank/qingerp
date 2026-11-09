from odoo.addons.qing_base.hooks import restore_view_visibility


def migrate(cr, version):
    restore_view_visibility(cr)
