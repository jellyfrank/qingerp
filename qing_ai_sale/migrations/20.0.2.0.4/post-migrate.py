"""Add ready sales capabilities while preserving customized authorizations."""

from odoo import Command, SUPERUSER_ID, api


LEGACY_KEYS = {
    'create_customer', 'find_customers', 'find_products', 'find_quotes',
    'find_activities', 'create_followup', 'create_quote_draft',
}
LEGACY_INSTRUCTIONS = (
    'Use only the permitted sales tools. Preview changes and wait for human confirmation.'
)
SALES_INSTRUCTIONS = (
    'Use only the permitted sales tools. Prepare a preview when the request has enough '
    'details, then wait for human confirmation before applying changes. Customer and '
    'sales order searches may omit search terms to list and count accessible records. '
    'Never claim a record was saved before the action succeeds.'
)
TOOL_DESCRIPTIONS = {
    'create_customer': (
        "Prepare a customer with a name and optional contact details. Creation requires "
        "confirmation and the current user's customer maintenance permissions."
    ),
    'find_customers': (
        'Count and list accessible customers in the current company, optionally filtered '
        'by a search term. Counts follow the same customer identity and record rules as the list.'
    ),
    'find_products': (
        'Find accessible sellable products by name or SKU for the current company.'
    ),
    'find_quotes': (
        'Count and list accessible quotations and sales orders in the current company, '
        'optionally filtered by order number, customer or state.'
    ),
    'find_activities': 'List accessible follow-up activities for one customer.',
    'create_followup': (
        'Prepare a customer follow-up activity assigned to the requesting employee. '
        'Create it only after confirmation.'
    ),
    'create_quote_draft': (
        'Prepare a draft quotation from an accessible customer, products and quantities. '
        'Odoo computes prices and taxes after confirmation; the order remains a draft '
        'and is not sent to the customer.'
    ),
}
TOOL_NAMES = {
    'find_quotes': ('Find Quotations', 'Find Sales Orders'),
    'create_quote_draft': ('Create Quotation Draft', 'Create Sales Order Draft'),
}


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {'lang': None})
    for key, description in TOOL_DESCRIPTIONS.items():
        tool = env.ref(f'qing_ai_sale.agent_tool_{key}', raise_if_not_found=False)
        if not tool:
            continue
        values = {}
        if not tool.description:
            values['description'] = description
        old_name, new_name = TOOL_NAMES.get(key, (False, False))
        if old_name and tool.name == old_name:
            values['name'] = new_name
        if values:
            tool.write(values)

    skill = env.ref('qing_ai_sale.skill_sales', raise_if_not_found=False)
    edit_tool = env.ref('qing_ai_sale.agent_tool_update_customer', raise_if_not_found=False)
    if not skill or not edit_tool:
        return
    untouched = (
        skill.active and not skill.group_ids
        and skill.instructions == LEGACY_INSTRUCTIONS
        and set(skill.tool_ids.mapped('key')) == LEGACY_KEYS
        and all(tool.active and tool.review_state == 'published' and not tool.group_ids
                for tool in skill.tool_ids)
    )
    if untouched:
        skill.write({
            'description': 'Customer queries, creation and editing, sales order drafts and follow-up workflows.',
            'instructions': SALES_INSTRUCTIONS,
            'tool_ids': [Command.link(edit_tool.id)],
        })
