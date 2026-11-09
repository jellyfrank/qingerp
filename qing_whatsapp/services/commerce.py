"""Outbound commerce builders for future shop adapters; no sales/payment dependency."""
from urllib.parse import urlparse


def product_message(catalog_id, retailer_id):
    if not catalog_id or not retailer_id:
        raise ValueError('A catalog and retailer product ID are required')
    return {'type': 'product', 'action': {'catalog_id': str(catalog_id),
                                        'product_retailer_id': str(retailer_id)}}


def product_list(catalog_id, title, body, sections):
    if not catalog_id or not title or not body or not sections or len(sections) > 10:
        raise ValueError('Invalid product list')
    if not 1 <= sum(len(s.get('product_items', [])) for s in sections) <= 30:
        raise ValueError('Use 1 to 30 products')
    for section in sections:
        if not section.get('product_items') or any(not p.get('product_retailer_id') for p in section['product_items']):
            raise ValueError('Missing product identifier')
    return {'type': 'product_list', 'header': {'type': 'text', 'text': title},
            'body': {'text': body}, 'action': {'catalog_id': str(catalog_id), 'sections': sections}}


def flow_message(flow_id, token, label, screen, body, data=None):
    if not all([flow_id, token, label, screen, body]):
        raise ValueError('A published Flow, scoped token and starting screen are required')
    return {'type': 'flow', 'body': {'text': body}, 'action': {'name': 'flow', 'parameters': {
        'flow_message_version': '3', 'flow_id': str(flow_id), 'flow_token': token,
        'flow_cta': label, 'flow_action': 'navigate',
        'flow_action_payload': {'screen': screen, 'data': data or {}}}}}


def shop_link(url, body, label):
    parsed = urlparse(url)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('A public HTTPS shop URL is required')
    return {'type': 'cta_url', 'body': {'text': body},
            'action': {'name': 'cta_url', 'parameters': {'display_text': label, 'url': url}}}
