from datetime import timedelta
from math import isfinite
import re
from time import sleep

import requests

from odoo import fields
from odoo.addons.qing_base.tools.channel_queue import snapshot_digest
from odoo.addons.qing_channel.errors import ChannelReviewRequired, DeferredChannelError


SHOP_HOST = re.compile(r'[a-z0-9][a-z0-9-]{0,62}\.myshopify\.com\Z')
API_VERSION = '2026-10'
TIMEOUT = (4, 10)
TOKEN_PATH = '/admin/oauth/access_token'
TOKEN_RENEWAL_WINDOW = timedelta(minutes=15)
MUTATION_NOT_EXECUTED_CODES = {'shopify_throttled', 'shopify_token_refresh_in_progress'}
CUSTOMER_ACCESS_DENIED = 'shopify_protected_customer_fields_denied'
PROTECTED_FIELD_DENIAL = re.compile(r'^This app is not approved to use the ([a-z0-9_.]+) field\b', re.IGNORECASE)
PROTECTED_CUSTOMER_OBJECT_DENIAL = re.compile(
    r'^This app is not approved to access the Customer object\b', re.IGNORECASE,
)
PROTECTED_FIELDS = {
    'firstname': 'name', 'lastname': 'name',
    'shippingaddress': 'address', 'billingaddress': 'address', 'address1': 'address', 'address2': 'address',
    'city': 'address', 'zip': 'address', 'provincecode': 'address', 'countrycodev2': 'address',
    'email': 'email', 'emailaddress': 'email', 'defaultemailaddress': 'email',
    'phone': 'phone', 'phonenumber': 'phone', 'defaultphonenumber': 'phone',
}


class ShopifyProtectedCustomerDataDenied(ChannelReviewRequired):
    def __init__(self, denied_fields):
        super().__init__(CUSTOMER_ACCESS_DENIED)
        self.denied_fields = denied_fields


def _graphql_error_code(error):
    extensions = error.get('extensions')
    code = extensions.get('code') if isinstance(extensions, dict) else None
    return code if isinstance(code, str) else None


def _protected_fields_denied(errors):
    denied = set()
    for error in errors:
        message = str(error.get('message') or '')
        if PROTECTED_CUSTOMER_OBJECT_DENIAL.match(message):
            denied.add('customer')
            continue
        match = PROTECTED_FIELD_DENIAL.match(message)
        if match:
            field = match[1].rsplit('.', 1)[-1].lower()
        elif _graphql_error_code(error) == 'ACCESS_DENIED':
            path = error.get('path')
            field = path[-1].lower() if isinstance(path, list) and path and isinstance(path[-1], str) else ''
        else:
            continue
        if category := PROTECTED_FIELDS.get(field):
            denied.add(category)
    return denied


def _invalid_page_cursor(error):
    code = _graphql_error_code(error)
    message = str(error.get('message') or '').casefold()
    return code == 'INVALID_CURSOR' or any(phrase in message for phrase in (
        'invalid cursor', 'invalid pagination cursor', 'cursor is invalid', 'malformed cursor',
    ))


