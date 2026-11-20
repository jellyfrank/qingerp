"""Native Discuss bridge for Qing AI agents."""

import logging

from markupsafe import Markup

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.tools import html2plaintext

from .mail_message import REPLY_CONTEXT_KEY
from .qing_ai_conversation import (
    ASSISTANT_PERSONA_CONTEXT, MAX_INPUT_CHARS, render_assistant_markdown,
)


_logger = logging.getLogger(__name__)

# Lets Qing Assistant answer through the user's AI agent, with that user's permissions.
ASSISTANT_DATA_PARAM = 'qing_ai.assistant_data_access'
GENERAL_CHAT_SYSTEM = (
    "You are Qing Assistant (小青), QingERP's friendly chat assistant. "
    "Reply in the user's language. Answer general questions and casual conversation. "
    "You have no access to ERP records or actions. If asked for a QingERP procedure, "
    "ask for the app and operation so the public manual can be checked."
)


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

    def _store_channel_fields(self, res):
        super()._store_channel_fields(res)
        self._qing_ai_store_fields(res)

    def _sync_field_names(self, res):
        super()._sync_field_names(res)
        self._qing_ai_store_fields(res[None])

    def _qing_ai_store_fields(self, res):
        """Tell the web client which chats belong to an AI agent and to which conversation."""
        def is_ai_chat(channel):
            return bool(channel.qing_ai_agent_id)

        res.attr('qing_ai_agent_id', lambda channel: channel.qing_ai_agent_id.id, predicate=is_ai_chat)
        res.attr(
            'qing_ai_conversation_id', lambda channel: channel.qing_ai_conversation_id.id,
            predicate=is_ai_chat,
        )

    def _qing_ai_check_owner(self):
        """Only the user who opened an AI chat may drive it."""
        self.ensure_one()
        if not self.qing_ai_agent_id or self.qing_ai_owner_id != self.env.user:
            raise AccessError(self.env._('This AI chat is not yours.'))

    def _qing_ai_conversation_env(self):
        """The bound conversation, acting as the chat owner in the conversation's company."""
        self.ensure_one()
        conversation = self.sudo().qing_ai_conversation_id
        conversation = conversation.with_user(self.sudo().qing_ai_owner_id).with_company(conversation.company_id)
        if not self.sudo().qing_ai_identity_id:
            # Qing Assistant answers in its own name, not as the agent behind it.
            conversation = conversation.with_context(**{ASSISTANT_PERSONA_CONTEXT: True})
        return conversation

    def _qing_ai_last_answer_id(self):
        self.ensure_one()
        return max(self.sudo().qing_ai_conversation_id.message_ids.ids, default=0)

    def _qing_ai_is_assistant_chat(self):
        """Whether this is a user's private chat with Qing Assistant (the system bot)."""
        self.ensure_one()
        channel = self.sudo()
        partners = channel.channel_member_ids.partner_id
        return bool(
            channel.channel_type == 'chat' and not channel.qing_ai_identity_id
            and len(partners) == 2 and self.env.ref('base.partner_root') in partners
        )

    def _qing_ai_assistant_partner(self):
        """Author of AI replies in this chat: the agent identity, else Qing Assistant."""
        self.ensure_one()
        return self.sudo().qing_ai_identity_id.partner_id or self.env.ref('base.partner_root')

    def _qing_ai_bind_assistant(self, owner):
        """
        Let Qing Assistant answer this chat through an AI agent of ``owner``.

        The agent acts as ``owner`` in the current company, so it only finds and changes what
        that user may access. A still available bound agent is kept; otherwise the user's
        default agent is used.

        :param owner: user chatting with Qing Assistant
        :return: True when the chat is bound to an agent conversation of ``owner``
        """
        self.ensure_one()
        params = self.env['ir.config_parameter'].sudo()
        if not params.get_bool(ASSISTANT_DATA_PARAM, True) or not self._qing_ai_is_assistant_chat():
            return False
        channel = self.sudo()
        Agent = self.env['qing.ai.agent'].with_user(owner)
        agent = channel.qing_ai_agent_id.with_env(Agent.env).available_for_user() or Agent.default_for_user()
        if not agent:
            return False
        conversation = channel.qing_ai_conversation_id
        if (channel.qing_ai_agent_id == agent and channel.qing_ai_owner_id == owner
                and conversation.agent_id == agent and conversation.owner_id == owner
                and conversation.company_id == Agent.env.company):
            return True
        conversation = self.env['qing.ai.conversation'].with_env(Agent.env).create({
            'agent_id': agent.id,
            'name': Agent.env._('Qing Assistant in Discuss'),
        })
        channel.with_context(qing_ai_manage_channel=True).write({
            'qing_ai_agent_id': agent.id,
            'qing_ai_conversation_id': conversation.id,
            'qing_ai_owner_id': owner.id,
        })
        return True

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

    def _qing_ai_post_reply(self, content, answer=None):
        """
        Post an agent reply; ``answer`` links it to the Qing AI message it shows.

        :param content: Markdown text of the reply
        :param answer: optional ``qing.ai.message`` behind the reply
        """
        self.ensure_one()
        partner = self._qing_ai_assistant_partner().with_context(active_test=False)
        body = Markup(render_assistant_markdown(content))
        return self.with_context(**{
            'qing_ai_discuss_reply': True, REPLY_CONTEXT_KEY: answer.id if answer else False,
        }).sudo().message_post(
            author_id=partner.id,
            body=body,
            message_type='comment',
            silent=True,
            subtype_xmlid='mail.mt_comment',
        )

    def _qing_ai_set_typing(self, is_typing):
        """Show or hide the agent's typing indicator; cosmetic, so never fail the caller."""
        self.ensure_one()
        partner = self.qing_ai_identity_id.partner_id or self.env.ref('base.partner_root')
        members = self.sudo().channel_member_ids.filtered(lambda member: member.partner_id == partner)
        try:
            members._notify_typing(is_typing)
        except Exception:  # noqa: BLE001 - the reply itself must not depend on the indicator
            _logger.debug('Qing AI typing indicator failed for channel %s', self.id, exc_info=True)

    def _qing_ai_reply_to_message(self, message):
        """
        Start answering a Discuss message.

        A direct answer is complete after this call; an ERP request leaves a planner run
        that ``_qing_ai_continue_reply`` advances one bounded round at a time.
        """
        self.ensure_one()
        if not self.qing_ai_agent_id:
            return self._qing_ai_reply_from_manual(message)
        conversation = self._qing_ai_conversation_env()
        agent = self.qing_ai_agent_id.with_env(conversation.env)
        try:
            agent.require_available()
            text = html2plaintext(message.body or '', include_references=False).strip()
            if not text:
                raise UserError(self.env._('Send text to this AI agent in Discuss.'))
            conversation.assistant_send(
                text,
                task='ask',
                provider=conversation.provider,
                target_language=conversation.target_language or 'English',
                background=True,
                stream=True,
            )
        except (AccessError, UserError) as error:
            self._qing_ai_post_reply(str(error))
        except Exception:
            _logger.exception('Qing AI failed to answer Discuss message %s', message.id)
            self._qing_ai_post_reply(
                self.env._('The AI request failed. Please try again or open Qing AI for details.')
            )

    def _qing_ai_continue_reply(self, run_id):
        """Advance the planner run of the current answer by one round."""
        self.ensure_one()
        conversation = self._qing_ai_conversation_env()
        try:
            conversation.assistant_continue_run(run_id, stream=True)
        except (AccessError, UserError) as error:
            self._qing_ai_post_reply(str(error))
        except Exception:
            _logger.exception('Qing AI failed to continue run %s in channel %s', run_id, self.id)
            self._qing_ai_post_reply(
                self.env._('The AI request failed. Please try again or open Qing AI for details.')
            )

    def _qing_ai_publish_answers(self, after_id):
        """
        Post the conversation's new answers into this chat, oldest first.

        The last posted answer keeps the business previews that are new in this turn, so the
        chat can offer their confirmation buttons.

        :param after_id: last ``qing.ai.message`` id already shown in the chat
        :return: id of the last answer now shown
        """
        self.ensure_one()
        conversation = self._qing_ai_conversation_env()
        answers = self.env['qing.ai.message'].sudo().search([
            ('conversation_id', '=', conversation.id),
            ('id', '>', after_id),
            ('role', 'in', ('assistant', 'error')),
        ], order='id')
        if not answers:
            return after_id
        shown = self.env['qing.ai.message'].sudo().search([
            ('conversation_id', '=', conversation.id), ('operation_refs', '!=', False),
        ]).mapped('operation_refs')
        offered = {ref for refs in shown for ref in refs}
        new_refs = [item['id'] for item in conversation._pending_business_operations()
                    if item['id'] not in offered]
        if new_refs:
            answers[-1].write({'operation_refs': new_refs})
        for answer in answers:
            self._qing_ai_post_reply(answer.content, answer=answer)
        return answers[-1].id

    def qing_ai_advance(self):
        """
        Advance this chat's oldest unfinished reply by one bounded step.

        The chat window calls this repeatedly after the user posts; the reply cron only
        finishes replies the browser left behind.

        :return: ``{'running': bool, 'progressed': bool}``
        """
        if not self.sudo().qing_ai_agent_id and self._qing_ai_is_assistant_chat():
            # Without company data access, Qing Assistant replies without the chat window.
            return {'running': False, 'progressed': False}
        self._qing_ai_check_owner()
        Job = self.env['qing.ai.discuss.job'].sudo()
        domain = [('channel_id', '=', self.id), ('state', 'in', ('pending', 'running'))]
        job = Job.search(domain, limit=1)
        if not job:
            return {'running': False, 'progressed': False}
        if not job._lock():
            # Another worker holds this step; the caller retries shortly.
            return {'running': True, 'progressed': False}
        job._step()
        return {'running': bool(Job.search_count(domain)), 'progressed': True}

    def qing_ai_confirm_operation(self, operation_id):
        """
        Confirm a business preview offered in this chat, then continue the request.

        :param operation_id: preview id as offered with the reply
        :return: ``{'running': bool}``; the chat window then calls ``qing_ai_advance``
        """
        self._qing_ai_check_owner()
        conversation = self._qing_ai_conversation_env()
        after_id = self._qing_ai_last_answer_id()
        conversation.assistant_confirm_operation(operation_id)
        active_run = conversation._assistant_active_run()
        self.env['qing.ai.discuss.job'].sudo().create({
            'channel_id': self.id,
            'reply_after_id': after_id,
            'run_id': active_run['id'] if active_run else False,
            'state': 'running',
        })
        return {'running': True}

    def qing_ai_new_topic(self):
        """Start a new AI conversation in this chat; earlier messages stay visible only."""
        self._qing_ai_check_owner()
        current = self.sudo().qing_ai_conversation_id
        agent = self.sudo().qing_ai_agent_id
        name = (self.env._('%(agent)s in Discuss', agent=agent.name)[:120]
                if self.sudo().qing_ai_identity_id else self.env._('Qing Assistant in Discuss'))
        conversation = self.env['qing.ai.conversation'].with_company(current.company_id).create({
            'agent_id': agent.id,
            'name': name,
        })
        self.env['qing.ai.discuss.job'].sudo().search([
            ('channel_id', '=', self.id), ('state', 'in', ('pending', 'running')),
        ]).write({'state': 'done'})
        self.sudo().with_context(qing_ai_manage_channel=True).write({
            'qing_ai_conversation_id': conversation.id,
        })
        partner = self._qing_ai_assistant_partner().with_context(active_test=False)
        self.with_context(qing_ai_discuss_reply=True).sudo().message_post(
            author_id=partner.id,
            body=Markup('<p>%s</p>') % self.env._(
                'New topic started. Earlier messages are no longer sent to the AI.'
            ),
            message_type='notification',
            silent=True,
            subtype_xmlid='mail.mt_comment',
        )
        self._qing_ai_set_typing(False)
        return True

    def _qing_ai_assistant_author(self, message):
        self.ensure_one()
        user = message.create_uid
        assistant = self.env.ref('base.partner_root')
        if (
            self.channel_type == 'chat'
            and message.model == self._name and message.res_id == self.id
            and message.message_type == 'comment'
            and user.active and user._is_internal() and user.odoobot_state != 'disabled'
            and message.author_id == user.partner_id
            and self.channel_member_ids.partner_id == (assistant | user.partner_id)
        ):
            return user
        return self.env['res.users']

    def _qing_ai_reply_from_manual(self, message):
        owner = self._qing_ai_assistant_author(message)
        if not owner:
            return
        channel = self.with_user(owner).with_context(lang=owner.lang)
        manual = channel.env['qing.public.manual']
        text = html2plaintext(message.body or '', include_references=False).strip()
        section, question = manual._match_question(text)
        if not section:
            return
        result = None
        guide = channel.env['qing.ai.guide']
        if question and guide._online_allowed():
            try:
                result = guide._answer_documented_question(section, question['text'], public_only=True)
            except UserError:
                pass  # The public passage is still available if the provider or quota is unavailable.
        channel._qing_ai_post_assistant_reply(
            message, manual._format_reply(section, answer=result['answer'] if result else None),
        )

    def _qing_ai_reply_from_general_chat(self, message):
        owner = self._qing_ai_assistant_author(message)
        if not owner:
            return
        channel = self.with_user(owner).with_context(lang=owner.lang)
        bot = channel.env['mail.bot']
        manual = channel.env['qing.public.manual']
        text = html2plaintext(message.body or '', include_references=False).strip()
        if not text:
            return
        # Recheck edited messages before sending any text to the provider.
        body = bot._qing_assistant_local_reply(text)
        if not body:
            section, _question = manual._match_question(text)
            if section:
                body = manual._format_reply(section)
            elif manual._looks_like_erp_question(text):
                body = bot._qing_assistant_manual_miss()
            else:
                body = channel._qing_ai_general_chat_body(bot, text)
        channel._qing_ai_post_assistant_reply(message, body)

    def _qing_ai_general_chat_body(self, bot, text):
        provider, config = bot._qing_assistant_provider()
        if not provider:
            return bot._qing_assistant_no_model()
        if len(text) > MAX_INPUT_CHARS:
            return Markup('%s') % self.env._('This message is too long. Please send a shorter message.')
        conversation = self.env['qing.ai.conversation']
        try:
            result = conversation._provider_complete(
                text, provider=provider, config=config, system=GENERAL_CHAT_SYSTEM,
            )
        except UserError:
            return Markup('%s') % self.env._('I could not reply using the chat model. Please try again later.')
        return Markup(render_assistant_markdown(conversation._answer_with_notice(result)))

    def _qing_ai_post_assistant_reply(self, message, body):
        self.sudo().message_post(
            author_id=self.env.ref('base.partner_root').id,
            parent_id=message.id,
            body=body,
            message_type='comment',
            silent=True,
            subtype_xmlid='mail.mt_comment',
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
