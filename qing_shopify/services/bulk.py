import json
import re
from urllib.parse import urlsplit

import requests

from odoo.addons.qing_channel.errors import ChannelReviewRequired, DeferredChannelError

from .client import TIMEOUT


START_BULK = '''
mutation ChannelStartBulk($document: String!) {
  bulkOperationRunQuery(query: $document, groupObjects: false) {
    bulkOperation { id }
    userErrors { code field }
  }
}
'''
RECENT_BULKS = '''
query ChannelRecentBulk($term: String!) {
  bulkOperations(first: 20, query: $term) { nodes { id query type } }
}
'''
BULK_STATUS = '''
query ChannelBulkStatus($id: ID!) {
  bulkOperation(id: $id) { id status rootObjectCount fileSize url }
}
'''
BULK_ID = re.compile(r'gid://shopify/BulkOperation/[0-9]+\Z')
ORDER_ID = re.compile(r'gid://shopify/Order/[0-9]+\Z')
BYTE_RANGE = re.compile(r'bytes ([0-9]+)-([0-9]+)/([0-9]+)\Z')
MAX_ROW_BYTES = 4096


def bulk_orders_query(operation_name, lower, upper):
    term = f"updated_at:>='{lower.isoformat()}Z' AND updated_at:<='{upper.isoformat()}Z'"
    return f'query {operation_name} {{ orders(query: {json.dumps(term)}, sortKey: UPDATED_AT) {{ edges {{ node {{ id updatedAt }} }} }} }}'


def _validate_url(url):
    if not isinstance(url, str):
        raise ChannelReviewRequired('shopify_bulk_result_url_invalid')
    try:
        parsed = urlsplit(url)
        host = parsed.hostname or ''
        if (parsed.scheme == 'https' and not parsed.username and not parsed.password
                and parsed.port in (None, 443) and not parsed.fragment
                and (host == 'storage.googleapis.com' or host.endswith('.storage.googleapis.com'))):
            return
    except ValueError:
        pass
    raise ChannelReviewRequired('shopify_bulk_result_url_invalid')


def iter_bulk_rows(url, offset, expected_size):
    _validate_url(url)
    headers = {'Accept-Encoding': 'identity'}
    if offset:
        headers['Range'] = f'bytes={offset}-'
    try:
        with requests.get(url, headers=headers, stream=True, timeout=TIMEOUT, allow_redirects=False) as response:
            if response.status_code in (403, 404, 429) or response.status_code >= 500:
                raise DeferredChannelError('shopify_bulk_result_unavailable')
            if response.headers.get('Content-Encoding', 'identity').lower() != 'identity':
                raise ChannelReviewRequired('shopify_bulk_result_encoding_unsupported')
            if offset and response.status_code == 206:
                content_range = BYTE_RANGE.fullmatch(response.headers.get('Content-Range', ''))
                if not content_range:
                    raise ChannelReviewRequired('shopify_bulk_result_range_invalid')
                start, stop, size = map(int, content_range.groups())
                if (start, stop, size) != (offset, expected_size - 1, expected_size):
                    raise ChannelReviewRequired('shopify_bulk_result_range_invalid')
                byte_position = start
            elif response.status_code == 200:
                byte_position = 0
            else:
                raise ChannelReviewRequired('shopify_bulk_result_response_invalid')
            if response.headers.get('Content-Length'):
                try:
                    length = int(response.headers['Content-Length'])
                except ValueError:
                    raise ChannelReviewRequired('shopify_bulk_result_size_invalid') from None
                if length != expected_size - byte_position:
                    raise ChannelReviewRequired('shopify_bulk_result_size_invalid')
            yield from _read_rows(response.iter_content(chunk_size=65536), byte_position, offset, expected_size)
    except requests.RequestException:
        raise DeferredChannelError('shopify_bulk_result_transport_unknown') from None


def _read_rows(chunks, position, offset, expected_size):
    buffer = b''
    for chunk in chunks:
        buffer += chunk
        while b'\n' in buffer:
            line, buffer = buffer.split(b'\n', 1)
            position += len(line) + 1
            if position <= offset:
                continue
            if position - len(line) - 1 < offset or position > expected_size:
                raise ChannelReviewRequired('shopify_bulk_result_range_invalid')
            yield position, _decode_row(line)
        if len(buffer) > MAX_ROW_BYTES:
            raise ChannelReviewRequired('shopify_bulk_result_row_invalid')
    if buffer and position >= offset and position + len(buffer) == expected_size:
        position += len(buffer)
        yield position, _decode_row(buffer)
    if position < expected_size:
        raise DeferredChannelError('shopify_bulk_result_incomplete')
    if position != expected_size:
        raise ChannelReviewRequired('shopify_bulk_result_size_invalid')


def _decode_row(line):
    if not line or len(line) > MAX_ROW_BYTES:
        raise ChannelReviewRequired('shopify_bulk_result_row_invalid')
    try:
        node = json.loads(line)
        if isinstance(node, dict) and ORDER_ID.fullmatch(node.get('id') or '') and isinstance(node.get('updatedAt'), str):
            return node['id'], node['updatedAt']
    except (TypeError, ValueError):
        pass
    raise ChannelReviewRequired('shopify_bulk_result_row_invalid')
