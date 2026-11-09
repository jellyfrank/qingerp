from odoo.addons.qing_base.hooks import prepare_view_visibility


def migrate(cr, version):
    prepare_view_visibility(cr)
