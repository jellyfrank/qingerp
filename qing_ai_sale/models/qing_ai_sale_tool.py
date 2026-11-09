"""Legacy sales-tool settings retained for existing databases."""

from odoo import fields, models


SALE_TOOLS = [
    ('find_customers', 'Find Customers'),
    ('find_products', 'Find Products'),
    ('find_quotes', 'Find Quotations'),
    ('find_activities', 'Find Follow-up Activities'),
    ('create_followup', 'Create Follow-up Activity'),
    ('create_quote_draft', 'Create Quotation Draft'),
]


class QingAiSaleTool(models.Model):
    _name = 'qing.ai.sale.tool'
    _description = 'Qing AI Sales Tool Permission'

    _key_unique = models.Constraint('UNIQUE (key)', 'Each AI sales tool must be unique.')

    active = fields.Boolean(default=True)
    allowed_group_ids = fields.Many2many(
        'res.groups', string='Allowed Groups',
        help='Legacy setting. Configure allowed groups on the Skill instead.',
    )
    key = fields.Selection(SALE_TOOLS, required=True, readonly=True)
    name = fields.Char(required=True, translate=True)
