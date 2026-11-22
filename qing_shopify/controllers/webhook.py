import json
import re

from odoo import http
from odoo.http import request
from odoo.addons.qing_channel.errors import ChannelReviewRequired
from odoo.addons.qing_base.tools.channel_queue import verify_base64_hmac_sha256

from ..services.catalog import PRODUCT_ID, WEBHOOK_PRODUCT_TOPICS
from ..services.orders import parse_date


ORDER_TOPICS = {
    'orders/create', 'orders/updated', 'orders/edited', 'orders/cancelled', 'orders/paid',
    'refunds/create', 'order_transactions/create', 'fulfillments/create', 'fulfillments/update',
}
MAX_BYTES = 1024 * 1024
ORDER_ID = re.compile(r'gid://shopify/Order/[0-9]+\Z')


class ShopifyWebhook(http.Controller):
    @http.route('/qing-shopify/webhook/<string:key>', type='http', auth='public', methods=['POST'],
                csrf=False, save_session=False)
    def webhook(self, key):
        req = request.httprequest
        domain = [('active', '=', True), ('platform', '=', 'shopify'), ('shopify_webhook_key', '=', key),
                  ('shopify_domain', '=', req.headers.get('X-Shopify-Shop-Domain', ''))]
        store = request.env['qing.channel.store'].sudo().search(domain, limit=1)
        if not store:
            return request.make_response('', status=404)
        if req.content_length and req.content_length > MAX_BYTES:
            return request.make_response('', status=413)
        req.max_content_length = MAX_BYTES
        raw = req.get_data()
        if len(raw) > MAX_BYTES:
            return request.make_response('', status=413)
        signature = req.headers.get('X-Shopify-Hmac-Sha256')
        try:
            secret = store._shopify_decrypt('shopify_secret')
        except ChannelReviewRequired:
            return request.make_response('', status=503)
        if not verify_base64_hmac_sha256(raw, signature, secret):
            return request.make_response('', status=403)
        topic = req.headers.get('X-Shopify-Topic', '')
        webhook_id = req.headers.get('X-Shopify-Webhook-Id', '')
        event_id = req.headers.get('X-Shopify-Event-Id', '')
        if not webhook_id or len(webhook_id) > 100 or len(event_id) > 100:
            return request.make_response('', status=400)
        if topic == 'app/uninstalled':
            try:
                occurred_at = parse_date(req.headers.get('X-Shopify-Triggered-At'))
            except (ValueError, OverflowError):
                occurred_at = False
            delivery = request.env['qing.channel.delivery'].sudo()._receive(
                store, 'webhook:' + webhook_id, store.shopify_domain, topic, event_id, occurred_at=occurred_at)
            if delivery and delivery.topic == topic and delivery.state != 'done':
                store._import_delivery(delivery)
                delivery.state = 'done'
            return request.make_response('', status=200)
        if topic not in ORDER_TOPICS and topic not in WEBHOOK_PRODUCT_TOPICS:
            return request.make_response('', status=200)
        try:
            payload = json.loads(raw)
            if topic in WEBHOOK_PRODUCT_TOPICS:
                external_id = payload.get('admin_graphql_api_id') or f"gid://shopify/Product/{payload.get('id')}"
                expected_id = PRODUCT_ID
            elif topic == 'orders/edited':
                number = payload.get('order_edit', {}).get('order_id')
                external_id = f'gid://shopify/Order/{number}'
            elif topic.startswith('orders/'):
                external_id = payload.get('admin_graphql_api_id') or f"gid://shopify/Order/{payload.get('id')}"
            else:
                external_id = f"gid://shopify/Order/{payload.get('order_id')}"
            if topic not in WEBHOOK_PRODUCT_TOPICS:
                expected_id = ORDER_ID
            if not isinstance(external_id, str) or not expected_id.fullmatch(external_id):
                raise ValueError()
        except (ValueError, AttributeError):
            return request.make_response('', status=400)
        request.env['qing.channel.delivery'].sudo()._receive(store, 'webhook:' + webhook_id, external_id, topic, event_id)
        return request.make_response('', status=200)
