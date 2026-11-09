"""Administrator-selected scope for indexing installed business capabilities."""

from odoo import fields, models
from odoo.exceptions import AccessError


class QingAiDiscoveryWizard(models.TransientModel):
    _name = 'qing.ai.discovery.wizard'
    _description = 'Qing AI Action Discovery'

    module_id = fields.Many2one(
        'ir.module.module', required=True, string='Installed Module',
        domain=[('state', '=', 'installed')],
    )
    model_id = fields.Many2one('ir.model', string='Business Model',
                               domain=[('abstract', '=', False), ('transient', '=', False)])

    def action_scan(self):
        self.ensure_one()
        if not self.env.user.has_group('base.group_system'):
            raise AccessError(self.env._('Only system administrators can scan AI actions.'))
        tools = self.env['qing.ai.tool'].discover_module(self.module_id.id, self.model_id.id or None)
        return {
            'type': 'ir.actions.act_window',
            'name': self.env._('Discovered Actions'),
            'res_model': 'qing.ai.tool', 'view_mode': 'list,form',
            'domain': [('id', 'in', tools.ids)],
        }
