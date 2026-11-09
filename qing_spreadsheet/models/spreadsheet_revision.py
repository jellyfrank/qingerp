from odoo import api, fields, models, _
from odoo.exceptions import AccessError

from ..services.workbook import INTERNAL_WRITE


class QingSpreadsheetRevision(models.Model):
    _name = 'qing.spreadsheet.revision'
    _description = 'Workbook Version'
    _order = 'version desc, id desc'

    _version_unique = models.Constraint('unique(workbook_id, version)', 'A workbook version must be unique.')
    _request_unique = models.Constraint('unique(workbook_id, request_id)', 'A save request must be unique.')

    author_id = fields.Many2one('res.users', required=True, ondelete='restrict', readonly=True)
    company_id = fields.Many2one(related='workbook_id.company_id', store=True, index=True)
    data = fields.Text(required=True, readonly=True)
    digest = fields.Char(required=True, readonly=True)
    input_digest = fields.Char(required=True, readonly=True)
    label = fields.Char(readonly=True)
    operation = fields.Selection([('save', 'Save'), ('apply', 'Apply'), ('reload', 'Reload')], default='save', required=True, readonly=True)
    result_json = fields.Text(readonly=True)
    request_id = fields.Char(required=True, readonly=True)
    version = fields.Integer(required=True, readonly=True)
    workbook_id = fields.Many2one('qing.spreadsheet', required=True, ondelete='cascade', index=True, readonly=True)

    @api.model_create_multi
    def create(self, vals_list):
        if self.env.context.get('_qing_spreadsheet_internal') is not INTERNAL_WRITE:
            raise AccessError(_('Versions can only be created by saving a workbook.'))
        return super().create(vals_list)

    def write(self, vals):
        raise AccessError(_('Saved versions are immutable.'))

    def unlink(self):
        raise AccessError(_('Saved versions are removed only with their workbook.'))
