from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from odoo.addons.qing_channel.errors import ChannelReviewRequired, DeferredChannelError


MONEY = '{ shopMoney { amount currencyCode } presentmentMoney { amount currencyCode } }'
PAGE_INFO = 'pageInfo { hasNextPage endCursor }'
TAX = f'{{ title source rate channelLiable priceSet {MONEY} }}'
FIRST_PAGE_SIZE = 12
NEXT_PAGE_SIZE = 20
PAGINATION_LIMIT = 25_000

TRANSACTION_FIELDS = f'''
    id kind status gateway processedAt settlementCurrency settlementCurrencyRate
    currencyExchangeAdjustment {{ adjustment {{ amount currencyCode }} }}
    parentTransaction {{ id }} amountSet {MONEY}
'''
LINE_FIELDS = f'''
    id name sku quantity currentQuantity unfulfilledQuantity requiresShipping isGiftCard
    lineItemGroup {{ id }}
    variant {{ id requiresComponents inventoryItem {{ id tracked }} }}
    originalUnitPriceSet {MONEY} originalTotalSet {MONEY}
    priceAfterAllDiscountsBeforeTaxesSet {MONEY}
    discountAllocations {{ allocatedAmountSet {MONEY} }}
    taxLines {TAX}
'''
SHIPPING_FIELDS = f'id title code currentDiscountedPriceSet {MONEY} discountedPriceSet {MONEY} taxLines {TAX}'
REFUND_LINE_FIELDS = f'''
    id quantity restockType restocked location {{ id }} lineItem {{ id }}
    subtotalSet {MONEY} totalTaxSet {MONEY}
'''
REFUND_SHIPPING_FIELDS = f'id subtotalAmountSet {MONEY} taxAmountSet {MONEY} shippingLine {{ id }}'
ADJUSTMENT_FIELDS = f'id reason amountSet {MONEY} taxAmountSet {MONEY}'
ASSIGNMENT_LINE_FIELDS = 'id remainingQuantity totalQuantity lineItem { id }'
FULFILLED_LINE_FIELDS = 'id quantity lineItem { id }'


def _first_page(key, fields):
    return f'{key}(first: {FIRST_PAGE_SIZE}) {{ nodes {{ {fields} }} {PAGE_INFO} }}'


def _next_page(operation, parent, key, fields, type_name=None):
    selection = f'{key}(first: {NEXT_PAGE_SIZE}, after: $after) {{ nodes {{ {fields} }} {PAGE_INFO} }}'
    if type_name:
        selection = f'... on {type_name} {{ id {selection} }}'
    else:
        selection = f'id {selection}'
    return f'query {operation}($id: ID!, $after: String!) {{ {parent}(id: $id) {{ {selection} }} }}'


