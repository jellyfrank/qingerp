from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError

from .profit_statement_template import CN_CLOSING_XMLIDS, CN_XMLID_ROWS, ROW_SELECTION, TEMPLATE_VERSION


class ProfitStatementMapping(models.Model):
    _name = 'qing.profit.statement.mapping'
    _description = 'Profit Statement Mapping Version'
    _check_company_auto = True
    _order = 'id desc'

    def _check_draft(self):
        self.check_access('write')
        self.lock_for_update()
        self.invalidate_recordset(['state'])
        if any(record.state != 'draft' for record in self):
            raise UserError(self.env._(
                'Confirmed profit mappings are immutable. Duplicate the version to make changes.'
            ))

    @api.constrains('closing_account_ids', 'company_id', 'line_ids')
    def _check_accounts(self):
        for record in self:
            accounts = record.line_ids.account_id | record.closing_account_ids
            if any(record.company_id not in account.company_ids for account in accounts):
                raise ValidationError(self.env._(
                    'All profit and closing accounts must belong to the mapping company.'
                ))
            if record.line_ids.account_id & record.closing_account_ids:
                raise ValidationError(self.env._('A closing account cannot also be a profit statement account.'))

    closing_account_ids = fields.Many2many(
        'account.account', 'qing_profit_mapping_closing_account_rel', 'mapping_id', 'account_id',
        check_company=True, string='Earnings Closing Accounts',
    )
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company)
    line_ids = fields.One2many(
        'qing.profit.statement.mapping.line', 'mapping_id', copy=True, string='Account Mappings',
    )
    name = fields.Char(required=True, default='ASSE Profit 2011 / 1')
    state = fields.Selection(
        [('draft', 'Draft'), ('confirmed', 'Confirmed')], default='draft', required=True, copy=False,
    )
    template_version = fields.Char(default=TEMPLATE_VERSION, readonly=True, required=True, copy=False)

    @api.model_create_multi
    def create(self, values_list):
        if any(
            values.get('state', 'draft') != 'draft'
            or values.get('template_version', TEMPLATE_VERSION) != TEMPLATE_VERSION
            for values in values_list
        ):
            raise ValidationError(self.env._('Create a draft using the current profit statement template version.'))
        return super().create(values_list)

    def write(self, values):
        self._check_draft()
        if 'template_version' in values or 'state' in values:
            raise UserError(self.env._('Use Confirm Mapping to freeze this profit statement version.'))
        return super().write(values)

    def unlink(self):
        self._check_draft()
        return super().unlink()

    def action_confirm(self):
        self._check_draft()
        if any(not record.line_ids for record in self):
            raise UserError(self.env._('Add profit statement account mappings before confirming.'))
        if any(not record.closing_account_ids for record in self):
            raise UserError(self.env._('Select at least one earnings closing account before confirming.'))
        self._check_accounts()
        return super().write({'state': 'confirmed'})

    def action_suggest_cn(self):
        """Add original native cn mappings and closing accounts; never replace edits."""
        self.ensure_one()
        self._check_draft()
        if self.company_id != self.env.company or self.company_id.chart_template != 'cn':
            raise UserError(self.env._(
                'Suggestions require the current company to use the native Chinese small-business chart (cn).'
            ))
        chart = self.env['account.chart.template'].with_company(self.company_id)
        data = chart._get_chart_template_model_data('cn', 'account.account')
        mapped = set(self.line_ids.account_id.ids)
        values = []
        for xmlid, code in CN_XMLID_ROWS.items():
            if xmlid not in data:
                continue
            account = chart.ref(xmlid, raise_if_not_found=False)
            if not account or account.id in mapped or self.company_id not in account.company_ids:
                continue
            account.check_access('read')
            values.append({'mapping_id': self.id, 'account_id': account.id, 'line_code': str(code)})
            mapped.add(account.id)
        self.env['qing.profit.statement.mapping.line'].create(values)
        closing_accounts = self.env['account.account']
        for xmlid in CN_CLOSING_XMLIDS:
            if xmlid in data:
                account = chart.ref(xmlid, raise_if_not_found=False)
                if account and self.company_id in account.company_ids:
                    account.check_access('read')
                    closing_accounts |= account
        if closing_accounts:
            self.closing_account_ids |= closing_accounts

    def action_duplicate_version(self):
        self.ensure_one()
        copy = self.copy({'name': self.env._('%s (revision)', self.name)})
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': copy.id,
            'view_mode': 'form',
        }


class ProfitStatementMappingLine(models.Model):
    _name = 'qing.profit.statement.mapping.line'
    _description = 'Profit Statement Account Mapping'
    _check_company_auto = True
    _order = 'account_id, id'

    _unique_account = models.Constraint(
        'UNIQUE(mapping_id, account_id)', 'An account can only be mapped once per profit statement version.'
    )

    @api.constrains('account_id', 'mapping_id')
    def _check_account_company(self):
        self.mapping_id._check_accounts()

    account_id = fields.Many2one('account.account', required=True, check_company=True, ondelete='restrict')
    company_id = fields.Many2one(related='mapping_id.company_id', store=True)
    line_code = fields.Selection(ROW_SELECTION, required=True, string='Statement Row')
    mapping_id = fields.Many2one('qing.profit.statement.mapping', required=True, ondelete='cascade')

    @api.model_create_multi
    def create(self, values_list):
        mappings = self.env['qing.profit.statement.mapping'].browse(
            [values['mapping_id'] for values in values_list]
        )
        mappings._check_draft()
        return super().create(values_list)

    def write(self, values):
        mappings = self.mapping_id
        if values.get('mapping_id'):
            mappings |= self.env['qing.profit.statement.mapping'].browse(values['mapping_id'])
        mappings._check_draft()
        return super().write(values)

    def unlink(self):
        self.mapping_id._check_draft()
        return super().unlink()
