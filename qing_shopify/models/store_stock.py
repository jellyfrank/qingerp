from datetime import timedelta
from uuid import uuid4

from psycopg2.errors import SerializationFailure

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.addons.qing_channel.errors import ChannelReviewRequired, DeferredChannelError
from odoo.fields import Domain

IDEMPOTENCY_WINDOW = timedelta(hours=24) - timedelta(minutes=2)
INVENTORY_COMPARE_REJECTED = 'shopify_inventory_compare_rejected'
INVENTORY_CONFLICT_LIMIT = 'shopify_inventory_conflict_limit'
INVENTORY_RECHECK_REQUESTED = 'shopify_inventory_recheck_requested'
STOCK_HANDOVER_MISSING = 'shopify_erp_stock_handover_missing'


INVENTORY_LEVEL = '''
query ChannelInventory($item: ID!, $location: ID!) {
  inventoryItem(id: $item) {
    id tracked inventoryLevel(locationId: $location) { quantities(names: ["available"]) { name quantity } }
  }
}
'''
INVENTORY_ACTIVATE = '''
mutation ChannelInventoryActivate($item: ID!, $location: ID!, $key: String!) {
  inventoryActivate(inventoryItemId: $item, locationId: $location) @idempotent(key: $key) {
    inventoryLevel { id }
    userErrors { field }
  }
}
'''
INVENTORY_SET = '''
mutation ChannelInventorySet($input: InventorySetQuantitiesInput!, $key: String!) {
  inventorySetQuantities(input: $input) @idempotent(key: $key) {
    inventoryAdjustmentGroup { id referenceDocumentUri }
    userErrors { code field }
  }
}
'''


class ShopifyStockRequest(models.Model):
    _inherit = 'qing.channel.request'

    @api.model
    def _expedited_job_domain(self):
        inventory_set = Domain('store_id.platform', '=', 'shopify') & Domain('operation', '=', 'inventory_set')
        return super()._expedited_job_domain() | inventory_set

    def _shopify_inventory_conflict_limit_reached(self):
        attempts = self.env['qing.channel.request.attempt']
        scope = [('request_id.action_id', '=', self.action_id.id)]
        last_recheck = attempts.search(scope + [('result', '=', INVENTORY_RECHECK_REQUESTED)], limit=1)
        rejected = scope + [('result', '=', INVENTORY_COMPARE_REJECTED), ('id', '>', last_recheck.id or 0)]
        return attempts.search_count(rejected, limit=3) == 3

    def _recheck_after_exception(self, code):
        if (code != INVENTORY_CONFLICT_LIMIT or self.store_id.platform != 'shopify'
                or self.operation not in ('inventory_set', 'inventory_verify')):
            return super()._recheck_after_exception(code)
        self.state = 'superseded'
        self._log_attempt(INVENTORY_RECHECK_REQUESTED)
        action = self.action_id
        action.write({'state': 'pending', 'tries': 0, 'retry_at': False, 'expedited': True})
        self.env['qing.channel.exception'].search([
            ('store_id', '=', self.store_id.id), ('object_model', '=', action._name),
            ('object_id', '=', action.id), ('code', '=', code), ('resolved', '=', False),
        ]).resolved = True


