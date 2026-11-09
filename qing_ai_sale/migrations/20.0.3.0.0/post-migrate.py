"""Move untouched built-in skills to the runtime-discovered ORM query tools."""

from odoo import Command, SUPERUSER_ID, api


SKILLS = {
    'skill_customers': {
        'legacy': {'find_customers', 'create_customer', 'update_customer'},
        'tools': [
            'tool_partner_search', 'tool_partner_read', 'tool_partner_count',
            'tool_partner_group', 'agent_tool_find_customers',
            'agent_tool_create_customer', 'agent_tool_update_customer',
        ],
    },
    'skill_sales_orders': {
        'legacy': {'find_customers', 'find_products', 'find_quotes', 'create_quote_draft'},
        'tools': [
            'tool_partner_search', 'tool_product_search', 'tool_product_read',
            'tool_product_count', 'tool_product_group', 'tool_sale_order_search',
            'tool_sale_order_read', 'tool_sale_order_count', 'tool_sale_order_group',
            'agent_tool_find_customers', 'agent_tool_find_products', 'agent_tool_find_quotes',
            'agent_tool_create_quote_draft',
        ],
    },
    'skill_sales': {
        'legacy': {
            'create_customer', 'update_customer', 'find_customers', 'find_products',
            'find_quotes', 'find_activities', 'create_followup', 'create_quote_draft',
        },
        'tools': [
            'tool_partner_search', 'tool_partner_read', 'tool_partner_count',
            'tool_partner_group', 'tool_product_search', 'tool_product_read',
            'tool_product_count', 'tool_product_group', 'tool_sale_order_search',
            'tool_sale_order_read', 'tool_sale_order_count', 'tool_sale_order_group',
            'agent_tool_find_customers', 'agent_tool_find_products', 'agent_tool_find_quotes',
            'agent_tool_create_customer', 'agent_tool_update_customer',
            'agent_tool_find_activities', 'agent_tool_create_followup',
            'agent_tool_create_quote_draft',
        ],
    },
}


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {'lang': None})
    for skill_xmlid, spec in SKILLS.items():
        skill = env.ref('qing_ai_sale.' + skill_xmlid, raise_if_not_found=False)
        if not skill or set(skill.tool_ids.mapped('key')) != spec['legacy']:
            continue
        tools = [
            env.ref('qing_ai_sale.' + xmlid, raise_if_not_found=False)
            for xmlid in spec['tools']
        ]
        tools = [tool for tool in tools if tool]
        if len(tools) == len(spec['tools']):
            skill.write({'tool_ids': [Command.set([tool.id for tool in tools])]})
