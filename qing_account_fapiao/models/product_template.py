from odoo import fields, models


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    qing_fapiao_tax_classification_id = fields.Many2one(
        'qing.fapiao.tax.classification', string='Tax Classification', ondelete='restrict',
        domain="[('invoicing_allowed', '=', True)]",
        help='Tax classification for this product. Leave empty to inherit the product category setting.')

    def _qing_get_fapiao_tax_classification(self):
        self.ensure_one()
        return (self.qing_fapiao_tax_classification_id
                or self.categ_id._qing_get_fapiao_tax_classification())
