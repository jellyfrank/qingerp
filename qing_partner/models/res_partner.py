from copy import deepcopy
from uuid import uuid4

from odoo import Command, api, fields, models
from odoo.exceptions import AccessError, ValidationError
from odoo.fields import Domain

CONTACT_EDIT_FIELDS = frozenset({'email', 'phone', 'website', 'function', 'lang', 'comment'})
CONTACT_CREATE_FIELDS = CONTACT_EDIT_FIELDS | frozenset({
    'name', 'is_company', 'company_type', 'type', 'parent_id', 'company_id',
    'street', 'street2', 'city', 'zip', 'state_id', 'country_id', 'image_1920',
})


class ResPartner(models.Model):
    _inherit = 'res.partner'

    _qing_api_uid_unique = models.Constraint(
        'UNIQUE(qing_api_uid)',
        'A contact API identifier must be unique.',
    )

    @api.model
    def _qing_boolean_domain(self, domain, operator, value):
        # Odoo normalizes Boolean searches to in/not in before calling search().
        if operator in ('=', '!='):
            positive = bool(value) == (operator == '=')
        elif operator in ('in', 'not in'):
            values = set(value)
            if not values:
                return Domain.FALSE if operator == 'in' else Domain.TRUE
            if values == {False, True}:
                return Domain.TRUE if operator == 'in' else Domain.FALSE
            positive = (True in values) == (operator == 'in')
        else:
            raise ValidationError(self.env._('Use equality to filter business role flags.'))
        return domain if positive else ~domain

    @api.depends_context('uid')
    def _compute_qing_can_manage(self):
        for partner in self:
            partner.qing_can_manage = self._qing_is_manager()

    @api.model
    def _qing_is_manager(self):
        return self.env.user.has_group('qing_partner.group_partner_manager') or self.env.user.has_group('base.group_system')

    @api.model
    def _qing_access_domain(self):
        # Do not test env.su here: native record checks evaluate domains in sudo.
        user = self.env.user.sudo()
        if not user._is_internal() or self._qing_is_manager() or user.qing_partner_access_mode == 'shared':
            return Domain.TRUE
        role_domain = self.env['qing.partner.role']._qing_scope_domain()
        business = Domain('commercial_partner_id.qing_role_ids', 'any!', role_domain)
        internal = Domain('partner_share', '=', False)
        own = Domain('id', '=', user.partner_id.id)
        companies = Domain('id', 'in', user.company_ids.partner_id.ids)
        unclassified = (
            Domain('commercial_partner_id.create_uid', '=', user.id)
            & ~Domain('commercial_partner_id.qing_role_ids', 'any!', Domain.TRUE)
        )
        return business | internal | own | companies | unclassified

    @api.depends_context('uid', 'company', 'allowed_company_ids')
    def _compute_qing_accessible(self):
        domain = self._qing_access_domain() & Domain('id', 'in', self.ids)
        allowed = set(self.sudo().with_context(active_test=False).search(domain).ids)
        for partner in self:
            partner.qing_accessible = partner.id in allowed

    def _search_qing_accessible(self, operator, value):
        return self._qing_boolean_domain(self._qing_access_domain(), operator, value)

    @api.model
    def _qing_role_company(self):
        company_id = self.env.context.get('qing_role_company_id') or self.env.company.id
        if company_id not in self.env.companies.ids:
            raise AccessError(self.env._('Select a company enabled in your company selector.'))
        return self.env['res.company'].browse(company_id)

    @api.model
    def _qing_role_domain(self, role, enabled=False):
        domain = Domain('company_id', '=', self._qing_role_company().id) & Domain('role', '=', role)
        if enabled:
            domain &= Domain('state', '=', 'enabled')
        return Domain('commercial_partner_id.qing_role_ids', 'any!', domain)

    def _search_qing_is_customer(self, operator, value):
        return self._qing_boolean_domain(self._qing_role_domain('customer', enabled=True), operator, value)

    def _search_qing_is_supplier(self, operator, value):
        return self._qing_boolean_domain(self._qing_role_domain('supplier', enabled=True), operator, value)

    def _search_qing_customer_available(self, operator, value):
        domain = self._qing_role_domain('customer', enabled=True) if self._qing_role_company().qing_partner_enforce_roles else Domain.TRUE
        return self._qing_boolean_domain(domain, operator, value)

    def _search_qing_supplier_available(self, operator, value):
        domain = self._qing_role_domain('supplier', enabled=True) if self._qing_role_company().qing_partner_enforce_roles else Domain.TRUE
        return self._qing_boolean_domain(domain, operator, value)

    @api.depends('commercial_partner_id', 'commercial_partner_id.qing_role_ids.role', 'commercial_partner_id.qing_role_ids.state')
    @api.depends_context('company', 'qing_role_company_id', 'allowed_company_ids')
    def _compute_qing_roles(self):
        company = self._qing_role_company()
        domain = [('partner_id', 'in', self.commercial_partner_id.ids), ('company_id', '=', company.id)]
        roles = self.env['qing.partner.role'].sudo().search(domain)
        mapping = {}
        for role in roles:
            mapping.setdefault(role.partner_id.id, {})[role.role] = role.state
        for partner in self:
            states = mapping.get(partner.commercial_partner_id.id, {})
            partner.qing_is_customer = states.get('customer') == 'enabled'
            partner.qing_is_supplier = states.get('supplier') == 'enabled'
            partner.qing_customer_available = not company.qing_partner_enforce_roles or states.get('customer') == 'enabled'
            partner.qing_supplier_available = not company.qing_partner_enforce_roles or states.get('supplier') == 'enabled'

    def _qing_set_role_enabled(self, role, flag):
        if not self.env.su and not self._qing_is_manager():
            raise AccessError(self.env._('Only a partner manager can change business identities.'))
        self.check_access('write')
        company = self._qing_role_company()
        Role = self.env['qing.partner.role']
        for partner in self:
            if partner != partner.commercial_partner_id:
                raise ValidationError(self.env._('Set customer and supplier identities on the commercial entity.'))
            domain = [('partner_id', '=', partner.id), ('company_id', '=', company.id), ('role', '=', role)]
            record = Role.search(domain)
            if record:
                record.write({'state': 'enabled' if partner[flag] else 'disabled'})
            elif partner[flag]:
                Role.create({'partner_id': partner.id, 'company_id': company.id, 'role': role, 'state': 'enabled'})

    def _inverse_qing_is_customer(self):
        self._qing_set_role_enabled('customer', 'qing_is_customer')

    def _inverse_qing_is_supplier(self):
        self._qing_set_role_enabled('supplier', 'qing_is_supplier')

    def _qing_check_business_role(self, role, company):
        """Admission is company-specific and independent of rank and UI context."""
        if not company.sudo().qing_partner_enforce_roles:
            return
        self.check_access('read')
        partners = self.commercial_partner_id
        domain = [('partner_id', 'in', partners.ids), ('company_id', '=', company.id), ('role', '=', role), ('state', '=', 'enabled')]
        enabled = self.env['qing.partner.role'].sudo().search(domain).partner_id
        if partners - enabled:
            raise ValidationError(self.env._('An enabled %(role)s role is required in %(company)s.', role=role, company=company.display_name))

    @api.model
    def _qing_matches_contact_default(self, name, value, default):
        field = self._fields[name]
        if field.type in ('one2many', 'many2many'):
            # A command ending at the default value can still modify another record.
            for command in value or ():
                if isinstance(command, dict):
                    return False
                if isinstance(command, (tuple, list)) and command[0] not in (Command.LINK, Command.CLEAR, Command.SET):
                    return False
        return field.convert_to_cache(deepcopy(value), self) == field.convert_to_cache(deepcopy(default), self)

    def _qing_check_maintenance(self, values, creating=False):
        if 'qing_api_uid' in values and not self.env.su:
            raise AccessError(self.env._('The contact API identifier cannot be changed.'))
        if self.env.su or not self.env.user._is_internal() or self._qing_is_manager():
            return
        if {'user_id', 'buyer_id', 'qing_is_customer', 'qing_is_supplier'}.intersection(values):
            raise AccessError(self.env._('Only a partner manager can change business identities or responsible users.'))
        # Native permissions still govern user/company partners.
        if self and all(not partner.sudo().partner_share for partner in self):
            return
        if not self.env.user.has_group('qing_partner.group_partner_editor'):
            raise AccessError(self.env._('Partner maintenance permission is required.'))
        allowed = (CONTACT_CREATE_FIELDS | frozenset(self._address_fields())) if creating else CONTACT_EDIT_FIELDS
        if creating:
            defaults = {key[8:] for key in self.env.context if key.startswith('default_')}
            protected = defaults - allowed - {'customer_rank', 'supplier_rank'}
            if protected:
                raise AccessError(self.env._('Protected contact defaults require a partner manager.'))
        protected = set(values) - allowed
        native_defaults = set()
        if creating and protected and protected <= self._fields.keys():
            # Native forms also submit unchanged protected defaults at creation.
            defaults = self.default_get(list(protected))
            native_defaults = {name for name in protected if self._qing_matches_contact_default(name, values[name], defaults.get(name, False))}
            protected -= native_defaults
        if protected:
            raise AccessError(self.env._('Only a partner manager can change shared master data, hierarchy or business roles.'))
        if creating and values.get('parent_id'):
            self.browse(values['parent_id']).check_access('read')
        return native_defaults

    qing_accessible = fields.Boolean(compute='_compute_qing_accessible', search='_search_qing_accessible', compute_sudo=False)
    qing_api_uid = fields.Char(copy=False, index=True, readonly=True)
    qing_can_manage = fields.Boolean(compute='_compute_qing_can_manage')
    qing_customer_available = fields.Boolean(compute='_compute_qing_roles', search='_search_qing_customer_available')
    qing_is_customer = fields.Boolean(string='Customer', compute='_compute_qing_roles', inverse='_inverse_qing_is_customer', search='_search_qing_is_customer')
    qing_is_supplier = fields.Boolean(string='Supplier', compute='_compute_qing_roles', inverse='_inverse_qing_is_supplier', search='_search_qing_is_supplier')
    qing_role_ids = fields.One2many('qing.partner.role', 'partner_id', string='Business Roles', copy=False)
    qing_supplier_available = fields.Boolean(compute='_compute_qing_roles', search='_search_qing_supplier_available')

    @api.model_create_multi
    def create(self, vals_list):
        prepared_values = []
        for values in vals_list:
            native_defaults = self._qing_check_maintenance(values, creating=True) or ()
            # Let native defaults initialize without unnecessary inverse writes.
            values = {name: value for name, value in values.items() if name not in native_defaults}
            # Generate here, not in defaults forwarded by delegated user creation.
            values.setdefault('qing_api_uid', uuid4().hex)
            prepared_values.append(values)
        return super().create(prepared_values)

    def write(self, values):
        self._qing_check_maintenance(values)
        if {'parent_id', 'company_id', 'is_company'}.intersection(values):
            domain = [('partner_id', 'child_of', self.ids)]
            roles = self.env['qing.partner.role'].sudo().search(domain)
            result = super().write(values)
            roles._check_assignment()
            self.env.invalidate_all()
            return result
        result = super().write(values)
        if {'user_id', 'buyer_id'}.intersection(values):
            self.env.invalidate_all()
        return result

    def unlink(self):
        if not self.env.su and self.env.user._is_internal() and not self._qing_is_manager():
            raise AccessError(self.env._('Only a partner manager can delete contacts.'))
        return super().unlink()
