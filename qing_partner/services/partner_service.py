from odoo.exceptions import AccessError
from odoo.fields import Domain

from odoo.addons.qing_api.api import ApiError


class PartnerService:
    def __init__(self, env):
        self.env = env
        self.Partners = env['res.partner']

    def list(self, query):
        """Return a paginated list within the current user's contact access scope."""
        self._check_user()
        domain = Domain.TRUE
        search = (query.get('search') or '').strip()
        if search:
            domain &= (
                Domain('name', 'ilike', search)
                | Domain('email', 'ilike', search)
                | Domain('phone', 'ilike', search)
            )

        total = self.Partners.search_count(domain)
        partners = self.Partners.search(
            domain,
            offset=query['offset'],
            limit=query['limit'],
            order='name, id',
        )
        return {
            'items': [self._serialize(partner) for partner in partners],
            'limit': query['limit'],
            'offset': query['offset'],
            'total': total,
        }

    def get(self, partner_id):
        """Read one visible contact by its public API identifier."""
        self._check_user()
        return self._serialize(self._get_partner(partner_id))

    def create(self, data):
        """Create a contact through the current user's ORM permissions."""
        self._check_user()
        values = self._prepare_values(data)
        partner = self.Partners.create(values)
        return self._serialize(partner)

    def update(self, partner_id, data):
        """Update a visible contact through the current user's ORM permissions."""
        self._check_user()
        partner = self._get_partner(partner_id)
        values = self._prepare_values(data, partner=partner)
        if not values:
            raise ApiError('VALIDATION_ERROR', 'At least one field must be provided.', 422)
        partner.write(values)
        return self._serialize(partner)

    def delete(self, partner_id):
        """Delete a visible contact when record and role permissions allow it."""
        self._check_user()
        partner = self._get_partner(partner_id)
        public_id = self._public_id(partner)
        partner.unlink()
        return {'id': public_id, 'deleted': True}

    def _check_user(self):
        if not self.env.user._is_internal():
            raise AccessError(self.env._('An internal integration user is required.'))

    def _get_partner(self, partner_id):
        """Resolve an opaque contact identifier without bypassing record rules."""
        uid = partner_id.removeprefix('par_')
        partner = self.Partners.search([('qing_api_uid', '=', uid)], limit=1)
        if not partner:
            raise ApiError('RESOURCE_NOT_FOUND', 'Contact not found.', 404)
        return partner

    def _prepare_values(self, data, partner=None):
        """Map the stable API field names to permitted Odoo contact fields."""
        Country = self.env['res.country']
        CountryState = self.env['res.country.state']
        values = {
            name: value
            for name, value in data.items()
            if name not in {'country_code', 'state_code'}
        }
        if 'name' in values:
            values['name'] = values['name'].strip()
            if not values['name']:
                raise ApiError('VALIDATION_ERROR', 'Name cannot be empty.', 422)

        if 'country_code' in data:
            country_code = (data['country_code'] or '').strip().upper()
            country = Country.search(
                [('code', '=', country_code)],
                limit=1,
            ) if country_code else Country
            if country_code and not country:
                self._invalid_field('country_code', 'Country code is not recognized.')
            values['country_id'] = country.id or False
            if partner and 'state_code' not in data and partner.state_id.country_id != country:
                values['state_id'] = False

        if 'state_code' in data:
            state_code = (data['state_code'] or '').strip().upper()
            if 'country_id' in values:
                country = Country.browse(values['country_id'])
            elif partner:
                country = partner.country_id
            else:
                country = Country
            state = CountryState
            if state_code:
                if not country:
                    self._invalid_field('state_code', 'A country_code is required with state_code.')
                state = CountryState.search(
                    [('country_id', '=', country.id), ('code', '=', state_code)],
                    limit=1,
                )
                if not state:
                    self._invalid_field(
                        'state_code',
                        'State code is not recognized for the selected country.',
                    )
            values['state_id'] = state.id or False
        return values

    @staticmethod
    def _invalid_field(field, message):
        raise ApiError(
            'VALIDATION_ERROR',
            message,
            422,
            [{'field': 'body.%s' % field, 'code': 'INVALID_VALUE', 'message': message}],
        )

    @staticmethod
    def _public_id(partner):
        return 'par_%s' % partner.qing_api_uid

    def _serialize(self, partner):
        return {
            'id': self._public_id(partner),
            'name': partner.name,
            'is_company': bool(partner.is_company),
            'email': partner.email or None,
            'phone': partner.phone or None,
            'mobile': partner.mobile or None,
            'website': partner.website or None,
            'vat': partner.vat or None,
            'street': partner.street or None,
            'street2': partner.street2 or None,
            'zip': partner.zip or None,
            'city': partner.city or None,
            'country_code': partner.country_id.code or None,
            'state_code': partner.state_id.code or None,
        }
