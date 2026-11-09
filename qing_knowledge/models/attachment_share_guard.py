"""Keep knowledge files out of public attachment shares on upgraded databases.

QingERP does not depend on mommy_base. A database upgraded from an older
release may still have it installed, and its ``attachment.share`` model would
let a reader publish a knowledge file through a public link. When that model is
loaded, its create and write are wrapped the same way base_automation patches
models at registry setup.
"""
from odoo import api, models
from odoo.exceptions import AccessError

SHARE_MODEL = 'attachment.share'


def _check_shareable(env, attachment_ids):
    attachments = env['ir.attachment'].sudo().browse([i for i in attachment_ids if i]).exists()
    if any(attachment.res_model == 'qing.knowledge.asset' for attachment in attachments):
        raise AccessError(env._('Knowledge files cannot be shared publicly.'))


class QingKnowledgeAsset(models.Model):
    _inherit = 'qing.knowledge.asset'

    def _register_hook(self):
        super()._register_hook()
        Share = self.env.registry.get(SHARE_MODEL)
        if Share is None or getattr(Share, '_qing_knowledge_guard', False):
            return
        origin_create, origin_write = Share.create, Share.write

        @api.model_create_multi
        def create(share, vals_list):
            _check_shareable(share.env, [vals.get('attachment_id') for vals in vals_list])
            return origin_create(share, vals_list)

        def write(share, vals):
            attachment_ids = share.sudo().mapped('attachment_id').ids + [vals.get('attachment_id')]
            _check_shareable(share.env, attachment_ids)
            return origin_write(share, vals)

        Share.create = create
        Share.write = write
        Share._qing_knowledge_guard = True
