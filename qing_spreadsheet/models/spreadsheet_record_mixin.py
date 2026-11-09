from odoo import models, _
from odoo.exceptions import UserError

from ..services.workbook import INTERNAL_WRITE, encode


class QingSpreadsheetRecordMixin(models.AbstractModel):
    """Opt-in contract for business addons; never expose an arbitrary ORM writer."""

    _name = 'qing.spreadsheet.record.mixin'
    _description = 'Spreadsheet Business Record Adapter'
    _qing_spreadsheet_enabled = False

    def _qing_spreadsheet_load(self):
        """Return native workbook data. The adapter owns its stable row identities."""
        raise UserError(_('This model has no spreadsheet editor configured.'))

    def _qing_spreadsheet_fingerprint(self):
        """Override to include every related business row relevant to the editor."""
        raise UserError(_('The adapter must implement a complete source fingerprint.'))

    def _qing_spreadsheet_lock(self):
        """Lock all relevant rows; all adapter writers must use the same lock order."""
        raise UserError(_('The adapter must implement business record locking.'))

    def _qing_spreadsheet_validate(self, data):
        """Return {errors: [{sheet, cell, message}], changes: [{label, before, after}]} ."""
        raise UserError(_('This model has no spreadsheet validation configured.'))

    def _qing_spreadsheet_apply(self, data):
        """Apply already validated values using native ORM methods, without commits."""
        raise UserError(_('This model has no spreadsheet save adapter configured.'))

    def action_open_qing_spreadsheet(self):
        self.ensure_one()
        self.check_access('write')
        if not self._qing_spreadsheet_enabled:
            raise UserError(_('This model has no spreadsheet editor configured.'))
        company = self.company_id if 'company_id' in self._fields and self.company_id else self.env.company
        data = self._qing_spreadsheet_load()
        Book = self.env['qing.spreadsheet']
        domain = [('binding_model', '=', self._name), ('binding_res_id', '=', self.id),
                  ('owner_id', '=', self.env.uid), ('company_id', '=', company.id)]
        book = Book.search(domain, limit=1)
        if not book:
            book = Book.with_context(_qing_spreadsheet_internal=INTERNAL_WRITE).create({
                'name': self.display_name, 'company_id': company.id, 'binding_model': self._name,
                'binding_res_id': self.id, 'binding_version': str(self._qing_spreadsheet_fingerprint()),
                'spreadsheet_data': encode(data),
            })
            book._validated_data(data)
        return book.action_open()
