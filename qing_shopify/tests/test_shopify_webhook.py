import base64
import hmac
import json
import os
from unittest.mock import patch

from cryptography.fernet import Fernet

from odoo.tests import HttpCase, tagged


@tagged('post_install', '-at_install')
class TestShopifyWebhook(HttpCase):
    def test_orders_edited_webhooks_bind_to_the_order_and_keep_other_topics(self):
        self.enterContext(patch.dict(os.environ, {'QING_SHOPIFY_CREDENTIAL_KEY': Fernet.generate_key().decode()}))
        secret = 'Shopify order edit test key'
        warehouse = self.env['stock.warehouse'].search([('company_id', '=', self.env.company.id)], limit=1)
        store = self.env['qing.channel.store'].create({
            'name': 'Shopify webhook test', 'platform': 'shopify', 'shopify_domain': 'webhook-sample.myshopify.com',
            'warehouse_id': warehouse.id, 'shopify_secret': secret,
        })
        order_id = 820982911946154508
        remote_id = f'gid://shopify/Order/{order_id}'
        edit = {
            'order_edit': {
                'id': 78912328793123782, 'order_id': order_id,
                'line_items': {'additions': [], 'removals': []},
                'shipping_address': {'changed': True},
            },
        }
        for version, delivery in (('2026-10', 'address-edit'), ('2027-01', 'candidate-address-edit')):
            with self.subTest(version=version):
                response = self._send_shopify_webhook(store, secret, 'orders/edited', delivery, edit, version)
                self.assertEqual(response.status_code, 200)
        response = self._send_shopify_webhook(store, secret, 'orders/edited', 'address-edit', edit, '2026-10')
        self.assertEqual(response.status_code, 200)

        update = {'admin_graphql_api_id': remote_id}
        response = self._send_shopify_webhook(store, secret, 'orders/updated', 'same-action-update', update, '2026-10')
        self.assertEqual(response.status_code, 200)

        incomplete = {'id': order_id, 'order_edit': {'id': edit['order_edit']['id']}}
        response = self._send_shopify_webhook(store, secret, 'orders/edited', 'missing-order-id', incomplete, '2026-10')
        self.assertEqual(response.status_code, 400)

        deliveries = self.env['qing.channel.delivery'].search([('store_id', '=', store.id)])
        self.assertEqual(set(deliveries.mapped('identity')), {
            'webhook:address-edit', 'webhook:candidate-address-edit', 'webhook:same-action-update',
        })
        self.assertEqual(set(deliveries.mapped('external_id')), {remote_id})
        self.assertEqual(set(deliveries.mapped('topic')), {'orders/edited', 'orders/updated'})
        self.assertEqual(set(deliveries.mapped('state')), {'pending'})

    def _send_shopify_webhook(self, store, secret, topic, delivery, payload, version):
        body = json.dumps(payload).encode()
        signature = base64.b64encode(hmac.digest(secret.encode(), body, 'sha256')).decode()
        return self.url_open(f'/qing-shopify/webhook/{store.shopify_webhook_key}', data=body, headers={
            'Content-Type': 'application/json',
            'X-Shopify-Shop-Domain': store.shopify_domain,
            'X-Shopify-Hmac-Sha256': signature,
            'X-Shopify-Topic': topic,
            'X-Shopify-Webhook-Id': delivery,
            'X-Shopify-Event-Id': 'same-merchant-action',
            'X-Shopify-API-Version': version,
        })
