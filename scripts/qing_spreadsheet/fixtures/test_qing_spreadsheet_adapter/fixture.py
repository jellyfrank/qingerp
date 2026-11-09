"""Synthetic record used only in disposable integration tests, not a business addon."""
import hashlib
import json
from odoo import fields, models
from odoo.exceptions import ValidationError


class Fixture(models.Model):
    _name = 'qing.spreadsheet.fixture'
    _description = 'Spreadsheet Adapter Test Record'
    _inherit = ['qing.spreadsheet.record.mixin']
    _qing_spreadsheet_enabled = True

    def _qing_spreadsheet_lock(self):
        self.check_access('write')
        self.env.cr.execute('SELECT id FROM qing_spreadsheet_fixture WHERE id = %s FOR UPDATE', [self.id])
        self.invalidate_recordset()

    def _qing_spreadsheet_fingerprint(self):
        return hashlib.sha256(json.dumps([self.name, self.value, self.apply_count]).encode()).hexdigest()

    def _qing_spreadsheet_load(self):
        return {'version': '19.1.0', 'sheets': [{'id': 'record', 'name': 'Record',
                'cells': {'A1': self.name, 'B1': str(self.value)}}]}

    def _qing_spreadsheet_validate(self, data):
        cells = data['sheets'][0].get('cells', {})
        errors = []
        try:
            value = float(cells.get('B1', ''))
            if value < 0:
                raise ValueError()
        except (ValueError, TypeError):
            errors.append({'sheet': 'Record', 'cell': 'B1', 'message': 'Enter a non-negative number.'})
        return {'errors': errors, 'changes': [{'label': 'Value', 'before': self.value, 'after': cells.get('B1', '')}]}

    def _qing_spreadsheet_apply(self, data):
        self.write({'value': float(data['sheets'][0]['cells']['B1']), 'apply_count': self.apply_count + 1})
        if self.env.context.get('test_fail_after_write'):
            raise ValidationError('Simulated business failure after write')

    apply_count = fields.Integer()
    company_id = fields.Many2one('res.company', default=lambda self: self.env.company)
    name = fields.Char(required=True)
    owner_id = fields.Many2one('res.users', default=lambda self: self.env.user)
    value = fields.Float()

    def write(self, values):
        for record in self.sorted('id'):
            record._qing_spreadsheet_lock()
        return super().write(values)
