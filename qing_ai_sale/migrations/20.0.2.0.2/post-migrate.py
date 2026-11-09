"""Offer customer creation to untouched default sales skills on upgrade."""

from odoo import Command, SUPERUSER_ID, api


LEGACY_KEYS = {
    'find_customers', 'find_products', 'find_quotes', 'find_activities',
    'create_followup', 'create_quote_draft',
}


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    tool = env.ref('qing_ai_sale.agent_tool_create_customer', raise_if_not_found=False)
    if not tool:
        tool = env['qing.ai.tool'].create({'name': 'Create Customer', 'key': 'create_customer'})
        env['ir.model.data'].create({
            'module': 'qing_ai_sale', 'name': 'agent_tool_create_customer',
            'model': 'qing.ai.tool', 'res_id': tool.id, 'noupdate': True,
        })
    skill = env.ref('qing_ai_sale.skill_sales', raise_if_not_found=False)
    if skill and set(skill.tool_ids.mapped('key')) == LEGACY_KEYS:
        skill.write({'tool_ids': [Command.link(tool.id)]})
