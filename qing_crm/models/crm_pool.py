from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from .constants import CUSTOMER_GRADES, LIFECYCLE_STAGES


class CrmPool(models.Model):
    _name = 'qing.crm.pool'
    _description = 'CRM Customer Pool'
    _check_company_auto = True
    _order = 'company_id, name'

    _name_company_unique = models.Constraint(
        'UNIQUE(name, company_id)', 'A pool with this name already exists in the company.')
    _one_default_per_company = models.Constraint(
        'UNIQUE(default_company_id)', 'Only one default pool is allowed per company.')

    active = fields.Boolean(default=True)
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company, index=True)
    default_company_id = fields.Many2one('res.company', compute='_compute_default_company_id', store=True, precompute=True)
    is_default = fields.Boolean(default=False)
    name = fields.Char(required=True, translate=True)

    @api.model
    def _allow_multiple_pools(self):
        return False

    def _check_claim_policy(self, user):
        return True

    @api.model_create_multi
    def create(self, vals_list):
        if not self._allow_multiple_pools():
            checked_company_ids = set()
            for values in vals_list:
                company_id = values.get('company_id') or self.env.company.id
                if company_id in checked_company_ids:
                    raise ValidationError(self.env._('The free edition supports one customer pool per company.'))
                checked_company_ids.add(company_id)
                self.env.cr.execute('SELECT pg_advisory_xact_lock(%s, %s)', [1363362381, company_id])
                if self.search_count([('company_id', '=', company_id)], limit=1):
                    raise ValidationError(self.env._('The free edition supports one customer pool per company.'))
        return super().create(vals_list)

    @api.depends('company_id', 'is_default')
    def _compute_default_company_id(self):
        for pool in self:
            pool.default_company_id = pool.company_id if pool.is_default else False

    @api.model
    def _get_default_pool(self, company):
        pool = self.sudo().search([('company_id', '=', company.id), ('is_default', '=', True)], limit=1)
        if pool:
            return pool
        pool = self.sudo().search([('company_id', '=', company.id)], limit=1)
        if pool:
            pool.write({'is_default': True})
            return pool
        return self.sudo().create({
            'company_id': company.id,
            'is_default': True,
            'name': self.env._('Public Pool'),
        })


class CrmPoolEntry(models.Model):
    _name = 'qing.crm.pool.entry'
    _description = 'CRM Public Pool Entry'
    _check_company_auto = True
    _order = 'available_since desc, id desc'

    _role_unique = models.Constraint('UNIQUE(role_id)', 'A customer can only have one pool entry.')

    active = fields.Boolean(default=True, index=True)
    available_since = fields.Datetime(required=True, default=fields.Datetime.now, index=True)
    city = fields.Char(readonly=True)
    company_id = fields.Many2one('res.company', required=True, index=True, readonly=True)
    country_id = fields.Many2one('res.country', readonly=True)
    customer_grade = fields.Selection(CUSTOMER_GRADES, readonly=True)
    lifecycle_stage = fields.Selection(LIFECYCLE_STAGES, readonly=True)
    name = fields.Char(required=True, readonly=True)
    pool_id = fields.Many2one('qing.crm.pool', required=True, readonly=True, check_company=True)
    role_id = fields.Many2one(
        'qing.partner.role', required=True, ondelete='cascade', readonly=True,
        groups='sales_team.group_sale_manager')

    def action_claim(self):
        self.ensure_one()
        user = self.env.user
        if not user._is_internal() or not user.has_group('sales_team.group_sale_salesman'):
            raise AccessError(self.env._('Only internal sales users can claim public customers.'))
        if self.company_id not in self.env.companies:
            raise AccessError(self.env._('The customer belongs to a company outside your active companies.'))
        self.env.cr.execute('SELECT id FROM qing_crm_pool_entry WHERE id = %s FOR UPDATE', [self.id])
        self.invalidate_recordset(['active'])
        if not self.active:
            raise UserError(self.env._('This customer has already been claimed. Refresh the list.'))
        role = self.sudo().role_id
        if role.pool_state != 'public' or not role.crm_managed:
            raise UserError(self.env._('This customer is no longer available in the public pool.'))
        self.pool_id._check_claim_policy(user)
        role.with_context(qing_crm_service=True)._crm_assign(user, self.env._('Claimed from public pool'))
        return role.action_open_customer()

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            raise AccessError(self.env._('Pool entries are maintained by the CRM service.'))
        return super().create(vals_list)

    def write(self, values):
        if not self.env.su:
            raise AccessError(self.env._('Pool entries are maintained by the CRM service.'))
        return super().write(values)

    def unlink(self):
        if not self.env.su:
            raise ValidationError(self.env._('Pool entries are retained for audit. Archive them instead.'))
        return super().unlink()
