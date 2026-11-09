from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

from odoo import Command
from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged('post_install', '-at_install')
class TestTaxClassificationSync(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.manager = new_test_user(
            cls.env,
            login='tax_catalog_manager',
            groups='account.group_account_manager',
            company_id=cls.env.company.id,
            company_ids=[Command.set(cls.env.company.ids)],
        )
        cls.billing = new_test_user(
            cls.env,
            login='tax_catalog_billing',
            groups='account.group_account_invoice',
            company_id=cls.env.company.id,
            company_ids=[Command.set(cls.env.company.ids)],
        )

    def _items(self, count):
        return [{
            'code': str(9999999999999900000 + index),
            'name': 'Catalog item %s' % index,
            'short_name': 'Item %s' % index,
            'active': True,
            'invoicing_allowed': True,
        } for index in range(count)]

    def test_sync_fetches_all_pages_then_upserts_and_preserves_local_notes(self):
        items = self._items(101)
        Catalog = self.env['qing.fapiao.tax.classification'].with_user(self.manager)
        existing = Catalog.create({
            'code': items[0]['code'],
            'name': 'Previous name',
            'note': 'Local note',
        })
        instance = self.env['qing.service.instance'].sudo()._local_instance()

        def request(_instance, path, payload=None):
            self.assertIsNone(payload)
            query = parse_qs(urlsplit(path).query)
            after = query.get('after', [''])[0]
            start = next((index + 1 for index, item in enumerate(items) if item['code'] == after), 0)
            page_items = items[start:start + 100]
            next_after = page_items[-1]['code'] if start + len(page_items) < len(items) else ''
            return {
                'catalog_version': '20260604',
                'total': len(items),
                'items': page_items,
                'next_after': next_after,
            }

        with patch.object(type(instance), '_request', autospec=True, side_effect=request) as service_request:
            action = Catalog.action_sync_online_catalog()

        self.assertEqual(service_request.call_count, 2)
        self.assertEqual(action['tag'], 'display_notification')
        expected_message = Catalog.env._(
            'Catalog %s: created %s, updated %s, unchanged %s.',
            '20260604', 100, 1, 0,
        )
        self.assertEqual(action['params']['message'], expected_message)
        self.assertEqual(existing.name, items[0]['name'])
        self.assertEqual(existing.short_name, items[0]['short_name'])
        self.assertEqual(existing.note, 'Local note')
        self.assertEqual(Catalog.search_count([('code', 'in', [item['code'] for item in items])]), 101)

    def test_sync_rejects_invalid_remote_catalog_before_writing(self):
        Catalog = self.env['qing.fapiao.tax.classification'].with_user(self.manager)
        instance = self.env['qing.service.instance'].sudo()._local_instance()
        with patch.object(type(instance), '_request', return_value={
            'catalog_version': '20260604',
            'total': 1,
            'items': [{
                'code': 'invalid', 'name': 'Invalid', 'short_name': '',
                'active': True, 'invoicing_allowed': True,
            }],
            'next_after': '',
        }):
            with self.assertRaises(UserError):
                Catalog.action_sync_online_catalog()
        self.assertFalse(Catalog.search([('code', '=', 'invalid')]))

    def test_only_accounting_managers_can_sync(self):
        with self.assertRaises(AccessError):
            self.env['qing.fapiao.tax.classification'].with_user(self.billing).action_sync_online_catalog()
