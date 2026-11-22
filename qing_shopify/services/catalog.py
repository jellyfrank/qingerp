import re

from odoo.addons.qing_channel.errors import DeferredChannelError


CATALOG_RECHECK_TOPIC = 'products/recheck'
WEBHOOK_PRODUCT_TOPICS = frozenset({'products/create', 'products/update', 'products/delete'})
PRODUCT_TOPICS = WEBHOOK_PRODUCT_TOPICS | {'products/reconcile', CATALOG_RECHECK_TOPIC}
PRODUCT_ID = re.compile(r'gid://shopify/Product/[0-9]+\Z')
VARIANT_ID = re.compile(r'gid://shopify/ProductVariant/[0-9]+\Z')
PRODUCT_SCOPES = '''
query ChannelCatalogAuthorization {
    currentAppInstallation { accessScopes { handle } }
    shop { myshopifyDomain currencyCode taxesIncluded }
}
'''
CATALOG_PRODUCTS = '''
query ChannelCatalogProducts($after: String, $filter: String) {
    products(first: 25, after: $after, sortKey: ID, query: $filter) {
        nodes { id }
        pageInfo { hasNextPage endCursor }
    }
}
'''
VARIANT_FIELDS = '''
    id sku title price displayName requiresComponents inventoryPolicy
    selectedOptions { name value }
    inventoryItem { id tracked requiresShipping }
    barcodes(first: 20) { nodes { value type } pageInfo { hasNextPage } }
'''
CATALOG_PRODUCT = f'''
query ChannelCatalogProduct($id: ID!, $after: String) {{
    product(id: $id) {{
        id title status updatedAt descriptionHtml isGiftCard
        options {{ id name }}
        featuredMedia {{
            id status
            ... on MediaImage {{ image {{ url }} }}
        }}
        variants(first: 16, after: $after) {{
            nodes {{ {VARIANT_FIELDS} }}
            pageInfo {{ hasNextPage endCursor }}
        }}
    }}
}}
'''


def normalize_catalog_code(value):
    return (value or '').strip().lower()


def variant_code_keys(variant):
    sku = normalize_catalog_code(variant.get('sku'))
    if sku:
        yield f'sku:{sku}'
    for barcode in (variant.get('barcodes') or {}).get('nodes', []):
        value = normalize_catalog_code(barcode.get('value'))
        if value:
            yield f'barcode:{value}'


def _page(connection, after):
    if not isinstance(connection, dict) or not isinstance(connection.get('nodes'), list):
        raise DeferredChannelError('shopify_catalog_page_incomplete')
    page = connection.get('pageInfo') or {}
    if page.get('hasNextPage') is False:
        return connection['nodes'], False
    cursor = page.get('endCursor')
    if page.get('hasNextPage') is not True or not cursor or cursor == after or not connection['nodes']:
        raise DeferredChannelError('shopify_catalog_page_incomplete')
    return connection['nodes'], cursor


def product_ids(client, after=None, query_filter=None):
    data = client.query(CATALOG_PRODUCTS, {'after': after or None, 'filter': query_filter or None})
    nodes, cursor = _page(data.get('products'), after)
    ids = [node.get('id') for node in nodes]
    if any(not PRODUCT_ID.fullmatch(gid or '') for gid in ids) or len(ids) != len(set(ids)):
        raise DeferredChannelError('shopify_catalog_page_incomplete')
    return ids, cursor


def fetch_product(client, product_id):
    """Read a complete product before using the absence of a variant as evidence."""
    cursor = False
    pages = set()
    variants = {}
    snapshot = None
    while True:
        result = client.query(CATALOG_PRODUCT, {'id': product_id, 'after': cursor or None})
        if 'product' not in result:
            raise DeferredChannelError('shopify_catalog_page_incomplete')
        product = result['product']
        if product is None and not cursor:
            return None
        if not product or product.get('id') != product_id or not product.get('updatedAt'):
            raise DeferredChannelError('shopify_catalog_product_changed')
        if snapshot is None:
            snapshot = {key: value for key, value in product.items() if key != 'variants'}
        elif product['updatedAt'] != snapshot['updatedAt']:
            raise DeferredChannelError('shopify_catalog_product_changed')
        nodes, cursor = _page(product.get('variants'), cursor)
        for variant in nodes:
            gid = variant.get('id') or ''
            if not VARIANT_ID.fullmatch(gid) or gid in variants:
                raise DeferredChannelError('shopify_catalog_product_changed')
            variants[gid] = variant
        if not cursor:
            if not variants:
                raise DeferredChannelError('shopify_catalog_page_incomplete')
            return dict(snapshot, variants=list(variants.values()))
        if cursor in pages:
            raise DeferredChannelError('shopify_catalog_page_incomplete')
        pages.add(cursor)
