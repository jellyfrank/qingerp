from odoo import api, models

from ..tools.chinese_amount import cncurrency


class ChineseAmountConverter(models.AbstractModel):
    _name = 'ir.qweb.field.qing_chinese_amount'
    _inherit = 'ir.qweb.field'
    _description = 'Qing Chinese Amount'

    @api.model
    def value_to_html(self, value, options):
        return cncurrency(
            value,
            capital=options.get('capital', True),
            prefix=options.get('prefix', False),
            classical=options.get('classical'),
        )
