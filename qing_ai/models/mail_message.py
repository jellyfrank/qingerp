"""Show Qing AI execution details and pending previews under native Discuss replies."""

from odoo import api, fields, models


REPLY_CONTEXT_KEY = 'qing_ai_reply_message_id'


class MailMessage(models.Model):
    _inherit = 'mail.message'

    def _qing_ai_reply_link(self, values, reply_id):
        """
        Return the Qing AI answer a new Discuss reply may link to.

        Only a reply posted by the channel's AI author (the agent identity, or Qing Assistant in
        its own chat), in the channel bound to the answer's conversation, can be linked; any
        other value is ignored.
        """
        if not (reply_id and isinstance(reply_id, int) and self.env.su):
            return False
        if values.get('model') != 'discuss.channel' or not values.get('res_id'):
            return False
        channel = self.env['discuss.channel'].browse(values['res_id']).exists()
        reply = self.env['qing.ai.message'].browse(reply_id).exists()
        if not channel or not reply or reply.conversation_id != channel.qing_ai_conversation_id:
            return False
        if values.get('author_id') != channel._qing_ai_assistant_partner().id:
            return False
        return reply.id

    def _qing_ai_store_meta(self):
        """Execution steps, token usage and still-pending previews of the linked answer."""
        self.ensure_one()
        reply = self.sudo().qing_ai_message_id
        conversation = reply.conversation_id
        owner = conversation.owner_id
        operations = []
        if reply.operation_refs and owner and (self.env.su or self.env.user == owner):
            pending = conversation.with_user(owner).with_company(
                conversation.company_id
            )._pending_business_operations()
            by_id = {item['id']: item for item in pending}
            operations = [by_id[ref] for ref in reply.operation_refs if ref in by_id]
        return {
            'failed': reply.role == 'error',
            'model': reply.model or '',
            'operations': operations,
            'process_steps': reply.process_steps or [],
            'usage_summary': self.env['qing.ai.service']._usage_summary(reply.usage),
        }

    def _store_message_fields(self, res, **kwargs):
        super()._store_message_fields(res, **kwargs)
        res.attr(
            'qing_ai_meta',
            lambda message: message._qing_ai_store_meta(),
            predicate=lambda message: bool(message.sudo().qing_ai_message_id),
        )

    @api.model_create_multi
    def create(self, vals_list):
        reply_id = self.env.context.get(REPLY_CONTEXT_KEY)
        for values in vals_list:
            values.pop('qing_ai_message_id', None)
            link = self._qing_ai_reply_link(values, reply_id)
            if link:
                values['qing_ai_message_id'] = link
        return super().create(vals_list)

    qing_ai_message_id = fields.Many2one(
        'qing.ai.message', readonly=True, copy=False, ondelete='set null', index='btree_not_null',
    )
