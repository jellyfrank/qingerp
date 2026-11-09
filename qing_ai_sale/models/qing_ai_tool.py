"""Reviewed sales adapters that may back discovered model capabilities."""

from odoo import models
from odoo.fields import Domain


CUSTOMER_ORM_TOOLS = {
    'orm_res_partner_search', 'orm_res_partner_read',
    'orm_res_partner_count', 'orm_res_partner_group',
}


class QingAiTool(models.Model):
    _inherit = 'qing.ai.tool'

    def _orm_scope(self, Model, extra=None):
        domain = super()._orm_scope(Model, extra=extra)
        if self.key in CUSTOMER_ORM_TOOLS and Model._name == 'res.partner':
            role_field = 'qing_is_customer' if 'qing_is_customer' in Model._fields else 'customer_rank'
            domain &= Domain(role_field, '=', True) if role_field == 'qing_is_customer' else Domain(
                role_field, '>', 0,
            )
        return domain

    def _approved_adapter_targets(self):
        return {
            **super()._approved_adapter_targets(),
            'find_customers': ('res.partner', 'model_query'),
            'create_customer': ('res.partner', 'model_create'),
            'find_products': ('product.product', 'model_query'),
            'find_quotes': ('sale.order', 'model_query'),
            'create_quote_draft': ('sale.order', 'model_create'),
        }
