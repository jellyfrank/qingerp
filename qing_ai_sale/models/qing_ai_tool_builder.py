"""Reuse reviewed customer and quotation adapters from the simple builder."""

from odoo import fields, models


class QingAiToolBuilder(models.TransientModel):
    _inherit = 'qing.ai.tool.builder'

    def _template_tools(self):
        tools = super()._template_tools()
        templates = {
            'customers': [
                'tool_partner_search', 'tool_partner_read', 'tool_partner_count',
                'tool_partner_group', 'agent_tool_find_customers',
                'agent_tool_create_customer', 'agent_tool_update_customer',
            ],
            'sales_orders': [
                'tool_partner_search', 'tool_product_search', 'tool_product_read',
                'tool_product_count', 'tool_product_group', 'tool_sale_order_search',
                'tool_sale_order_read', 'tool_sale_order_count', 'tool_sale_order_group',
                'agent_tool_find_customers', 'agent_tool_find_products', 'agent_tool_find_quotes',
                'agent_tool_create_quote_draft',
            ],
        }
        for xmlid in templates.get(self.template, []):
            tools |= self.env.ref('qing_ai_sale.' + xmlid)
        return tools

    template = fields.Selection(selection_add=[('customers', 'Customer Management'),
                                               ('sales_orders', 'Sales Orders')],
                                 ondelete={'customers': 'set default', 'sales_orders': 'set default'})
