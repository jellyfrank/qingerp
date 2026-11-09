from odoo import fields, models


class ProductCategory(models.Model):
    _inherit = 'product.category'

    qing_fapiao_tax_classification_id = fields.Many2one(
        'qing.fapiao.tax.classification', string='Tax Classification', ondelete='restrict',
        domain="[('invoicing_allowed', '=', True)]",
        help='Default tax classification for products in this category and its child categories.')

    def _qing_get_fapiao_tax_classification(self):
        self.ensure_one()
        category = self
        while category:
            if category.qing_fapiao_tax_classification_id:
                return category.qing_fapiao_tax_classification_id
            category = category.parent_id
        return self.env['qing.fapiao.tax.classification']
