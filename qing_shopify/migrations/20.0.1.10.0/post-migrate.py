from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    ExceptionModel = env['qing.channel.exception']
    scope = [('object_model', '=', 'qing.channel.action'), ('resolved', '=', False),
             ('store_id.platform', '=', 'shopify')]
    pending = ExceptionModel.search(scope + [('code', '=', 'shopify_refund_pending')])
    if not pending:
        return
    actions = env['qing.channel.action'].search([
        ('id', 'in', pending.mapped('object_id')), ('store_id.platform', '=', 'shopify'),
        ('kind', '=', 'refund'), ('state', '=', 'review'),
    ])
    other_issues = ExceptionModel.search(scope + [
        ('object_id', 'in', actions.ids), ('code', '!=', 'shopify_refund_pending'),
    ])
    manual_action_ids = set(other_issues.mapped('object_id'))
    retry = actions.filtered(lambda action: action.id not in manual_action_ids)
    retry.write({'state': 'pending', 'tries': 0, 'retry_at': False})
    retry_ids = set(retry.ids)
    pending.filtered(lambda issue: issue.object_id in retry_ids).resolved = True
