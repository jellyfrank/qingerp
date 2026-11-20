from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    Issue = env['qing.channel.exception']
    scope = [('store_id.platform', '=', 'shopify'), ('resolved', '=', False)]
    disconnected = 'shopify_disconnected'
    models = ('qing.channel.request', 'qing.channel.delivery', 'qing.channel.action')
    for model in models:
        last_id = 0
        while True:
            batch = Issue.search(scope + [
                ('code', '=', disconnected), ('object_model', '=', model), ('id', '>', last_id),
            ], order='id', limit=250)
            if not batch:
                break
            for issue in batch:
                item = env[model].browse(issue.object_id).exists()
                if not item or item.store_id != issue.store_id or item.state != 'review':
                    continue
                related = [('store_id', '=', issue.store_id.id), ('object_model', '=', model),
                           ('object_id', '=', item.id)]
                if model == 'qing.channel.request':
                    related = ['|', '&', ('object_model', '=', model), ('object_id', '=', item.id),
                               '&', ('object_model', '=', 'qing.channel.action'), ('object_id', '=', item.action_id.id),
                               ('store_id', '=', issue.store_id.id)]
                if Issue.search_count(scope + related + [('code', '!=', disconnected)], limit=1):
                    continue
                if model == 'qing.channel.request':
                    item._recheck_after_exception(disconnected)
                elif model == 'qing.channel.action' and item.request_ids:
                    continue
                else:
                    item.write({'state': 'pending', 'tries': 0, 'retry_at': False})
                Issue.search(scope + related + [('code', '=', disconnected)]).resolved = True
            last_id = batch[-1].id
            env.invalidate_all()
