from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError

from .balance_sheet_template import CN_PREFIX_ROWS, ROW_SELECTION, TEMPLATE_VERSION


class BalanceSheetMapping(models.Model):
    _name = 'qing.balance.sheet.mapping'
    _description = 'Balance Sheet Mapping Version'
    _check_company_auto = True
    _order = 'id desc'

    def _check_draft(self):
        self.check_access('write')
        self.lock_for_update()
        self.invalidate_recordset(['state'])
        if any(record.state != 'draft' for record in self):
            raise UserError(self.env._('Confirmed mappings are immutable. Duplicate the version to make changes.'))

    @api.constrains('company_id', 'line_ids')
    def _check_accounts(self):
        for record in self:
            if any(record.company_id not in line.account_id.company_ids for line in record.line_ids):
                raise ValidationError(self.env._('All mapped accounts must belong to the mapping company.'))

    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company)
    line_ids = fields.One2many('qing.balance.sheet.mapping.line', 'mapping_id', copy=True, string='Account Mappings')
    name = fields.Char(required=True, default='ASSE 2011 / 1')
    state = fields.Selection([('draft', 'Draft'), ('confirmed', 'Confirmed')], default='draft', required=True, copy=False)
    template_version = fields.Char(default=TEMPLATE_VERSION, readonly=True, required=True, copy=False)

    @api.model_create_multi
    def create(self, values_list):
        if any(values.get('state', 'draft') != 'draft' or
               values.get('template_version', TEMPLATE_VERSION) != TEMPLATE_VERSION for values in values_list):
            raise ValidationError(self.env._('Create a draft using the current template version.'))
        return super().create(values_list)

    def write(self, values):
        self._check_draft()
        if 'template_version' in values or 'state' in values:
            raise UserError(self.env._('Use Confirm Mapping to freeze this version.'))
        return super().write(values)

    def unlink(self):
        self._check_draft()
        return super().unlink()

    def action_confirm(self):
        self._check_draft()
        if any(not record.line_ids for record in self):
            raise UserError(self.env._('Add account mappings before confirming.'))
        self._check_accounts()
        return super().write({'state': 'confirmed'})

    def action_suggest_cn(self):
        """Add missing mappings for original native cn accounts; never replace edits."""
        self.ensure_one()
        self._check_draft()
        if self.company_id != self.env.company or self.company_id.chart_template != 'cn':
            raise UserError(self.env._('Suggestions require the current company to use the native Chinese small-business chart (cn).'))
        chart = self.env['account.chart.template'].with_company(self.company_id)
        data = chart._get_chart_template_model_data('cn', 'account.account')
        mapped = set(self.line_ids.account_id.ids)
        values = []
        for xmlid, entry in data.items():
            code = CN_PREFIX_ROWS.get(str(entry.get('code', ''))[:4])
            if not code:
                continue
            account = chart.ref(xmlid, raise_if_not_found=False)
            if not account or account.id in mapped or self.company_id not in account.company_ids:
                continue
            account.check_access('read')
            values.append({'mapping_id': self.id, 'account_id': account.id, 'line_code': str(code)})
            mapped.add(account.id)
        self.env['qing.balance.sheet.mapping.line'].create(values)

    def action_duplicate_version(self):
        self.ensure_one()
        copy = self.copy({'name': self.env._('%s (revision)', self.name)})
        return {'type': 'ir.actions.act_window', 'res_model': self._name,
                'res_id': copy.id, 'view_mode': 'form'}


class BalanceSheetMappingLine(models.Model):
    _name = 'qing.balance.sheet.mapping.line'
    _description = 'Balance Sheet Account Mapping'
    _check_company_auto = True
    _order = 'account_id, id'

    _unique_account = models.Constraint('UNIQUE(mapping_id, account_id)', 'An account can only be mapped once per version.')

    @api.constrains('account_id', 'mapping_id')
    def _check_account_company(self):
        self.mapping_id._check_accounts()

    account_id = fields.Many2one('account.account', required=True, check_company=True, ondelete='restrict')
    company_id = fields.Many2one(related='mapping_id.company_id', store=True)
    line_code = fields.Selection(ROW_SELECTION, required=True, string='Statement Row')
    mapping_id = fields.Many2one('qing.balance.sheet.mapping', required=True, ondelete='cascade')

    @api.model_create_multi
    def create(self, values_list):
        self.env['qing.balance.sheet.mapping'].browse([values['mapping_id'] for values in values_list])._check_draft()
        return super().create(values_list)

    def write(self, values):
        profiles = self.mapping_id
        if values.get('mapping_id'):
            profiles |= self.env['qing.balance.sheet.mapping'].browse(values['mapping_id'])
        profiles._check_draft()
        return super().write(values)

    def unlink(self):
        self.mapping_id._check_draft()
        return super().unlink()