class ShopifyStoreStock(models.Model):
    _inherit = 'qing.channel.store'

    def _shopify_inventory_level(self, binding):
        data = self._shopify_client().query(INVENTORY_LEVEL, {
            'item': binding.external_id, 'location': binding.location_external_id,
        }).get('inventoryItem') or {}
        if not data.get('tracked'):
            raise ChannelReviewRequired('shopify_inventory_item_not_tracked')
        return data.get('inventoryLevel')

    def _shopify_inventory(self, binding):
        level = self._shopify_inventory_level(binding)
        if not level:
            raise ChannelReviewRequired('shopify_inventory_location_not_active')
        quantities = {item['name']: item['quantity'] for item in level['quantities']}
        if 'available' not in quantities:
            raise ChannelReviewRequired('shopify_available_not_visible')
        return quantities['available']

    def _shopify_inventory_bindings(self):
        domain = [('store_id', '=', self.id), ('kind', '=', 'inventory'),
                  ('location_external_id', '=', self.shopify_location)]
        return self.env['qing.channel.binding'].search(domain)

    def _shopify_stock_values(self, binding):
        self.ensure_one()
        if not binding.product_id or not binding.product_id.is_storable:
            raise ChannelReviewRequired('shopify_stock_product_mapping_required')
        scope = [('store_id.warehouse_id', '=', self.warehouse_id.id),
                 ('store_id.company_id', '=', self.company_id.id), ('store_id.platform', '=', 'shopify')]
        claims_domain = scope + [('product_id', '=', binding.product_id.id)]
        # Claims keep Shopify's committed quantity separate from ERP reservations and deliveries.
        Claim = self.env['qing.channel.stock.claim']
        claims = Claim.search(claims_domain)
        unmapped = scope + [('product_id', '=', False), ('shopify_opening_fulfilled_only', '=', False)]
        if Claim.search_count(unmapped, limit=1):
            raise ChannelReviewRequired('shopify_stock_unmapped_commitment')
        if self.env['qing.channel.order'].search_count(scope + [('stock_unverified', '=', True)], limit=1):
            raise ChannelReviewRequired('shopify_stock_unverified_commitment')
        if claims.filtered(lambda claim: claim.location_uncertain and not claim.shopify_opening_fulfilled_only):
            raise ChannelReviewRequired('shopify_stock_commitment_location_uncertain')
        known_book = 0
        commitments = []
        for claim in claims:
            remaining = claim.ordered_qty - claim.cancelled_qty - claim.opening_fulfilled_qty
            cutoff = claim.store_id.cutover_at if claim.line_id.order_id.historical else False
            commitments.append((claim.line_id.sale_line_id, remaining, cutoff))
            if claim.store_id == self:
                known_book += claim.ordered_qty - claim.opening_fulfilled_qty - claim.restocked_qty
        imported = {(claim.store_id.id, claim.line_id.external_id): claim.line_id for claim in claims}
        for commitment in self.env['qing.shopify.opening.commitment'].search(claims_domain):
            line = imported.get((commitment.store_id.id, commitment.external_line_id))
            if line:
                try:
                    commitment._check_imported_order_line(line)
                except ValidationError:
                    raise ChannelReviewRequired('shopify_opening_commitment_match_requires_review') from None
                if commitment.sale_line_id and commitment.sale_line_id != line.sale_line_id:
                    raise ChannelReviewRequired('shopify_opening_commitment_match_requires_review')
                continue
            commitments.append((commitment.sale_line_id, commitment.quantity, commitment.store_id.cutover_at))
            if commitment.store_id == self:
                known_book += commitment.quantity
        counted_sale_lines = set()
        unhanded = 0
        for sale_line, quantity, cutover_at in commitments:
            if quantity <= 0:
                continue
            if sale_line:
                if sale_line.id in counted_sale_lines:
                    raise ChannelReviewRequired('shopify_stock_handover_reused')
                counted_sale_lines.add(sale_line.id)
            unhanded += self._unhanded_stock_quantity(sale_line, binding.product_id, quantity, cutover_at)
        available = binding.product_id.with_company(self.company_id).with_context(
            warehouse_id=self.warehouse_id.id
        ).free_qty
        return max(0, int(available - unhanded - self.shopify_safety_stock)), known_book

    def _unhanded_stock_quantity(self, sale_line, product, remaining, cutover_at):
        warehouse_location = self.warehouse_id.view_location_id
        moves = sale_line.move_ids.filtered(
            lambda move: move.state not in ('cancel', 'draft')
            and move.product_id == product
            and move.location_dest_usage == 'customer'
            and move.location_id._child_of(warehouse_location)
            # The opening count already reflects completed movements before cutover.
            and (move.state != 'done' or not cutover_at or move.date >= cutover_at)
        )
        handed = sum(move.uom_id._compute_quantity(move.quantity, product.uom_id, round=False) for move in moves)
        return max(0, remaining - handed)

    def _check_shopify_handover_differences(self):
        scope = [('store_id', '=', self.id)]
        claims = self.env['qing.channel.stock.claim']
        affected = claims.search(scope + [
            '|', ('line_id.sale_line_id.order_id.state', '=', 'cancel'),
            '&', ('line_id.sale_line_id', '=', False), ('line_id.order_id.sale_id', '!=', False),
        ])
        pending_orders = self.env['qing.channel.order']
        for claim in affected:
            line = claim.line_id
            order = line.order_id
            if order in pending_orders or (order.snapshot or {}).get('cancelledAt'):
                continue
            shipped = self._shopify_shipped_before_cutover(order, line, all_dates=True) if order.snapshot else 0
            if claim.ordered_qty - claim.cancelled_qty > shipped:
                pending_orders |= order

        opening = self.env['qing.shopify.opening.commitment'].search(scope + [
            ('quantity', '>', 0), ('sale_line_id.order_id.state', '=', 'cancel'),
        ])
        if opening:
            synced = claims.search(scope + [('line_id.external_id', 'in', opening.mapped('external_line_id'))])
            synced_ids = set(synced.line_id.mapped('external_id'))
            opening = opening.filtered(lambda line: line.external_line_id not in synced_ids)

        pending = {(record._name, record.id): record for records in (pending_orders, opening) for record in records}
        ExceptionModel = self.env['qing.channel.exception']
        issues = ExceptionModel.search(scope + [('code', '=', STOCK_HANDOVER_MISSING), ('resolved', '=', False)])
        for issue in issues:
            if pending.pop((issue.object_model, issue.object_id), None) is None:
                issue.resolved = True
        for record in pending.values():
            ExceptionModel._flag(self, record, STOCK_HANDOVER_MISSING)

    @api.model
    def _cron_shopify_inventory(self):
        domain = [('platform', '=', 'shopify'), ('active', '=', True)]
        store_model = self.sudo()
        Action = self.env['qing.channel.action'].sudo()
        for store in store_model.search(domain):
            store._check_shopify_handover_differences()
            if not store.onboarded or store.shopify_health != 'connected':
                continue
            for binding in store._shopify_inventory_bindings().filtered(lambda record: not record.stock_paused):
                try:
                    target, book = store._shopify_stock_values(binding)
                except ChannelReviewRequired as exc:
                    if binding.stock_hold_reason != exc.code:
                        binding.stock_hold_reason = exc.code
                    self.env['qing.channel.exception'].sudo()._flag(store, binding, exc.code)
                    continue
                if binding.stock_hold_reason:
                    binding.stock_hold_reason = False
                changed = (target, book) != (binding.last_quantity, binding.shopify_confirmed_book)
                if not changed and binding.quantity_checked_at and (
                    fields.Datetime.now() - binding.quantity_checked_at < timedelta(days=1)
                ):
                    continue
                active_domain = [('store_id', '=', store.id), ('kind', '=', 'stock'),
                                 ('key', '=like', f'stock:{binding.id}:%'),
                                 ('state', 'in', ['pending', 'waiting', 'external', 'review'])]
                pending = Action.search(active_domain, limit=1)
                if pending:
                    if changed and not pending.expedited and pending.state != 'review':
                        pending.expedited = True
                    continue
                Action._ensure_action(store, f'stock:{binding.id}:{binding.quantity_version}:{target}:{book}', 'stock',
                                      data={'binding': binding.id}, expedited=changed)

    def _execute_channel_action(self, action):
        if self.platform != 'shopify' or action.kind not in ('stock', 'inventory_activation'):
            return super()._execute_channel_action(action)
        binding = self.env['qing.channel.binding'].browse(action.data['binding']).exists()
        if action.kind == 'inventory_activation':
            self.lock_for_update(allow_referencing=True)
            if not self._shopify_inventory_can_activate(binding):
                return 'obsolete'
            self._prepare_activation_request(action, binding)
            return 'external'
        if not binding or binding.store_id != self or not self.onboarded or binding.stock_paused:
            return 'obsolete'
        if self.shopify_health != 'connected':
            raise DeferredChannelError('shopify_disconnected')
        target, book = self._shopify_stock_values(binding)
        self._prepare_inventory_request(action, binding, target, book)
        return 'external'

    def _prepare_inventory_request(self, action, binding, target, book):
        data = {'binding': binding.id, 'baseline_version': binding.quantity_version,
                'target': target, 'book': book, 'baseline': binding.last_quantity,
                'item': binding.external_id, 'location': binding.location_external_id}
        unchanged = (target, book) == (binding.last_quantity, binding.shopify_confirmed_book)
        operation = 'inventory_verify' if unchanged else 'inventory_set'
        self.env['qing.channel.request']._prepare_request(action, operation, f'stock:{binding.id}', data)

    def _execute_channel_request(self, channel_request, recovering):
        if self.platform != 'shopify':
            return super()._execute_channel_request(channel_request, recovering)
        if self.shopify_health != 'connected':
            raise DeferredChannelError('shopify_disconnected')
        if channel_request.operation in ('inventory_set', 'inventory_verify'):
            return self._send_inventory_request(channel_request, recovering)
        if channel_request.operation == 'inventory_activate':
            return self._send_activation_request(channel_request, recovering)
        return super()._execute_channel_request(channel_request, recovering)

    def _lock_inventory_dispatch(self, payload):
        try:
            self.invalidate_recordset()
            if self.try_lock_for_update(allow_referencing=True):
                binding = self.env['qing.channel.binding'].browse(payload['binding']).exists()
                binding.invalidate_recordset()
                if not binding or binding.try_lock_for_update(allow_referencing=True):
                    return binding
        except SerializationFailure:
            # The queue rolls back and rechecks using a fresh database snapshot.
            pass
        raise DeferredChannelError('shopify_inventory_dispatch_busy', retry_after=5)

    def _inventory_request_has_current_mapping(self, binding, payload):
        return (binding and binding.store_id == self and binding.kind == 'inventory'
                and binding.location_external_id == self.shopify_location
                and payload.get('item', binding.external_id) == binding.external_id
                and payload.get('location', binding.location_external_id) == binding.location_external_id)

    def _discard_unsent_inventory_request(self, request):
        request._log_attempt('shopify_inventory_send_cancelled_before_transport')
        return 'obsolete'

    def _replace_changed_inventory_target(self, request, binding):
        target, book = self._shopify_stock_values(binding)
        if (target, book) == (request.payload['target'], request.payload['book']):
            return False
        self._prepare_inventory_request(request.action_id, binding, target, book)
        return True

    def _send_inventory_request(self, channel_request, recovering):
        payload = channel_request.payload
        binding = self._lock_inventory_dispatch(payload)
        if not self.active or self.shopify_health != 'connected':
            raise DeferredChannelError('shopify_disconnected')
        if not self._inventory_request_has_current_mapping(binding, payload):
            if channel_request.first_sent_at:
                raise ChannelReviewRequired('shopify_inventory_request_needs_reconciliation')
            return 'obsolete'
        paused = not self.onboarded or binding.stock_paused
        if paused and not channel_request.first_sent_at:
            return 'obsolete'
        checking = channel_request.operation == 'inventory_verify'
        if binding.quantity_version != payload['baseline_version']:
            if recovering:
                raise ChannelReviewRequired('shopify_inventory_old_request_needs_reconciliation')
            return 'obsolete'
        if paused or checking:
            return self._reconcile_inventory_request(channel_request, binding, publish=not paused)
        age = fields.Datetime.now() - channel_request.first_sent_at if recovering else timedelta()
        if recovering and (channel_request.confirmed_rejection or channel_request.reconcile_after
                           or age >= IDEMPOTENCY_WINDOW):
            return self._reconcile_inventory_request(channel_request, binding)
        if not recovering and self._replace_changed_inventory_target(channel_request, binding):
            return 'superseded'
        channel_request._mark_before_send()
        if not recovering:
            # Odoo can open a snapshot while refreshing registry caches after the send marker commits.
            # Close it before checking the newest committed warehouse quantities.
            self.env.cr.commit()
        binding = self._lock_inventory_dispatch(payload)
        if not self.active or self.shopify_health != 'connected':
            if recovering:
                raise DeferredChannelError('shopify_disconnected')
            return self._discard_unsent_inventory_request(channel_request)
        if not self._inventory_request_has_current_mapping(binding, payload) or (
            binding.quantity_version != payload['baseline_version']
        ):
            if recovering:
                raise ChannelReviewRequired('shopify_inventory_old_request_needs_reconciliation')
            return self._discard_unsent_inventory_request(channel_request)
        if not self.onboarded or binding.stock_paused:
            if recovering:
                return self._reconcile_inventory_request(channel_request, binding, publish=False)
            return self._discard_unsent_inventory_request(channel_request)
        if not recovering:
            try:
                replaced = self._replace_changed_inventory_target(channel_request, binding)
            except ChannelReviewRequired:
                channel_request._record_remote_rejection('shopify_inventory_send_cancelled_before_transport')
                raise
            if replaced:
                channel_request._log_attempt('shopify_inventory_target_changed_before_transport')
                return 'superseded'
        data = self._shopify_client().query(INVENTORY_SET, {
            'key': channel_request.idempotency_key,
            'input': {
                'name': 'available', 'reason': 'correction',
                'referenceDocumentUri': 'qing-channel://inventory/' + channel_request.idempotency_key,
                'quantities': [{
                    'inventoryItemId': payload.get('item', binding.external_id),
                    'locationId': payload.get('location', binding.location_external_id),
                    'quantity': payload['target'], 'changeFromQuantity': payload['baseline'],
                }],
            },
        })['inventorySetQuantities']
        errors = {item['code'] for item in data.get('userErrors', [])}
        if errors == {'CHANGE_FROM_QUANTITY_STALE'}:
            channel_request._record_remote_rejection(INVENTORY_COMPARE_REJECTED)
            return self._reconcile_inventory_request(channel_request, binding)
        if errors or not data.get('inventoryAdjustmentGroup'):
            raise ChannelReviewRequired('shopify_inventory_write_denied')
        channel_request._log_attempt('shopify_inventory_confirmed')
        channel_request.remote_reference = data['inventoryAdjustmentGroup'].get('referenceDocumentUri')
        binding.invalidate_recordset()
        binding.lock_for_update(allow_referencing=True)
        if binding.quantity_version == payload['baseline_version']:
            binding._confirm_shopify_quantity(payload['target'], payload['book'])
        return 'success'

    def _reconcile_inventory_request(self, channel_request, binding, publish=True):
        payload = channel_request.payload
        if not channel_request.reconcile_after:
            actual = self._shopify_inventory(binding)
            if channel_request.operation == 'inventory_verify' and actual == payload['baseline']:
                target, book = self._shopify_stock_values(binding)
                if book == binding.shopify_confirmed_book:
                    binding._confirm_shopify_quantity(actual, book)
                    if target == actual:
                        return 'satisfied'
                    if not publish:
                        return 'obsolete'
                    channel_request.action_id.expedited = True
                    self._prepare_inventory_request(channel_request.action_id, binding, target, book)
                    return 'superseded'
            channel_request.reconcile_quantity = actual
            channel_request.reconcile_after = fields.Datetime.now()
            channel_request._log_attempt('shopify_inventory_observed')
            self.env.cr.commit()
            raise DeferredChannelError('shopify_inventory_waiting_for_order_scan')
        Cursor = self.env['qing.channel.cursor']
        domain = [('store_id', '=', self.id), ('stream', '=', 'orders'),
                  ('through', '>=', channel_request.reconcile_after)]
        if not Cursor.search_count(domain, limit=1):
            raise DeferredChannelError('shopify_inventory_waiting_for_order_scan')
        if self._shopify_pending_imports():
            raise DeferredChannelError('shopify_inventory_waiting_for_order_import')
        actual = self._shopify_inventory(binding)
        if actual != channel_request.reconcile_quantity:
            channel_request.write({'reconcile_after': fields.Datetime.now(), 'reconcile_quantity': actual})
            channel_request._log_attempt('shopify_inventory_observation_changed')
            self.env.cr.commit()
            raise DeferredChannelError('shopify_inventory_waiting_for_order_scan')
        target, known_book = self._shopify_stock_values(binding)
        intervening = known_book - binding.shopify_confirmed_book
        explained = {payload['baseline'] - intervening}
        if channel_request.first_sent_at and not channel_request.confirmed_rejection:
            explained.add(payload['target'] - intervening)
        if actual not in explained or binding.quantity_version != payload['baseline_version']:
            raise ChannelReviewRequired('shopify_inventory_difference_requires_review')
        binding._confirm_shopify_quantity(actual, known_book)
        if actual == target:
            return 'satisfied'
        if not publish:
            return 'obsolete'
        if channel_request._shopify_inventory_conflict_limit_reached():
            channel_request._finish_review(INVENTORY_CONFLICT_LIMIT)
            return 'review'
        self._prepare_inventory_request(channel_request.action_id, binding, target, known_book)
        return 'superseded'

    def _shopify_inventory_can_activate(self, binding):
        return (binding and binding.store_id == self and binding.kind == 'inventory'
                and binding.location_external_id == self.shopify_location and not self.onboarded)

    def _prepare_activation_request(self, action, binding):
        payload = {'binding': binding.id, 'item': binding.external_id, 'location': binding.location_external_id}
        self.env['qing.channel.request']._prepare_request(
            action, 'inventory_activate', f'stock:{binding.id}', payload,
        )

    def _send_activation_request(self, channel_request, recovering):
        payload = channel_request.payload
        binding = self._lock_inventory_dispatch(payload)
        if not self.active or self.shopify_health != 'connected':
            raise DeferredChannelError('shopify_disconnected')
        if (not self._inventory_request_has_current_mapping(binding, payload)
                or not self._shopify_inventory_can_activate(binding)):
            if recovering:
                raise ChannelReviewRequired('shopify_inventory_activation_mapping_changed')
            return 'obsolete'
        if self._shopify_inventory_level(binding) is not None:
            return 'satisfied'
        if recovering and fields.Datetime.now() - channel_request.first_sent_at >= IDEMPOTENCY_WINDOW:
            self._prepare_activation_request(channel_request.action_id, binding)
            return 'superseded'
        channel_request._mark_before_send()
        binding = self._lock_inventory_dispatch(payload)
        if not self.active or self.shopify_health != 'connected':
            if recovering:
                raise DeferredChannelError('shopify_disconnected')
            return self._discard_unsent_inventory_request(channel_request)
        if (not self._inventory_request_has_current_mapping(binding, payload)
                or not self._shopify_inventory_can_activate(binding)):
            if recovering:
                raise ChannelReviewRequired('shopify_inventory_activation_mapping_changed')
            return self._discard_unsent_inventory_request(channel_request)
        result = self._shopify_client().query(INVENTORY_ACTIVATE, {
            'item': payload['item'], 'location': payload['location'], 'key': channel_request.idempotency_key,
        })['inventoryActivate']
        if result.get('userErrors') or not result.get('inventoryLevel'):
            raise ChannelReviewRequired('shopify_inventory_activation_rejected')
        channel_request.remote_reference = result['inventoryLevel']['id']
        channel_request._log_attempt('shopify_inventory_activation_confirmed')
        return 'success'

    def _enqueue_shopify_inventory_activation(self, binding):
        self.lock_for_update(allow_referencing=True)
        if self.shopify_health != 'connected' or not self._shopify_inventory_can_activate(binding):
            raise UserError(_('Connect Shopify and pause inventory publishing before enabling an inventory item.'))
        Action = self.env['qing.channel.action']
        if Action.search_count([
            ('store_id', '=', self.id), ('kind', '=', 'inventory_activation'),
            ('key', '=like', f'inventory-activation:{binding.id}:%'),
            ('state', 'in', ('pending', 'external', 'review', 'waiting')),
        ], limit=1):
            raise UserError(_('An activation request for this inventory item already needs processing.'))
        binding.shopify_preview_at = False
        Action._ensure_action(
            self, f'inventory-activation:{binding.id}:{uuid4().hex}', 'inventory_activation',
            data={'binding': binding.id},
        )

    def _shopify_inventory_cutover_bindings(self):
        self.ensure_one()
        if self.platform != 'shopify':
            raise UserError(_('Choose a Shopify store.'))
        self.lock_for_update(allow_referencing=True)
        if self.shopify_health != 'connected' or not self.shopify_open_orders_verified:
            raise UserError(_('Verify the complete list of open orders before preparing inventory publishing.'))
        if self.onboarded:
            raise UserError(_('Inventory publishing is already enabled.'))
        if self._shopify_active_bulk_import():
            raise UserError(_('Finish the Shopify bulk history import first.'))
        cursor = self.env['qing.channel.cursor'].search([
            ('store_id', '=', self.id), ('stream', '=', 'orders'),
        ], limit=1)
        if not cursor or cursor.window_end or cursor.through < self.cutover_at:
            raise UserError(_('Complete the initial Shopify order scan before preparing inventory publishing.'))
        if self._shopify_pending_imports():
            raise UserError(_('Process or resolve outstanding Shopify imports first.'))
        if self.env['qing.channel.action'].search_count([
            ('store_id', '=', self.id), ('kind', '=', 'inventory_activation'),
            ('state', 'in', ('pending', 'waiting', 'external', 'review')),
        ], limit=1):
            raise UserError(_('Resolve Shopify inventory activations before preparing the cutover.'))
        bindings = self._shopify_inventory_bindings().filtered(lambda binding: not binding.stock_paused)
        if not bindings:
            raise UserError(_('Map at least one Shopify inventory item and location.'))
        bindings.lock_for_update(allow_referencing=True)
        return bindings

    def _shopify_pending_imports(self):
        return self.env['qing.channel.delivery'].search_count([
            ('store_id', '=', self.id), ('state', '!=', 'done'), ('topic', '!=', 'app/uninstalled'),
        ], limit=1)

    def _shopify_cutover_quantities(self, binding):
        try:
            target, book = self._shopify_stock_values(binding)
            actual = self._shopify_inventory(binding)
        except DeferredChannelError as exc:
            raise UserError(_('Inventory initialization requires review for %(product)s (%(code)s).',
                              product=binding.product_id.display_name, code=exc.code)) from None
        return actual, target, book

    def action_shopify_prepare_inventory(self):
        self._check_channel_manager()
        store = self.sudo().with_company(self.company_id)
        bindings = store._shopify_inventory_cutover_bindings()
        checked_at = fields.Datetime.now()
        for binding in bindings:
            actual, target, book = store._shopify_cutover_quantities(binding)
            binding.write({
                'shopify_preview_at': checked_at,
                'shopify_preview_available': actual,
                'shopify_preview_target': target,
                'shopify_preview_book': book,
            })
        return {
            'name': _('Shopify Inventory Cutover'),
            'type': 'ir.actions.act_window',
            'res_model': 'qing.channel.binding',
            'views': [(self.env.ref('qing_shopify.view_shopify_inventory_cutover_list').id, 'list')],
            'domain': [('id', 'in', bindings.ids)],
            'target': 'current',
        }

    def action_shopify_enable_inventory(self):
        self._check_channel_manager()
        store = self.sudo().with_company(self.company_id)
        bindings = store._shopify_inventory_cutover_bindings()
        requests = self.env['qing.channel.request'].sudo().search([
            ('store_id', '=', self.id), ('resource', 'in', [f'stock:{binding.id}' for binding in bindings]),
            ('operation', 'in', ('inventory_set', 'inventory_verify')),
            ('state', 'in', ('uncertain', 'verify', 'review')),
        ])
        cutoff = fields.Datetime.now() - IDEMPOTENCY_WINDOW
        if requests.filtered(lambda request: request.state != 'review' or (
            request.first_sent_at and not request.confirmed_rejection and request.first_sent_at > cutoff
        )):
            raise UserError(_('Recheck outstanding inventory requests before enabling publishing.'))
        for binding in bindings:
            if not binding.shopify_preview_at:
                raise UserError(_('Prepare and review the inventory cutover report before enabling publishing.'))
            actual, target, book = store._shopify_cutover_quantities(binding)
            preview = (binding.shopify_preview_available, binding.shopify_preview_target, binding.shopify_preview_book)
            if (actual, target, book) != preview:
                raise UserError(_('Inventory changed for %s. Refresh and review the cutover report.',
                                  binding.product_id.display_name))
            binding._confirm_shopify_quantity(actual, book)
        for request in requests:
            request._log_attempt('shopify_inventory_cutover_superseded')
            request.state = 'obsolete'
            request.action_id.state = 'obsolete'
        store.write({'onboarded': True})
        return True

    def action_shopify_pause_inventory(self):
        self._check_channel_manager()
        self.sudo().write({'onboarded': False, 'shopify_open_orders_verified': False})
        return True
