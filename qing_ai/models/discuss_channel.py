"""Native Discuss bridge for Qing AI agents."""

import logging

from markupsafe import Markup

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.tools import html2plaintext

from .qing_ai_conversation import render_assistant_markdown


_logger = logging.getLogger(__name__)


class DiscussChannel(models.Model):
    _inherit = 'discuss.channel'

    qing_ai_agent_id = fields.Many2one(
        'qing.ai.agent', copy=False, readonly=True, ondelete='set null', index=True,
    )
    qing_ai_conversation_id = fields.Many2one(
        'qing.ai.conversation', copy=False, readonly=True, ondelete='set null', index=True,
    )
    qing_ai_identity_id = fields.Many2one(
        'qing.ai.agent.identity', copy=False, readonly=True, ondelete='set null', index=True,
    )
    qing_ai_owner_id = fields.Many2one(
        'res.users', copy=False, readonly=True, ondelete='set null', index=True,
    )

    @api.model_create_multi
    def create(self, vals_list):
        protected = {
            'qing_ai_agent_id', 'qing_ai_conversation_id', 'qing_ai_identity_id',
            'qing_ai_owner_id',
        }
        if not self.env.su and any(protected & values.keys() for values in vals_list):
            raise AccessError(self.env._('AI chat identity is managed by Qing AI.'))
        return super().create(vals_list)

    def write(self, values):
        protected = {
            'qing_ai_agent_id', 'qing_ai_conversation_id', 'qing_ai_identity_id',
            'qing_ai_owner_id',
        }
        if protected & values.keys() and not (
                self.env.su and self.env.context.get('qing_ai_manage_channel')):
            raise AccessError(self.env._('AI chat identity is managed by Qing AI.'))
        return super().write(values)

    def _qing_ai_chat_is_valid(self, message):
        self.ensure_one()
        agent = self.qing_ai_agent_id
        conversation = self.qing_ai_conversation_id
        identity = self.qing_ai_identity_id
        owner = self.qing_ai_owner_id
        members = self.channel_member_ids.partner_id
        return bool(
            self.channel_type == 'chat'
            and identity and identity.agent_id == agent
            and identity.company_id == conversation.company_id
            and identity.partner_id in members
            and owner and owner.partner_id in members
            and conversation and conversation.owner_id == owner
            and conversation.company_id in owner.company_ids
            and conversation.agent_id == agent
            and message.message_type == 'comment'
            and message.author_id == owner.partner_id
        )

    def _qing_ai_post_reply(self, content):
        self.ensure_one()
        partner = self.qing_ai_identity_id.sudo().partner_id.with_context(active_test=False)
        body = Markup(render_assistant_markdown(content))
        return self.with_context(qing_ai_discuss_reply=True).sudo().message_post(
            author_id=partner.id,
            body=body,
            message_type='comment',
            silent=True,
            subtype_xmlid='mail.mt_comment',
        )

    def _qing_ai_set_typing(self, is_typing):
        """Show or hide the agent's typing indicator; cosmetic, so never fail the caller."""
        self.ensure_one()
        partner = self.qing_ai_identity_id.partner_id
        members = self.sudo().channel_member_ids.filtered(lambda member: member.partner_id == partner)
        try:
            members._notify_typing(is_typing)
        except Exception:  # noqa: BLE001 - the reply itself must not depend on the indicator
            _logger.debug('Qing AI typing indicator failed for channel %s', self.id, exc_info=True)

    def _qing_ai_reply_to_message(self, message):
        self.ensure_one()
        owner = self.qing_ai_owner_id
        company = self.qing_ai_conversation_id.company_id
        conversation = self.qing_ai_conversation_id.with_user(owner).with_company(company)
        agent = self.qing_ai_agent_id.with_user(owner).with_company(company)
        try:
            agent.require_available()
            text = html2plaintext(message.body or '', include_references=False).strip()
            if not text:
                raise UserError(self.env._('Send text to this AI agent in Discuss.'))
            previous_message_id = max(conversation.message_ids.ids, default=0)
            conversation.assistant_send(
                text,
                task='ask',
                provider=conversation.provider,
                target_language=conversation.target_language or 'English',
            )
            reply = self.env['qing.ai.message'].sudo().search([
                ('conversation_id', '=', conversation.id),
                ('id', '>', previous_message_id),
                ('role', 'in', ('assistant', 'error')),
            ], order='id desc', limit=1)
            if reply:
                self._qing_ai_post_reply(reply.content)
        except (AccessError, UserError) as error:
            self._qing_ai_post_reply(str(error))
        except Exception:
            _logger.exception('Qing AI failed to answer Discuss message %s', message.id)
            self._qing_ai_post_reply(
                self.env._('The AI request failed. Please try again or open Qing AI for details.')
            )

    def _message_post_after_hook(self, message):
        result = super()._message_post_after_hook(message)
        if self.env.context.get('qing_ai_discuss_reply'):
            return result
        for channel in self.sudo().filtered('qing_ai_agent_id'):
            if channel._qing_ai_chat_is_valid(message):
                # Answer in the background so sending the message never waits for the provider.
                self.env['qing.ai.discuss.job']._enqueue(channel, message)
        return result