class ShopifyClient:
    def __init__(self, store):
        self.store = store.sudo()
        if not SHOP_HOST.fullmatch(store.shopify_domain or ''):
            raise ChannelReviewRequired('invalid_shopify_domain')
        self.root = 'https://' + store.shopify_domain

    def _post(self, path, payload, headers=None):
        try:
            response = requests.post(self.root + path, json=payload, headers=headers, timeout=TIMEOUT,
                                     allow_redirects=False)
            if response.status_code in (401, 403) or (path == TOKEN_PATH and response.status_code == 400):
                raise ChannelReviewRequired('shopify_access_denied')
            if response.status_code == 429:
                try:
                    delay = float(response.headers.get('Retry-After', 2))
                except ValueError:
                    delay = 2
                raise DeferredChannelError('shopify_throttled', retry_after=delay if isfinite(delay) and delay >= 1 else 2)
            if response.status_code != 200:
                raise DeferredChannelError('shopify_http_error')
            if path.startswith('/admin/api/') and response.headers.get('X-Shopify-API-Version') != API_VERSION:
                raise ChannelReviewRequired('shopify_api_version_mismatch')
            return response.json()
        except (requests.RequestException, ValueError):
            raise DeferredChannelError('shopify_transport_unknown') from None

    def _token(self, renew_before=None):
        store = self.store
        now = fields.Datetime.now()
        renew_before = renew_before or now
        if store.shopify_token and store.shopify_token_until and store.shopify_token_until > renew_before:
            return store._shopify_decrypt('shopify_token')
        store.env.cr.execute('SELECT pg_try_advisory_xact_lock(hashtextextended(%s, 0))',
                             [f'qing-shopify-token:{store.id}'])
        if not store.env.cr.fetchone()[0]:
            raise DeferredChannelError('shopify_token_refresh_in_progress', retry_after=5)
        if not store.shopify_client_id or not store.shopify_secret:
            raise ChannelReviewRequired('shopify_credentials_required')
        try:
            data = self._post(TOKEN_PATH, {
                'grant_type': 'client_credentials', 'client_id': store.shopify_client_id,
                'client_secret': store._shopify_decrypt('shopify_secret'),
            })
        except ChannelReviewRequired:
            raise
        except DeferredChannelError as exc:
            if exc.retry_after is not None or (store.shopify_token_until and store.shopify_token_until > now):
                raise
            raise ChannelReviewRequired('shopify_token_refresh_failed') from None
        if not isinstance(data, dict):
            raise ChannelReviewRequired('shopify_token_unavailable')
        token = data.get('access_token')
        try:
            seconds = min(86400, int(data.get('expires_in'))) - 300
        except (OverflowError, TypeError, ValueError):
            raise ChannelReviewRequired('shopify_token_unavailable') from None
        if not isinstance(token, str) or not token.strip() or seconds <= 0:
            raise ChannelReviewRequired('shopify_token_unavailable')
        store.write({'shopify_token': token, 'shopify_token_until': now + timedelta(seconds=seconds)})
        return token

    def query(self, document, variables=None):
        variables = variables or {}
        budget = self.store.env['qing.shopify.api.budget']
        key = snapshot_digest([API_VERSION, document, {key: value for key, value in variables.items()
                                                       if isinstance(value, bool)}])
        token = self._token()
        try:
            cost = budget._reserve(self.store.id, key)
        except DeferredChannelError as exc:
            # A short wait avoids restarting an order snapshot halfway through its linked queries.
            if exc.retry_after is None or exc.retry_after > 2:
                raise
            sleep(exc.retry_after)
            cost = budget._reserve(self.store.id, key)
        try:
            response = self._post(f'/admin/api/{API_VERSION}/graphql.json',
                                  {'query': document, 'variables': variables}, {'X-Shopify-Access-Token': token})
        except DeferredChannelError as exc:
            if exc.code == 'shopify_throttled':
                budget._pause(self.store.id, exc.retry_after)
            raise
        if not isinstance(response, dict):
            raise DeferredChannelError('shopify_missing_data')
        delay = budget._observe(self.store.id, key, cost, response)
        errors = response.get('errors') or []
        if errors:
            if not isinstance(errors, list) or not all(isinstance(error, dict) for error in errors):
                raise DeferredChannelError('shopify_graphql_error')
            if denied := _protected_fields_denied(errors):
                raise ShopifyProtectedCustomerDataDenied(denied)
            codes = {_graphql_error_code(error) for error in errors}
            if 'ACCESS_DENIED' in codes:
                raise ChannelReviewRequired('shopify_scope_denied')
            if 'MAX_COST_EXCEEDED' in codes:
                raise ChannelReviewRequired('shopify_graphql_query_cost_exceeded')
            if 'THROTTLED' in codes:
                if delay is None:
                    delay = 2
                    budget._pause(self.store.id, delay)
                raise DeferredChannelError('shopify_throttled', retry_after=delay)
            if 'IDEMPOTENCY_CONCURRENT_REQUEST' in codes:
                raise DeferredChannelError('shopify_request_running')
            if 'IDEMPOTENCY_KEY_PARAMETER_MISMATCH' in codes:
                raise ChannelReviewRequired('shopify_idempotency_mismatch')
            if variables.get('after') and all(_invalid_page_cursor(error) for error in errors):
                raise DeferredChannelError('shopify_page_cursor_invalid')
            raise DeferredChannelError('shopify_graphql_error')
        if not isinstance(response.get('data'), dict):
            raise DeferredChannelError('shopify_missing_data')
        return response['data']
