from collections import Counter
from urllib.parse import urlsplit

from psycopg2.errors import SerializationFailure

from odoo import fields, models
from odoo.addons.qing_channel.errors import ChannelReviewRequired, DeferredChannelError
from odoo.addons.qing_shopify.services.client import MUTATION_NOT_EXECUTED_CODES
from odoo.addons.qing_shopify.services.orders import fetch_fulfillment_snapshot, nodes


FULFILL = '''
mutation ChannelFulfill($fulfillment: FulfillmentInput!) {
  fulfillmentCreate(fulfillment: $fulfillment) {
    fulfillment { id }
    userErrors { field }
  }
}
'''
UPDATE_TRACKING = '''
mutation ChannelTracking($id: ID!, $tracking: FulfillmentTrackingInput!) {
  fulfillmentTrackingInfoUpdate(fulfillmentId: $id, trackingInfoInput: $tracking, notifyCustomer: false) {
    fulfillment { id }
    userErrors { field }
  }
}
'''


class ShopifyStoreDelivery(models.Model):
    _inherit = 'qing.channel.store'

    def _shopify_shipment_before_cutover(self, picking):
        return self.cutover_at and picking.date_done and picking.date_done < self.cutover_at

    def _enqueue_shopify_shipment(self, order, picking):
        Shipment = self.env['qing.shopify.shipment']
        domain = [('picking_id', '=', picking.id)]
        shipment = Shipment.search(domain, limit=1)
        if not shipment.remote_id and self._shopify_shipment_before_cutover(picking):
            return
        carrier = picking.carrier_id.name or False
        tracking = picking.carrier_tracking_ref or False
        tracking_url = self._shopify_tracking_url(picking.carrier_tracking_url) if tracking else False
        values = {'carrier': carrier, 'tracking': tracking, 'tracking_url': tracking_url}
        if not shipment:
            shipment = Shipment.create({'order_id': order.id, 'picking_id': picking.id, **values})
        elif (carrier, tracking, tracking_url) != (shipment.carrier, shipment.tracking, shipment.tracking_url):
            shipment.write({**values, 'version': shipment.version + 1})
        kind = 'tracking' if shipment.remote_id else 'fulfillment'
        self.env['qing.channel.action']._ensure_action(
            self, f'{kind}:{shipment.id}:{shipment.version}', kind,
            order_id=order.id, data={'shipment': shipment.id, 'version': shipment.version})
        if self.shopify_invoice_policy == 'shipment' and not order.historical:
            self.env['qing.channel.action']._ensure_action(
                self, f'invoice:{picking.id}', 'shipment_invoice', order_id=order.id)

    def _execute_channel_action(self, action):
        if self.platform != 'shopify' or action.kind not in ('fulfillment', 'tracking', 'shipment_invoice'):
            return super()._execute_channel_action(action)
        if self.shopify_health != 'connected':
            raise DeferredChannelError('shopify_disconnected')
        if action.kind == 'shipment_invoice':
            self._invoice_shopify_sale(action.order_id)
            self._reconcile_shopify_receivable(action.order_id)
            return
        shipment = self.env['qing.shopify.shipment'].browse(action.data['shipment']).exists()
        if not shipment or shipment.order_id != action.order_id or shipment.version != action.data['version']:
            return 'obsolete'
        if not shipment.remote_id and self._shopify_shipment_before_cutover(shipment.picking_id):
            return 'obsolete'
        if not self._shopify_shipment_eligible(shipment):
            raise ChannelReviewRequired('shopify_shipment_requires_review')
        prior_domain = [('resource', '=', f'fulfillment:{shipment.order_id.id}'),
                        ('operation', '=', 'fulfillment_create'), ('state', 'in', ['uncertain', 'verify', 'review'])]
        if self.env['qing.channel.request'].search_count(prior_domain, limit=1):
            raise DeferredChannelError('shopify_order_has_unconfirmed_shipment')
        data = {'shipment': shipment.id, 'version': shipment.version,
                'tracking': self._shopify_tracking(shipment)}
        if shipment.remote_id:
            data['remote_id'] = shipment.remote_id
            operation = 'tracking_update'
        else:
            data.update(self._shopify_shipment_lines(shipment))
            operation = 'fulfillment_create'
        self.env['qing.channel.request']._prepare_request(action, operation, f'fulfillment:{shipment.order_id.id}', data)
        return 'external'

    def _shopify_tracking_url(self, url):
        if not url:
            return False
        try:
            parts = urlsplit(url)
        except ValueError:
            return False
        if parts.scheme in ('http', 'https') and parts.hostname and parts.username is None:
            return url
        return False

    def _shopify_tracking(self, shipment):
        if not shipment.tracking:
            return {}
        carrier = shipment.picking_id.carrier_id.shopify_tracking_company
        values = {'number': shipment.tracking}
        if carrier:
            values['company'] = carrier
        if shipment.tracking_url:
            values['url'] = shipment.tracking_url
        return values

    def _shopify_shipment_lines(self, shipment):
        order = shipment.order_id
        client = self._shopify_client()
        snapshot = fetch_fulfillment_snapshot(client, order.external_id)
        assignment_by_line = {}
        for assignment in nodes(snapshot.get('fulfillmentOrders', {})):
            if assignment['status'] not in ('OPEN', 'IN_PROGRESS'):
                continue
            if assignment['assignedLocation']['location']['id'] != self.shopify_location:
                raise ChannelReviewRequired('shopify_shipment_location_outside_pilot')
            for detail in nodes(assignment['lineItems']):
                remote_line_id = detail['lineItem']['id']
                if remote_line_id in assignment_by_line and detail['remainingQuantity']:
                    raise ChannelReviewRequired('shopify_ambiguous_fulfillment_line')
                if detail['remainingQuantity']:
                    assignment_by_line[remote_line_id] = (assignment['id'], detail['id'], detail['remainingQuantity'])
        local_map = {line.sale_line_id.id: line.external_id for line in order.line_ids if line.sale_line_id}
        by_order_line = Counter()
        for move in shipment.picking_id.move_ids.filtered(lambda move: move.state == 'done'):
            line_id = local_map.get(move.sale_line_id.id)
            if not line_id:
                raise ChannelReviewRequired('shopify_shipment_line_mapping_required')
            qty = move.uom_id._compute_quantity(move.quantity, move.product_id.uom_id, round=False)
            if not qty > 0 or qty != int(qty):
                raise ChannelReviewRequired('shopify_shipment_quantity_invalid')
            by_order_line[line_id] += int(qty)
        if not by_order_line:
            raise ChannelReviewRequired('shopify_shipment_lines_missing')
        by_assignment = {}
        for remote_line_id, qty in by_order_line.items():
            assignment, line_id, remaining = assignment_by_line.get(remote_line_id, (None, None, 0))
            if qty > remaining:
                raise ChannelReviewRequired('shopify_shipment_quantity_exceeds_remaining')
            by_assignment.setdefault(assignment, []).append({'id': line_id, 'quantity': qty})
        return {'line_quantities': dict(by_order_line),
                'assignments': [{'fulfillmentOrderId': key, 'fulfillmentOrderLineItems': values}
                                for key, values in sorted(by_assignment.items())],
                'previous_ids': sorted(entry['id'] for entry in snapshot.get('fulfillments', []))}

    def _execute_channel_request(self, channel_request, recovering):
        if self.platform != 'shopify' or channel_request.operation not in ('fulfillment_create', 'tracking_update'):
            return super()._execute_channel_request(channel_request, recovering)
        if self.shopify_health != 'connected':
            raise DeferredChannelError('shopify_disconnected')
        data = channel_request.payload
        shipment = self.env['qing.shopify.shipment'].browse(data['shipment']).exists()
        if not shipment or shipment.order_id != channel_request.action_id.order_id:
            raise ChannelReviewRequired('shopify_shipment_binding_missing')
        if channel_request.operation == 'tracking_update':
            return self._send_shopify_tracking(channel_request, shipment, recovering)
        return self._send_shopify_fulfillment(channel_request, shipment, recovering)

    def _shopify_shipment_eligible(self, shipment):
        return shipment.picking_id.state == 'done' and not shipment.order_id.snapshot.get('cancelledAt')

    def _shopify_fulfillment_obsolete(self, shipment, data):
        return (shipment.remote_id or shipment.version != data['version']
                or self._shopify_shipment_before_cutover(shipment.picking_id))

    def _mark_shopify_shipment_for_transport(self, request, shipment):
        request._mark_before_send()
        # The marker commits. Lock the current source records before starting the mutation.
        try:
            locked = True
            for record in (self, shipment.order_id, shipment.picking_id, shipment):
                record.invalidate_recordset()
                if not record.try_lock_for_update(allow_referencing=True):
                    locked = False
                    break
        except SerializationFailure:
            self.env.cr.rollback()
            locked = False
        if not locked:
            request._record_remote_rejection('shopify_shipment_dispatch_busy')
            raise DeferredChannelError('shopify_shipment_dispatch_busy', retry_after=5)
        if not self.active or self.shopify_health != 'connected':
            request._record_remote_rejection('shopify_shipment_dispatch_disconnected')
            raise DeferredChannelError('shopify_disconnected')

    def _discard_unsent_shopify_shipment(self, request):
        request._log_attempt('shopify_shipment_send_cancelled_before_transport')
        return 'obsolete'

    def _send_shopify_fulfillment(self, channel_request, shipment, recovering):
        data = channel_request.payload
        if recovering and not channel_request.confirmed_rejection:
            return self._check_shopify_fulfillment(channel_request, shipment)
        if self._shopify_fulfillment_obsolete(shipment, data):
            return 'obsolete'
        if not self._shopify_shipment_eligible(shipment):
            raise ChannelReviewRequired('shopify_shipment_requires_review')
        source = (shipment.order_id.snapshot_version, self.shopify_location)
        current = self._shopify_shipment_lines(shipment)
        if any(data[key] != current[key] for key in ('line_quantities', 'assignments', 'previous_ids')):
            raise ChannelReviewRequired('shopify_shipment_contents_changed')
        self._mark_shopify_shipment_for_transport(channel_request, shipment)
        if self._shopify_fulfillment_obsolete(shipment, data):
            return self._discard_unsent_shopify_shipment(channel_request)
        if not self._shopify_shipment_eligible(shipment):
            channel_request._record_remote_rejection('shopify_shipment_send_cancelled_before_transport')
            raise ChannelReviewRequired('shopify_shipment_requires_review')
        if source != (shipment.order_id.snapshot_version, self.shopify_location):
            channel_request._record_remote_rejection('shopify_shipment_snapshot_changed_before_transport')
            raise DeferredChannelError('shopify_shipment_snapshot_changed', retry_after=5)
        values = {'lineItemsByFulfillmentOrder': data['assignments'], 'notifyCustomer': False}
        if data['tracking']:
            values['trackingInfo'] = data['tracking']
        try:
            result = self._shopify_client().query(FULFILL, {'fulfillment': values})['fulfillmentCreate']
        except DeferredChannelError as exc:
            if exc.code in MUTATION_NOT_EXECUTED_CODES:
                channel_request._record_remote_rejection(exc.code)
            raise
        if result.get('userErrors') or not result.get('fulfillment'):
            raise ChannelReviewRequired('shopify_fulfillment_rejected')
        self._record_shopify_fulfillment(channel_request, shipment, result['fulfillment']['id'])
        return 'success'

    def _check_shopify_fulfillment(self, channel_request, shipment):
        order = fetch_fulfillment_snapshot(self._shopify_client(), shipment.order_id.external_id)
        if not order:
            raise DeferredChannelError('shopify_fulfillment_status_unavailable')
        data = channel_request.payload
        wanted_tracking = data['tracking'].get('number')
        matches = []
        for candidate in order.get('fulfillments', []):
            if candidate['status'] != 'SUCCESS' or candidate['id'] in data['previous_ids']:
                continue
            if candidate['location']['id'] != self.shopify_location:
                continue
            quantities = Counter()
            for line in nodes(candidate['fulfillmentLineItems']):
                quantities[line['lineItem']['id']] += line['quantity']
            tracking_numbers = {row['number'] for row in candidate['trackingInfo']}
            if dict(quantities) == data['line_quantities'] and wanted_tracking and wanted_tracking in tracking_numbers:
                matches.append(candidate['id'])
        if len(matches) == 1:
            self._record_shopify_fulfillment(channel_request, shipment, matches[0])
            return 'satisfied'
        if len(matches) > 1:
            raise ChannelReviewRequired('shopify_fulfillment_ambiguous')
        # Absence in a read and sufficient remaining quantity cannot prove the mutation never ran.
        raise DeferredChannelError('shopify_fulfillment_outcome_unknown')

    def _record_shopify_fulfillment(self, channel_request, shipment, remote_id):
        shipment.remote_id = remote_id
        channel_request.remote_reference = remote_id
        channel_request._log_attempt('shopify_fulfillment_observed')
        if shipment.version != channel_request.payload['version']:
            self._enqueue_shopify_shipment(shipment.order_id, shipment.picking_id)

    def _send_shopify_tracking(self, channel_request, shipment, recovering):
        data = channel_request.payload
        if shipment.remote_id != data['remote_id']:
            if recovering:
                raise ChannelReviewRequired('shopify_tracking_binding_changed')
            return 'obsolete'
        wanted = data['tracking']
        current = self._shopify_tracking(shipment)
        outdated = shipment.version != data['version'] or current != wanted
        if outdated and (not recovering or channel_request.confirmed_rejection):
            return 'obsolete'
        if not wanted:
            raise ChannelReviewRequired('shopify_tracking_clear_requires_review')
        if recovering:
            order = fetch_fulfillment_snapshot(self._shopify_client(), shipment.order_id.external_id)
            candidate = next((item for item in order.get('fulfillments', []) if item['id'] == shipment.remote_id), None)
            if not candidate:
                raise DeferredChannelError('shopify_tracking_fulfillment_unavailable')
            actual = candidate['trackingInfo']
            if outdated and self._shopify_tracking_matches(actual, current):
                channel_request._log_attempt('shopify_newer_tracking_already_present')
                return 'obsolete'
            if self._shopify_tracking_matches(actual, wanted):
                return 'obsolete' if outdated else 'satisfied'
            if outdated or not channel_request.confirmed_rejection:
                # An unknown write may still be in flight and ordering matters on corrections.
                raise DeferredChannelError('shopify_tracking_outcome_unknown')
        prior = [('resource', '=', channel_request.resource), ('operation', '=', 'tracking_update'),
                 ('id', '!=', channel_request.id), ('state', 'in', ['uncertain', 'verify', 'review'])]
        if self.env['qing.channel.request'].search_count(prior, limit=1):
            raise DeferredChannelError('shopify_tracking_predecessor_unresolved')
        self._mark_shopify_shipment_for_transport(channel_request, shipment)
        if (shipment.remote_id != data['remote_id'] or shipment.version != data['version']
                or self._shopify_tracking(shipment) != wanted):
            return self._discard_unsent_shopify_shipment(channel_request)
        if not self._shopify_shipment_eligible(shipment):
            channel_request._record_remote_rejection('shopify_shipment_send_cancelled_before_transport')
            raise ChannelReviewRequired('shopify_shipment_requires_review')
        try:
            result = self._shopify_client().query(UPDATE_TRACKING, {
                'id': shipment.remote_id, 'tracking': wanted})['fulfillmentTrackingInfoUpdate']
        except DeferredChannelError as exc:
            if exc.code in MUTATION_NOT_EXECUTED_CODES:
                channel_request._record_remote_rejection(exc.code)
            raise
        if result.get('userErrors') or not result.get('fulfillment'):
            raise ChannelReviewRequired('shopify_tracking_update_rejected')
        channel_request._log_attempt('shopify_tracking_confirmed')
        return 'success'

    def _shopify_tracking_matches(self, actual, expected):
        return bool(expected) and len(actual) == 1 and all(actual[0].get(key) == value
                                                           for key, value in expected.items())
