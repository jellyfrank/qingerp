import re
from urllib.parse import urlencode

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError


CATALOG_PAGE_SIZE = 100
TAX_CODE_PATTERN = re.compile(r'^[0-9]{19}$')


class FapiaoTaxClassification(models.Model):
    _name = 'qing.fapiao.tax.classification'
    _description = 'Tax Classification Code'
    _order = 'code'

    @api.model
    def _fetch_online_catalog(self, instance):
        """Fetch and validate every service page before changing the local catalog."""
        rows = []
        seen_codes = set()
        after = ''
        catalog_version = False
        total = False

        while True:
            query = {'limit': CATALOG_PAGE_SIZE}
            if after:
                query['after'] = after
            page = instance._request(
                '/api/v1/service/fapiao/tax-classifications?%s' % urlencode(query),
            )
            page_version = page.get('catalog_version')
            page_total = page.get('total')
            items = page.get('items')
            next_after = page.get('next_after')
            if (not isinstance(page_version, str) or not page_version
                    or isinstance(page_total, bool) or not isinstance(page_total, int) or page_total < 0
                    or not isinstance(items, list) or len(items) > CATALOG_PAGE_SIZE
                    or not isinstance(next_after, str)):
                raise UserError(self.env._('The online service returned an invalid tax classification page.'))
            if (catalog_version and page_version != catalog_version) or (total and page_total != total):
                raise UserError(self.env._('The tax classification catalog changed during synchronization. Retry the synchronization.'))
            catalog_version = page_version
            total = page_total
            if not items:
                if next_after:
                    raise UserError(self.env._('The online service returned an invalid tax classification page.'))
                break

            previous_code = after
            for item in items:
                if (not isinstance(item, dict)
                        or not isinstance(item.get('code'), str)
                        or not TAX_CODE_PATTERN.fullmatch(item['code'])
                        or not isinstance(item.get('name'), str) or not item['name'].strip()
                        or not isinstance(item.get('short_name'), str)
                        or not isinstance(item.get('active'), bool)
                        or not isinstance(item.get('invoicing_allowed'), bool)
                        or item['code'] in seen_codes or item['code'] <= previous_code):
                    raise UserError(self.env._('The online service returned invalid tax classification data.'))
                seen_codes.add(item['code'])
                previous_code = item['code']
                rows.append({
                    'active': item['active'],
                    'code': item['code'],
                    'invoicing_allowed': item['invoicing_allowed'],
                    'name': item['name'].strip(),
                    'short_name': item['short_name'].strip() or False,
                })

            if next_after:
                if next_after != items[-1]['code'] or next_after <= after or len(items) != CATALOG_PAGE_SIZE:
                    raise UserError(self.env._('The online service returned an invalid tax classification page.'))
                after = next_after
                continue
            break

        if len(rows) != total:
            raise UserError(self.env._('The online service returned an incomplete tax classification catalog.'))
        return catalog_version, rows

    @api.model
    def _merge_online_catalog(self, rows):
        """Upsert service-managed code fields while leaving local notes and extra codes intact."""
        domain = [('code', 'in', [row['code'] for row in rows])]
        existing_records = self.with_context(active_test=False).search(domain)
        existing_by_code = {record.code: record for record in existing_records}
        result = {'created': 0, 'updated': 0, 'unchanged': 0}
        pending = []
        managed_fields = ('active', 'invoicing_allowed', 'name', 'short_name')

        for row in rows:
            existing = existing_by_code.get(row['code'])
            if not existing:
                pending.append(row)
                result['created'] += 1
                if len(pending) == 200:
                    self.create(pending)
                    pending = []
                continue
            values = {name: row[name] for name in managed_fields}
            if any(existing[name] != value for name, value in values.items()):
                existing.write(values)
                result['updated'] += 1
                continue
            result['unchanged'] += 1

        if pending:
            self.create(pending)
        return result

    active = fields.Boolean(default=True)
    code = fields.Char(string='Tax Classification Code', required=True, index=True)
    name = fields.Char(string='Tax Classification Name', required=True, index=True)
    short_name = fields.Char(string='Invoice Short Name')
    invoicing_allowed = fields.Boolean(string='Allowed for Invoicing', default=True,
        help='Disable summary or obsolete codes that must not be used on an invoice line.')
    note = fields.Text(string='Notes')

    _code_unique = models.Constraint('UNIQUE(code)', 'The tax classification code must be unique.')

    @api.depends('code', 'name')
    def _compute_display_name(self):
        for record in self:
            record.display_name = '[%s] %s' % (record.code or '', record.name or '')

    @api.constrains('code')
    def _check_code(self):
        for record in self:
            if not re.fullmatch(r'[0-9]{19}', record.code or ''):
                raise ValidationError(self.env._('Tax classification codes must contain exactly 19 digits.'))

    @api.model_create_multi
    def create(self, vals_list):
        return super().create([
            dict(vals, code=(vals.get('code') or '').strip()) for vals in vals_list
        ])

    def write(self, vals):
        vals = dict(vals)
        if 'code' in vals:
            vals['code'] = (vals['code'] or '').strip()
        return super().write(vals)

    @api.model
    def action_sync_online_catalog(self):
        """Synchronize the tax code catalog for an instance with an active service subscription."""
        if not self.env.user.has_group('account.group_account_manager'):
            raise AccessError(self.env._('Only accounting managers can synchronize tax classification codes.'))

        instance = self.env['qing.service.instance'].sudo().search([], limit=1)
        if not instance:
            raise UserError(self.env._('Register this QingERP instance with Online Services before synchronizing.'))
        if not instance._ensure_not_clone():
            raise UserError(self.env._('This copied database received a new online service identity. Register it before synchronizing.'))

        catalog_version, rows = self._fetch_online_catalog(instance)
        result = self._merge_online_catalog(rows)
        message = self.env._(
            'Catalog %s: created %s, updated %s, unchanged %s.',
            catalog_version,
            result['created'],
            result['updated'],
            result['unchanged'],
        )
        return self._qing_notify(
            message,
            title=self.env._('Tax classification synchronization completed'),
            notification_type='success',
            next_action={'type': 'ir.actions.client', 'tag': 'reload'},
        )
