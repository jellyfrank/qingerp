"""Hidden Discuss identities used by Qing AI agents."""

from odoo import api, fields, models
from odoo.exceptions import AccessError


class QingAiAgentIdentity(models.Model):
    _name = 'qing.ai.agent.identity'
    _description = 'Qing AI Agent Discuss Identity'

    agent_id = fields.Many2one('qing.ai.agent', required=True, ondelete='cascade', index=True)
    company_id = fields.Many2one('res.company', required=True, ondelete='cascade', index=True)
    partner_id = fields.Many2one('res.partner', required=True, ondelete='restrict', index=True)

    _agent_company_unique = models.Constraint(
        'UNIQUE(agent_id, company_id)',
        'An AI agent can have only one Discuss identity per company.',
    )

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            raise AccessError(self.env._('AI Discuss identities are managed by Qing AI.'))
        return super().create(vals_list)

    def write(self, values):
        if not self.env.su:
            raise AccessError(self.env._('AI Discuss identities are managed by Qing AI.'))
        return super().write(values)

    def unlink(self):
        if not self.env.su:
            raise AccessError(self.env._('AI Discuss identities are managed by Qing AI.'))
        partners = self.mapped('partner_id').with_context(active_test=False)
        result = super().unlink()
        partners.write({'active': False})
        return result