ADDRESS_FIELDS = 'firstName lastName address1 address2 city zip provinceCode countryCodeV2 phone'
CUSTOMER_FIELDS = f'''
    customer {{ id }}
    email phone
    shippingAddress {{ {ADDRESS_FIELDS} }}
    billingAddress {{ {ADDRESS_FIELDS} }}
'''
CUSTOMER_QUERY = f'''
query ChannelOrderCustomer($id: ID!) {{
    order(id: $id) {{ {CUSTOMER_FIELDS} }}
}}
'''
ORDER_QUERY = f'''
query ChannelOrder($id: ID!) {{
    order(id: $id) {{
        id name sourceName createdAt updatedAt cancelledAt taxesIncluded currencyCode
        currentTotalPriceSet {MONEY} currentTotalTaxSet {MONEY}
        originalTotalPriceSet {MONEY} totalTaxSet {MONEY}
        currentTotalDutiesSet {MONEY} currentTotalAdditionalFeesSet {MONEY}
        originalTotalDutiesSet {MONEY} originalTotalAdditionalFeesSet {MONEY}
        {CUSTOMER_FIELDS}
        refunds(first: 51) {{ id }}
        {_first_page('lineItems', LINE_FIELDS)}
        {_first_page('shippingLines', SHIPPING_FIELDS)}
    }}
}}
'''
ORDER_VERSION = '''
query ChannelOrderVersion($id: ID!) {
    order(id: $id) { updatedAt }
}
'''
ORDER_TRANSACTIONS = f'''
query ChannelOrderTransactions($id: ID!) {{
    order(id: $id) {{ updatedAt transactions(first: 100) {{ {TRANSACTION_FIELDS} }} }}
}}
'''
ORDER_LINE_PAGE = _next_page('ChannelOrderLines', 'order', 'lineItems', LINE_FIELDS)
SHIPPING_PAGE = _next_page('ChannelOrderShipping', 'order', 'shippingLines', SHIPPING_FIELDS)
REFUND_QUERY = f'''
query ChannelRefund($id: ID!) {{
    node(id: $id) {{
        ... on Refund {{
            id processedAt totalRefundedSet {MONEY}
            {_first_page('transactions', TRANSACTION_FIELDS)}
            {_first_page('refundLineItems', REFUND_LINE_FIELDS)}
            {_first_page('refundShippingLines', REFUND_SHIPPING_FIELDS)}
            duties {{ amountSet {MONEY} }}
            {_first_page('orderAdjustments', ADJUSTMENT_FIELDS)}
        }}
    }}
}}
'''
REFUND_TRANSACTION_PAGE = _next_page('ChannelRefundTransactions', 'node', 'transactions', TRANSACTION_FIELDS, 'Refund')
REFUND_LINE_PAGE = _next_page('ChannelRefundLines', 'node', 'refundLineItems', REFUND_LINE_FIELDS, 'Refund')
REFUND_SHIPPING_PAGE = _next_page('ChannelRefundShipping', 'node', 'refundShippingLines', REFUND_SHIPPING_FIELDS, 'Refund')
REFUND_ADJUSTMENT_PAGE = _next_page('ChannelRefundAdjustments', 'node', 'orderAdjustments', ADJUSTMENT_FIELDS, 'Refund')
ORDER_PAGE = f'''
query ChannelOrderPage($term: String!, $after: String) {{
    orders(first: 50, after: $after, query: $term, sortKey: UPDATED_AT) {{
        nodes {{ id updatedAt }} {PAGE_INFO}
    }}
}}
'''
FULFILLMENT_REFERENCES = f'''
query ChannelFulfillmentReferences($id: ID!) {{
    order(id: $id) {{
        id updatedAt
        {_first_page('fulfillmentOrders', 'id')}
        fulfillments(first: 51) {{ id }}
    }}
}}
'''
FULFILLMENT_REFERENCE_PAGE = _next_page('ChannelFulfillmentAssignments', 'order', 'fulfillmentOrders', 'id')
FULFILLMENT_ORDER = f'''
query ChannelFulfillmentOrder($id: ID!) {{
    fulfillmentOrder(id: $id) {{
        id status assignedLocation {{ location {{ id }} }}
        {_first_page('lineItems', ASSIGNMENT_LINE_FIELDS)}
    }}
}}
'''
ASSIGNMENT_LINE_PAGE = _next_page('ChannelAssignmentLines', 'fulfillmentOrder', 'lineItems', ASSIGNMENT_LINE_FIELDS)
FULFILLMENT = f'''
query ChannelFulfillment($id: ID!) {{
    node(id: $id) {{
        ... on Fulfillment {{
            id status createdAt location {{ id }}
            trackingInfo {{ company number url }}
            {_first_page('fulfillmentLineItems', FULFILLED_LINE_FIELDS)}
        }}
    }}
}}
'''
FULFILLED_LINE_PAGE = _next_page('ChannelFulfilledLines', 'node', 'fulfillmentLineItems', FULFILLED_LINE_FIELDS, 'Fulfillment')


def parse_date(value):
    if not value:
        return False
    return datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(timezone.utc).replace(tzinfo=None)


def shop_amount(bag, currency):
    try:
        data = bag['shopMoney']
        if data['currencyCode'] != currency:
            raise ValueError()
        result = Decimal(data['amount'])
        if not result.is_finite():
            raise ValueError()
        return result
    except (TypeError, KeyError, ValueError, InvalidOperation):
        raise ChannelReviewRequired('unsupported_shop_money') from None


def nodes(data):
    if data.get('pageInfo', {}).get('hasNextPage'):
        raise ChannelReviewRequired('nested_payload_requires_review')
    return data.get('nodes', [])


