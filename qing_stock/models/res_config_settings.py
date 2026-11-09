from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    module_qing_stock_barcode = fields.Boolean(
        string='Enable QingERP Warehouse Scanning',
    )

    qing_prevent_negative_stock = fields.Boolean(
        string='Prevent Negative Stock',
        help='Prevent stock operations from making internal or transit locations negative. '
             'Users explicitly allowed to create negative stock remain exempt.',
        config_parameter='qing_stock.prevent_negative_stock',
        default=True,
    )
