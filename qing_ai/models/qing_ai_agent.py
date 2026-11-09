"""User-selectable, company-scoped Qing AI agent profiles."""

import psycopg2

from odoo import api, fields, models
from odoo.exceptions import AccessError


class QingAiAgent(models.Model):
    _name = 'qing.ai.agent'
    _inherit = ['avatar.mixin']
    _description = 'Qing AI Agent'
    _order = 'sequence, name, id'

    active = fields.Boolean(default=True)
    company_id = fields.Many2one('res.company', string='Company',
                                 help='Leave empty to make this agent available in every company.')
    description = fields.Text()
    group_ids = fields.Many2many('res.groups', string='Allowed Groups',
                                 help='Leave empty for all internal users.')
    is_default = fields.Boolean(
        string='Default Agent',
        help='When several agents are default, the first available agent by sequence is used.',
    )
    name = fields.Char(required=True, translate=True)
    identity_ids = fields.One2many(
        'qing.ai.agent.identity', 'agent_id', string='Discuss Identities', copy=False,
    )
    provider_code = fields.Selection(
        selection='_selection_provider_code', string='Preferred Provider',
        help='Leave empty to use the system default provider.',
    )
    sequence = fields.Integer(default=10)
    skill_ids = fields.Many2many('qing.ai.skill', string='Skills')
    system_prompt = fields.Text(help='Professional role and response rules. Do not put secrets here.')

    def _discuss_partner_values(self):
        self.ensure_one()
        return {
            'active': False,
            'company_id': self.company_id.id or False,
            'image_1920': self.image_1920,
            'name': self.name,
        }

    def _ensure_discuss_identity(self):
        """Return this agent's hidden Discuss identity for the active company."""
        self.ensure_one()
        self.require_available()
        identity = self.env['qing.ai.agent.identity'].sudo().search([
            ('agent_id', '=', self.id), ('company_id', '=', self.env.company.id),
        ], limit=1)
        if not identity:
            try:
                with self.env.cr.savepoint():
                    partner_values = self._discuss_partner_values()
                    partner_values['company_id'] = self.env.company.id
                    partner = self.env['res.partner'].sudo().with_context(active_test=False).create(
                        partner_values
                    )
                    identity = self.env['qing.ai.agent.identity'].sudo().create({
                        'agent_id': self.id,
                        'company_id': self.env.company.id,
                        'partner_id': partner.id,
                    })
            except psycopg2.errors.UniqueViolation:
                return self._ensure_discuss_identity()
        return identity

    def write(self, values):
        result = super().write(values)
        if {'name', 'company_id', 'image_1920'} & values.keys():
            for agent in self:
                for identity in agent.sudo().identity_ids:
                    partner_values = agent._discuss_partner_values()
                    partner_values['company_id'] = identity.company_id.id
                    identity.partner_id.with_context(active_test=False).write(partner_values)
        return result

    def unlink(self):
        partners = self.sudo().identity_ids.partner_id.with_context(active_test=False)
        result = super().unlink()
        partners.sudo().write({'active': False})
        return result

    @api.model
    def _selection_provider_code(self):
        codes = self.env['qing.ai.service']._provider_codes()
        return [(code, code.title()) for code in codes]

    def available_for_user(self):
        if self.env.user.share:
            return self.browse()
        groups = self.env.user.all_group_ids
        company = self.env.company
        return self.filtered(lambda agent: agent.active and
                             (not agent.company_id or agent.company_id == company) and
                             (not agent.group_ids or bool(agent.group_ids & groups)))

    def require_available(self):
        self.ensure_one()
        if self not in self.available_for_user():
            raise AccessError(self.env._('This AI agent is not available to you.'))
        return self

    @api.model
    def default_for_user(self):
        domain = [('active', '=', True), ('company_id', 'in', [False, self.env.company.id])]
        agents = self.search(domain).available_for_user()
        return agents.filtered('is_default')[:1] or agents[:1]

    @api.model
    def assistant_agents(self):
        domain = [('active', '=', True), ('company_id', 'in', [False, self.env.company.id])]
        agents = self.search(domain).available_for_user()
        return [{
            'id': agent.id,
            'name': agent.name,
            'description': agent.description or '',
            'avatar_url': '/web/image/qing.ai.agent/%s/avatar_128' % agent.id,
        } for agent in agents]

    def action_open_discuss(self):
        """Open the current user's private native Discuss chat with this agent."""
        self.ensure_one()
        self.require_available()
        identity = self._ensure_discuss_identity()
        partner = identity.partner_id
        channel = self.env['discuss.channel']._get_or_create_chat([partner.id])
        conversation = channel.sudo().qing_ai_conversation_id
        if not conversation or (
                conversation.agent_id != self
                or conversation.company_id != self.env.company
                or conversation.owner_id != self.env.user):
            conversation = self.env['qing.ai.conversation'].create({
                'agent_id': self.id,
                'name': self.env._('%(agent)s in Discuss', agent=self.name)[:120],
            })
        channel.sudo().with_context(qing_ai_manage_channel=True).write({
            'qing_ai_agent_id': self.id,
            'qing_ai_conversation_id': conversation.id,
            'qing_ai_identity_id': identity.id,
            'qing_ai_owner_id': self.env.uid,
        })
        return {
            'type': 'ir.actions.client',
            'tag': 'mail.action_discuss',
            'params': {'active_id': 'discuss.channel_%s' % channel.id},
        }