def _complete_connection(client, parent, field, document, result_field):
    connection = parent[field]
    page = connection.get('pageInfo', {})
    if not page.get('hasNextPage'):
        connection.pop('pageInfo', None)
        return
    items = list(connection['nodes'])
    seen_ids = {item['id'] for item in items}
    if len(seen_ids) != len(items):
        raise DeferredChannelError('shopify_snapshot_page_overlap')
    seen_cursors = set()
    while page['hasNextPage']:
        after = page.get('endCursor')
        if not after or after in seen_cursors:
            raise DeferredChannelError('shopify_snapshot_cursor_invalid')
        if len(items) >= PAGINATION_LIMIT:
            raise ChannelReviewRequired('shopify_order_detail_limit_reached')
        seen_cursors.add(after)
        current = client.query(document, {'id': parent['id'], 'after': after}).get(result_field)
        if not current or current.get('id') != parent['id']:
            raise DeferredChannelError('shopify_snapshot_changed_during_read')
        next_connection = current[field]
        next_items = next_connection['nodes']
        next_page = next_connection['pageInfo']
        next_ids = {item['id'] for item in next_items}
        if not next_items or len(next_ids) != len(next_items) or seen_ids.intersection(next_ids):
            raise DeferredChannelError('shopify_snapshot_page_overlap')
        items.extend(next_items)
        if len(items) > PAGINATION_LIMIT:
            raise ChannelReviewRequired('shopify_order_detail_limit_reached')
        seen_ids.update(next_ids)
        page = next_page
    connection['nodes'] = items
    del connection['pageInfo']


def _check_order_version(client, external_id, version):
    current = client.query(ORDER_VERSION, {'id': external_id}).get('order')
    if not current or current['updatedAt'] != version:
        raise DeferredChannelError('shopify_snapshot_changed_during_read')


def _fetch_fulfillment_snapshot(client, external_id, version=None):
    references = client.query(FULFILLMENT_REFERENCES, {'id': external_id}).get('order')
    if not references:
        raise ChannelReviewRequired('fulfillment_not_visible')
    if version is not None and references['updatedAt'] != version:
        raise DeferredChannelError('shopify_snapshot_changed_during_read')
    if len(references['fulfillments']) > 50:
        raise ChannelReviewRequired('shopify_fulfillment_count_outside_pilot')
    _complete_connection(client, references, 'fulfillmentOrders', FULFILLMENT_REFERENCE_PAGE, 'order')
    orders = []
    for reference in nodes(references['fulfillmentOrders']):
        details = client.query(FULFILLMENT_ORDER, {'id': reference['id']}).get('fulfillmentOrder')
        if not details:
            raise ChannelReviewRequired('fulfillment_order_not_visible')
        _complete_connection(client, details, 'lineItems', ASSIGNMENT_LINE_PAGE, 'fulfillmentOrder')
        orders.append(details)
    fulfillments = []
    for reference in references['fulfillments']:
        details = client.query(FULFILLMENT, {'id': reference['id']}).get('node')
        if not details or not details.get('id'):
            raise ChannelReviewRequired('fulfillment_not_visible')
        _complete_connection(client, details, 'fulfillmentLineItems', FULFILLED_LINE_PAGE, 'node')
        fulfillments.append(details)
    return {'fulfillmentOrders': {'nodes': orders}, 'fulfillments': fulfillments}, references['updatedAt']


def fetch_fulfillment_snapshot(client, external_id):
    snapshot, version = _fetch_fulfillment_snapshot(client, external_id)
    _check_order_version(client, external_id, version)
    return snapshot


def fetch_refund(client, external_id):
    refund = client.query(REFUND_QUERY, {'id': external_id}).get('node')
    if not refund or refund.get('id') != external_id:
        raise ChannelReviewRequired('shopify_refund_not_visible')
    for field, document in (
        ('transactions', REFUND_TRANSACTION_PAGE), ('refundLineItems', REFUND_LINE_PAGE),
        ('refundShippingLines', REFUND_SHIPPING_PAGE), ('orderAdjustments', REFUND_ADJUSTMENT_PAGE),
    ):
        _complete_connection(client, refund, field, document, 'node')
    return refund


def fetch_order(client, external_id):
    order = client.query(ORDER_QUERY, {'id': external_id}).get('order')
    if not order:
        raise ChannelReviewRequired('order_not_visible')
    references = order['refunds']
    if len(references) > 50:
        raise ChannelReviewRequired('shopify_refund_count_outside_pilot')
    _complete_connection(client, order, 'lineItems', ORDER_LINE_PAGE, 'order')
    _complete_connection(client, order, 'shippingLines', SHIPPING_PAGE, 'order')
    status, version = _fetch_fulfillment_snapshot(client, external_id, order['updatedAt'])
    order.update(status)
    order['refunds'] = [fetch_refund(client, reference['id']) for reference in references]
    transactions = client.query(ORDER_TRANSACTIONS, {'id': external_id}).get('order')
    if not transactions or transactions['updatedAt'] != version:
        raise DeferredChannelError('shopify_snapshot_changed_during_read')
    order['transactions'] = transactions['transactions']
    if len(order['transactions']) >= 100:
        raise ChannelReviewRequired('shopify_order_detail_limit_reached')
    return order
