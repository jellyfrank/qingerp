from odoo import api, fields, models
from odoo.exceptions import AccessError


class KnowledgeTransfer(models.Model):
    _name = 'qing.knowledge.transfer'
    _description = 'Knowledge Import Idempotency Record'
    _order = 'id desc'

    _unique_operation = models.Constraint('UNIQUE(operation_uuid)', 'Import operation UUID must be unique.')

    actor_id = fields.Many2one('res.users', required=True, readonly=True)
    operation_uuid = fields.Char(required=True, readonly=True)
    request_hash = fields.Char(required=True, readonly=True)
    result_json = fields.Text(required=True, readonly=True)
    space_id = fields.Many2one('qing.knowledge.space', required=True, readonly=True)

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            raise AccessError(self.env._('Knowledge transfer records are server-managed.'))
        return super().create(vals_list)

    def write(self, vals):
        raise AccessError(self.env._('Knowledge transfer records are immutable.'))

    def unlink(self):
        raise AccessError(self.env._('Knowledge transfer records are immutable.'))
