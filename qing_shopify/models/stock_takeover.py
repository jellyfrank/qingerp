from odoo import fields, models
from odoo.addons.qing_channel.errors import ChannelReviewRequired, DeferredChannelError

from ..services.catalog import fetch_product
from .store_stock import INVENTORY_COMPARE_REJECTED, INVENTORY_SET


class ShopifyStockTakeover(models.Model):
    _inherit = 'qing.channel.store'

    def _check_shopify_takeover(self, binding):
        if not self.onboarded or self.shopify_health != 'connected' or not binding.shopify_takeover_pending:
            raise ChannelReviewRequired('shopify_stock_takeover_unavailable')
        if self._shopify_pending_imports():
            raise DeferredChannelError('shopify_inventory_waiting_for_order_import')
        bindings = self.env['qing.channel.binding'].search([
            ('store_id', '=', self.id), ('kind', '=', 'variant'), ('product_id', '=', binding.product_id.id),
            ('shopify_catalog_item_id.state', '=', 'linked'), ('shopify_catalog_withdrawn', '=', False),
        ], limit=2)
        if len(bindings) != 1:
            raise ChannelReviewRequired('shopify_stock_catalog_item_required')
        catalog_item = bindings.shopify_catalog_item_id
        current = fetch_product(self._shopify_client(), catalog_item.external_id)
        if not current or current['status'] == 'ARCHIVED':
            raise ChannelReviewRequired('shopify_stock_catalog_item_required')
        variant = next((variant for variant in current['variants'] if variant['id'] == bindings.external_id), None)
        inventory = (variant or {}).get('inventoryItem') or {}
        if inventory.get('id') != binding.external_id:
            raise ChannelReviewRequired('shopify_stock_takeover_mapping_changed')
        if not inventory.get('tracked') or variant['inventoryPolicy'] != 'DENY':
            raise ChannelReviewRequired('shopify_stock_tracking_and_deny_required')
        return self._shopify_inventory(binding), *self._shopify_stock_values(binding)

    def _execute_channel_action(self, action):
        if action.kind != 'shopify_stock_takeover':
            return super()._execute_channel_action(action)
        data = action.data
        binding = self._lock_inventory_dispatch(data)
        if not self._inventory_request_has_current_mapping(binding, data) or not binding.shopify_takeover_pending:
            return 'obsolete'
        self.env['qing.channel.request']._prepare_request(
            action, 'shopify_stock_takeover', f'stock:{binding.id}', data,
        )
        return 'external'

    def _execute_channel_request(self, request, recovering):
        if request.operation != 'shopify_stock_takeover':
            return super()._execute_channel_request(request, recovering)
        data = request.payload
        binding = self._lock_inventory_dispatch(data)
        if not self._inventory_request_has_current_mapping(binding, data) or not binding.shopify_takeover_pending:
            raise ChannelReviewRequired('shopify_stock_takeover_mapping_changed')
        actual, target, book = self._check_shopify_takeover(binding)
        if recovering:
            return self._verify_takeover(request, binding, actual, target, book)
        if (actual, target, book) != (data['baseline'], data['target'], data['book']):
            raise ChannelReviewRequired('shopify_stock_takeover_preview_changed')
        if actual == target:
            return self._finish_takeover(binding, actual, book)
        request._mark_before_send()
        binding = self._lock_inventory_dispatch(data)
        if not self._inventory_request_has_current_mapping(binding, data):
            request._record_remote_rejection('shopify_stock_takeover_mapping_changed')
            raise ChannelReviewRequired('shopify_stock_takeover_mapping_changed')
        try:
            quantities = self._check_shopify_takeover(binding)
        except DeferredChannelError as exc:
            request._record_remote_rejection(exc.code)
            raise ChannelReviewRequired(exc.code) from None
        if quantities != (actual, target, book):
            request._record_remote_rejection('shopify_stock_takeover_preview_changed')
            raise ChannelReviewRequired('shopify_stock_takeover_preview_changed')
        result = self._shopify_client().query(INVENTORY_SET, {
            'key': request.idempotency_key,
            'input': {
                'name': 'available', 'reason': 'correction',
                'referenceDocumentUri': 'qing-channel://inventory/' + request.idempotency_key,
                'quantities': [{'inventoryItemId': data['item'], 'locationId': data['location'],
                                'quantity': target, 'changeFromQuantity': actual}],
            },
        }).get('inventorySetQuantities') or {}
        if result.get('userErrors'):
            request._record_remote_rejection(INVENTORY_COMPARE_REJECTED)
            raise ChannelReviewRequired('shopify_stock_takeover_preview_changed')
        if not result.get('inventoryAdjustmentGroup'):
            raise DeferredChannelError('shopify_stock_takeover_write_unknown')
        actual = self._shopify_inventory(binding)
        if actual == target:
            return self._finish_takeover(binding, actual, book)
        raise DeferredChannelError('shopify_stock_takeover_waiting_for_orders')

    def _verify_takeover(self, request, binding, actual, target, book):
        data = request.payload
        if request.confirmed_rejection:
            raise ChannelReviewRequired('shopify_stock_takeover_preview_changed')
        cutoff = request.first_sent_at or fields.Datetime.now()
        if not self.env['qing.channel.cursor'].search_count([
            ('store_id', '=', self.id), ('stream', '=', 'orders'), ('through', '>=', cutoff),
        ], limit=1):
            raise DeferredChannelError('shopify_stock_takeover_waiting_for_orders')
        difference = book - data['book']
        if actual == data['target'] - difference == target:
            return self._finish_takeover(binding, actual, book)
        raise ChannelReviewRequired('shopify_stock_takeover_quantity_needs_review')

    def _finish_takeover(self, binding, actual, book):
        binding._confirm_shopify_quantity(actual, book)
        binding.write({'shopify_takeover_pending': False, 'stock_paused': False})
        return 'success'
