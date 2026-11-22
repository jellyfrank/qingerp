import re
from datetime import timedelta
from uuid import uuid4

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.fields import Domain
from odoo.tools.translate import LazyTranslate
from odoo.addons.qing_channel.errors import ChannelReviewRequired, DeferredChannelError
from odoo.addons.qing_base.tools.channel_queue import locked_job_environment

from ..services.client import (
    CUSTOMER_ACCESS_DENIED, MUTATION_NOT_EXECUTED_CODES, SHOP_HOST, TOKEN_RENEWAL_WINDOW, ShopifyClient,
    ShopifyProtectedCustomerDataDenied,
)
from ..services.credentials import open_secret, seal, storage_ready
from ..services.orders import ORDER_PAGE, fetch_order, parse_date


_lt = LazyTranslate(__name__)

EXCEPTION_TITLES = {
    'shopify_credentials_required': _lt('Shopify app credentials are missing'),
    'shopify_scope_denied': _lt('Shopify app permissions are missing'),
    'shopify_sku_missing': _lt('Shopify SKU and barcode are missing'),
    'shopify_product_mapping_required': _lt('Shopify product mapping is missing'),
    'shopify_stock_product_mapping_required': _lt('Shopify inventory mapping is missing'),
    'shopify_product_codes_ambiguous': _lt('Shopify product codes conflict'),
    'shopify_pricelist_currency_mismatch': _lt('The pricelist currency does not match the Shopify order'),
    'shopify_page_cursor_invalid_after_restart': _lt('Shopify order synchronization needs to be retried'),
    CUSTOMER_ACCESS_DENIED: _lt('Shopify app lacks access to protected customer data'),
}

REQUIRED_SCOPES = {
    'read_orders', 'read_customers', 'read_products', 'read_inventory', 'write_inventory',
    'read_locations', 'read_merchant_managed_fulfillment_orders', 'write_merchant_managed_fulfillment_orders',
}
DISCONNECT_CODES = {
    'shopify_access_denied', 'shopify_scope_denied', 'shopify_credentials_required',
    'shopify_token_unavailable', 'shopify_api_version_mismatch', 'shopify_credential_key_unavailable',
    'shopify_credential_requires_reentry', 'shopify_credential_key_mismatch',
    'shopify_token_refresh_failed', 'shopify_app_uninstalled', CUSTOMER_ACCESS_DENIED,
    'shopify_app_installation_unavailable', 'shopify_shop_identity_or_currency_mismatch', 'shopify_location_unavailable',
    'shopify_connection_response_incomplete', 'shopify_history_scope_required', 'shopify_pilot_warehouse_unavailable',
}
WINDOW_LIMIT = 24000
LOCATION_ID = re.compile(r'gid://shopify/Location/[0-9]+\Z')
HEALTH = [('setup', 'Setup'), ('connected', 'Connected'), ('disconnected', 'Disconnected')]
INVOICE_POLICY = [('shipment', 'Invoice Delivered Quantities'), ('capture', 'Invoice on Capture')]
CONNECTION_QUERY = '''
query ChannelConnection($location: ID!) {
  shop { myshopifyDomain currencyCode }
  location(id: $location) { id isActive }
  currentAppInstallation { accessScopes { handle } }
  customers(first: 1) { nodes { id } }
  orders(first: 5, sortKey: CREATED_AT, reverse: true) {
    nodes { id email phone shippingAddress { firstName lastName address1 phone } }
  }
}
'''


class ShopifyScanException(models.Model):
    _inherit = 'qing.channel.exception'

    def _get_exception_title(self):
        title = EXCEPTION_TITLES.get(self.code)
        return self.env._(title) if title else super()._get_exception_title()

    @api.depends('code', 'object_id', 'store_id.platform')
    def _compute_can_recheck(self):
        super()._compute_can_recheck()

    def _supports_recheck(self):
        return super()._supports_recheck() or (
            self.object_model == 'qing.channel.store'
            and self.code == 'shopify_page_cursor_invalid_after_restart'
            and self.object_id == self.store_id.id
            and self.store_id.platform == 'shopify'
        )

    def action_recheck(self):
        self.ensure_one()
        self.check_access('read')
        if self.object_model != 'qing.channel.store' or self.code != 'shopify_page_cursor_invalid_after_restart':
            return super().action_recheck()
        store = self.store_id
        store._check_channel_manager()
        if self.resolved or self.object_id != store.id or store.platform != 'shopify':
            raise UserError(_('This Shopify reconciliation exception is no longer active.'))
        self.env.cr.execute('SELECT pg_try_advisory_xact_lock(hashtextextended(%s, 0))',
                            [f'qing-shopify-scan:{store.id}'])
        if not self.env.cr.fetchone()[0]:
            raise UserError(_('Wait for the current Shopify order scan to finish, then recheck.'))
        cursor = self.env['qing.channel.cursor'].sudo().search([
            ('store_id', '=', store.id), ('stream', '=', 'orders'),
        ], limit=1)
        if cursor.window_start and cursor.window_end and cursor.page_restarts:
            cursor.write({'page_cursor': False, 'page_rows': 0, 'page_restarts': 0})
        self.sudo().resolved = True
        return True


class ChannelStore(models.Model):
    _inherit = 'qing.channel.store'

    @api.constrains('platform', 'shopify_domain', 'shopify_location')
    def _check_shopify_connection_values(self):
        for rec in self.filtered(lambda store: store.platform == 'shopify'):
            if not SHOP_HOST.fullmatch(rec.shopify_domain or ''):
                raise ValidationError(_('Enter the canonical myshopify.com domain.'))
            if rec.shopify_location and not LOCATION_ID.fullmatch(rec.shopify_location):
                raise ValidationError(_('Enter a valid Shopify location identifier.'))

    def _shopify_client(self):
        return ShopifyClient(self)

    def _shopify_decrypt(self, secret_field):
        self.ensure_one()
        if secret_field not in ('shopify_secret', 'shopify_token'):
            raise ValueError('Invalid Shopify credential field')
        return open_secret(self.sudo()[secret_field])

    def _compute_shopify_credential_storage_ready(self):
        self.shopify_credential_storage_ready = storage_ready()

    @api.model
    def _shopify_prepare_values(self, vals):
        vals = dict(vals)
        location = vals.get('shopify_location')
        if isinstance(location, str):
            location = location.strip()
            if location.isascii() and location.isdecimal():
                location = f'gid://shopify/Location/{location}'
            vals['shopify_location'] = location
        try:
            for field in ('shopify_secret', 'shopify_token'):
                if field in vals:
                    vals[field] = seal(vals[field])
        except ChannelReviewRequired as exc:
            raise UserError(_('Shopify credential encryption is unavailable on this QingERP instance. Ask the system administrator to enable it before saving the Shopify client secret.')) from exc
        return vals

    def _check_channel_manager(self):
        self.ensure_one()
        self.check_access('write')
        if not self.env.user.has_group('qing_channel.group_channel_manager'):
            raise UserError(_('Channel Manager access is required.'))

    def _import_delivery(self, delivery):
        if self.platform != 'shopify':
            return super()._import_delivery(delivery)
        if delivery.topic == 'app/uninstalled':
            uninstalled_at = delivery.occurred_at or delivery.received_at
            if not self.shopify_connected_at or uninstalled_at >= self.shopify_connected_at:
                self._record_shopify_failure('shopify_app_uninstalled')
            return
        if self.shopify_health != 'connected':
            raise DeferredChannelError('shopify_disconnected')
        order = fetch_order(self._shopify_client(), delivery.external_id)
        self._apply_shopify_order(order)

    @api.model
    def _cron_shopify_renew_credentials(self):
        renew_before = fields.Datetime.now() + TOKEN_RENEWAL_WINDOW
        domain = [
            ('platform', '=', 'shopify'), ('active', '=', True), ('shopify_health', '=', 'connected'),
            '|', ('shopify_token_until', '=', False), ('shopify_token_until', '<=', renew_before),
        ]
        for store in self.sudo().search(domain):
            with locked_job_environment(self.env.registry, f'qing-shopify-renew:{store.id}') as env:
                if env is None:
                    continue
                shop = env[self._name].browse(store.id).exists()
                if not shop or not shop.active or shop.shopify_health != 'connected':
                    continue
                try:
                    shop._shopify_client()._token(renew_before=renew_before)
                except DeferredChannelError as exc:
                    if exc.code != 'shopify_token_refresh_in_progress':
                        shop._record_shopify_failure(exc.code)
                env.cr.commit()

    @api.model
    def _cron_shopify_pull(self):
        domain = [('platform', '=', 'shopify'), ('active', '=', True), ('shopify_health', '=', 'connected')]
        for store in self.sudo().search(domain):
            with locked_job_environment(self.env.registry, f'qing-shopify-scan:{store.id}') as env:
                if env is None:
                    continue
                shop = env[self._name].browse(store.id).with_company(store.company_id)
                try:
                    shop._scan_shopify_orders()
                    env.cr.commit()
                except (DeferredChannelError, ChannelReviewRequired) as exc:
                    env.cr.rollback()
                    if exc.code not in MUTATION_NOT_EXECUTED_CODES:
                        shop._record_shopify_failure(exc.code)
                    env.cr.commit()

    def _channel_worker_domain(self):
        shopify_available = Domain('platform', '!=', 'shopify') | Domain('shopify_health', '=', 'connected')
        return super()._channel_worker_domain() & shopify_available

    def _channel_operation_failed(self, code):
        if self.platform == 'shopify' and code in DISCONNECT_CODES:
            self._record_shopify_failure(code)
        return super()._channel_operation_failed(code)

    def _record_shopify_failure(self, code):
        if code in DISCONNECT_CODES:
            self.sudo().write({'shopify_health': 'disconnected', 'shopify_token': False,
                               'shopify_token_until': False, 'shopify_scopes': False,
                               'onboarded': False, 'shopify_open_orders_verified': False})
        self.env['qing.channel.exception'].sudo()._flag(self, self, code)

    def _shopify_history_start(self, now):
        floor = now - timedelta(days=60)
        date = self.shopify_history_from
        scopes = set((self.shopify_scopes or '').split(', '))
        if date and date < floor and 'read_all_orders' not in scopes:
            raise ChannelReviewRequired('shopify_history_scope_required')
        return date or floor

    def _scan_shopify_orders(self):
        self.ensure_one()
        if self._shopify_active_bulk_import():
            return
        Cursor = self.env['qing.channel.cursor']
        domain = [('store_id', '=', self.id), ('stream', '=', 'orders')]
        cursor = Cursor.search(domain, limit=1)
        now = fields.Datetime.now()
        if cursor:
            lower = cursor.window_start or (cursor.through - timedelta(minutes=10))
            if not cursor.window_start and self.shopify_history_from:
                lower = max(lower, self.shopify_history_from)
        else:
            lower = self._shopify_history_start(now)
        upper = cursor.window_end or (now - timedelta(minutes=2))
        if upper < lower:
            return
        if not cursor:
            cursor = Cursor.create({'store_id': self.id, 'stream': 'orders', 'through': lower})
        if (cursor.window_start, cursor.window_end) != (lower, upper):
            cursor.write({
                'window_start': lower, 'window_end': upper,
                'page_cursor': False, 'page_rows': 0, 'page_restarts': 0,
            })
        after = cursor.page_cursor or None
        term = f"updated_at:>='{lower.isoformat()}Z' AND updated_at:<='{upper.isoformat()}Z'"
        delivery = self.env['qing.channel.delivery']
        client = self._shopify_client()
        for _page in range(4):
            try:
                connection = client.query(ORDER_PAGE, {'term': term, 'after': after})['orders']
            except DeferredChannelError as exc:
                if exc.code != 'shopify_page_cursor_invalid' or not after:
                    raise
                return self._restart_shopify_order_page(cursor)
            page = connection['pageInfo']
            next_cursor = page.get('endCursor')
            if page['hasNextPage'] and (not next_cursor or next_cursor == after or not connection['nodes']):
                if not after:
                    raise ChannelReviewRequired('shopify_missing_page_cursor')
                return self._restart_shopify_order_page(cursor)
            for order in connection['nodes']:
                external_id, stamp = order['id'], order['updatedAt']
                updated_at = parse_date(stamp)
                if not updated_at:
                    raise ChannelReviewRequired('shopify_order_index_timestamp_missing')
                if lower <= updated_at <= upper:
                    delivery._receive(self, f'pull:{external_id}:{stamp}', external_id, 'orders/reconcile')
            if not page['hasNextPage']:
                cursor._finish_window(upper)
                return
            after = next_cursor
            rows = cursor.page_rows + len(connection['nodes'])
            if rows >= WINDOW_LIMIT:
                reduced = (lower + (upper - lower) / 2).replace(microsecond=0)
                if reduced <= cursor.through:
                    raise ChannelReviewRequired('shopify_dense_reconciliation_window')
                cursor.write({'window_end': reduced, 'page_cursor': False, 'page_rows': 0, 'page_restarts': 0})
                return
            cursor.write({'page_cursor': after, 'page_rows': rows})
            self.env.cr.commit()

    def _restart_shopify_order_page(self, cursor):
        if cursor.page_restarts:
            raise ChannelReviewRequired('shopify_page_cursor_invalid_after_restart')
        cursor.write({'page_cursor': False, 'page_rows': 0, 'page_restarts': 1})

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su and any(set(vals) & {'shopify_connected_at', 'shopify_health', 'shopify_scopes',
                                                'shopify_token', 'shopify_token_until', 'onboarded',
                                                'shopify_webhook_key'}
                                   for vals in vals_list):
            raise AccessError(_('Use the Shopify connection and cutover actions to change synchronisation state.'))
        for vals in vals_list:
            if vals.get('platform') == 'shopify':
                vals['shopify_webhook_key'] = uuid4().hex
        return super().create([self._shopify_prepare_values(vals) for vals in vals_list])

    def write(self, vals):
        protected = {'shopify_connected_at', 'shopify_health', 'shopify_scopes', 'shopify_token', 'shopify_token_until',
                     'onboarded', 'shopify_webhook_key'}
        if not self.env.su and protected & vals.keys():
            raise AccessError(_('Use the Shopify connection and cutover actions to change synchronisation state.'))
        if {'company_id', 'platform'} & vals.keys() and any(
            record.platform == 'shopify' and (vals.get('company_id', record.company_id.id) != record.company_id.id
                                              or vals.get('platform', record.platform) != record.platform)
            for record in self
        ):
            raise UserError(_('A Shopify link cannot be moved to a different company or platform.'))
        if 'shopify_history_from' in vals and any(
            store._shopify_active_bulk_import() for store in self if store.platform == 'shopify'
        ):
            raise UserError(_('Wait for the Shopify bulk import to finish before changing the history start.'))
        if 'shopify_domain' in vals and self.filtered(lambda record: record.shopify_domain
                                                       and record.shopify_domain != vals['shopify_domain']):
            raise UserError(_('Create a new channel store for a different Shopify domain.'))
        if {'cutover_at', 'warehouse_id', 'shopify_location', 'shopify_currency_id', 'shopify_history_from'} & vals.keys():
            if any(record.onboarded for record in self):
                raise UserError(_('Pause Shopify inventory publishing before changing cutover parameters.'))
        if {'shopify_client_id', 'shopify_secret', 'shopify_domain'} & vals.keys():
            vals = dict(vals, shopify_health='setup', shopify_token=False, shopify_token_until=False,
                        onboarded=False, shopify_open_orders_verified=False)
        result = super().write(self._shopify_prepare_values(vals))
        cutover_fields = {'cutover_at', 'warehouse_id', 'shopify_location', 'shopify_history_from',
                          'shopify_client_id', 'shopify_secret', 'shopify_open_orders_verified'}
        if cutover_fields & vals.keys():
            self.env['qing.channel.binding'].sudo().search([
                ('store_id', 'in', self.filtered(lambda store: store.platform == 'shopify').ids),
                ('kind', '=', 'inventory'), ('shopify_preview_at', '!=', False),
            ]).write({'shopify_preview_at': False})
        if 'shopify_history_from' in vals:
            self.env['qing.channel.cursor'].sudo().search([
                ('store_id', 'in', self.ids), ('stream', '=', 'orders')
            ]).unlink()
        return result

    platform = fields.Selection(selection_add=[('shopify', 'Shopify')], ondelete={'shopify': 'set default'})
    shopify_apply_tax_rounding = fields.Boolean(groups='base.group_system', copy=False)
    shopify_client_id = fields.Char(groups='base.group_system', copy=False)
    shopify_connected_at = fields.Datetime(readonly=True, copy=False)
    shopify_credential_storage_ready = fields.Boolean(
        compute='_compute_shopify_credential_storage_ready', groups='base.group_system',
    )
    shopify_currency_id = fields.Many2one('res.currency', ondelete='restrict')
    shopify_domain = fields.Char(copy=False)
    shopify_health = fields.Selection(HEALTH, default='setup', readonly=True, copy=False)
    shopify_history_from = fields.Datetime(copy=False)
    shopify_invoice_policy = fields.Selection(INVOICE_POLICY, default='shipment', required=True)
    shopify_location = fields.Char(
        copy=False, help='Paste the location number from Shopify Settings > Locations, or the full Shopify location ID.',
    )
    shopify_open_orders_verified = fields.Boolean(copy=False)
    shopify_pricelist_id = fields.Many2one('product.pricelist', check_company=True, ondelete='restrict')
    shopify_safety_stock = fields.Integer(default=0)
    shopify_scopes = fields.Char(readonly=True, copy=False)
    shopify_secret = fields.Char(groups='base.group_system', copy=False)
    shopify_token = fields.Char(groups='base.group_system', readonly=True, copy=False)
    shopify_token_until = fields.Datetime(groups='base.group_system', readonly=True, copy=False)
    shopify_webhook_key = fields.Char(groups='base.group_system', readonly=True, copy=False)

    _shopify_domain_unique = models.UniqueIndex("(shopify_domain) WHERE platform = 'shopify'")

    def _shopify_connection_notice(self, message, notification_type='info'):
        return {
            'type': 'ir.actions.client', 'tag': 'display_notification',
            'params': {
                'message': message, 'type': notification_type, 'sticky': False,
                'next': {'type': 'ir.actions.client', 'tag': 'soft_reload'},
            },
        }

    def _shopify_connection_failed(self, code, message):
        self._record_shopify_failure(code)
        return self._shopify_connection_notice(message, 'danger')

    def action_shopify_check_connection(self):
        self._check_channel_manager()
        if self.platform != 'shopify':
            return False
        if not self.shopify_currency_id or not self.shopify_location or not self.cutover_at:
            raise UserError(_('Set the shop currency, location and inventory cutover before connecting.'))
        if self.warehouse_id.delivery_steps != 'ship_only':
            return self._shopify_connection_failed(
                'shopify_pilot_warehouse_unavailable',
                _('Choose a warehouse configured for single-step delivery for the Shopify pilot.'),
            )
        labels = {'name': _('name'), 'address': _('address'), 'email': _('email'), 'phone': _('phone')}
        checked_at = fields.Datetime.now()
        try:
            data = self._shopify_client().query(CONNECTION_QUERY, {'location': self.shopify_location})
        except ShopifyProtectedCustomerDataDenied as exc:
            if 'customer' in exc.denied_fields:
                return self._shopify_connection_failed(
                    exc.code,
                    _('Shopify denied access to protected customer data. Enable it for the app and check again.'),
                )
            denied = ', '.join(label for category, label in labels.items() if category in exc.denied_fields)
            return self._shopify_connection_failed(exc.code, _('Shopify has denied access to customer fields: %s', denied))
        except DeferredChannelError as exc:
            return self._shopify_connection_failed(exc.code, _('Shopify connection failed: %s', exc.code))
        installation = data.get('currentAppInstallation')
        access = installation.get('accessScopes') if isinstance(installation, dict) else None
        if not isinstance(access, list) or any(
            not isinstance(item, dict) or not isinstance(item.get('handle'), str) for item in access
        ):
            return self._shopify_connection_failed(
                'shopify_app_installation_unavailable', _('Shopify did not confirm the app installation and granted permissions.'),
            )
        scopes = {item['handle'] for item in access}
        missing = REQUIRED_SCOPES - scopes
        if missing:
            return self._shopify_connection_failed(
                'shopify_scope_denied', _('Missing Shopify scopes: %s', ', '.join(sorted(missing))),
            )
        shop = data.get('shop')
        if not isinstance(shop, dict) or (
            shop.get('myshopifyDomain'), shop.get('currencyCode')
        ) != (self.shopify_domain, self.shopify_currency_id.name):
            return self._shopify_connection_failed(
                'shopify_shop_identity_or_currency_mismatch',
                _('The authorised shop or shop currency differs from the configured store.'),
            )
        location = data.get('location')
        if not isinstance(location, dict) or location.get('id') != self.shopify_location or location.get('isActive') is not True:
            return self._shopify_connection_failed(
                'shopify_location_unavailable', _('The configured Shopify location is unavailable.'),
            )
        if (self.shopify_history_from and self.shopify_history_from < checked_at - timedelta(days=60)
                and 'read_all_orders' not in scopes):
            return self._shopify_connection_failed(
                'shopify_history_scope_required',
                _('Shopify only grants access to the last 60 days. Adjust the import date or grant read_all_orders.'),
            )
        customers = data.get('customers')
        if not isinstance(customers, dict) or not isinstance(customers.get('nodes'), list):
            return self._shopify_connection_failed(
                'shopify_connection_response_incomplete',
                _('Shopify did not return complete customer access information.'),
            )
        connection = data.get('orders')
        orders = connection.get('nodes') if isinstance(connection, dict) else None
        if not isinstance(orders, list) or any(
            not isinstance(item, dict) or not isinstance(item.get('shippingAddress') or {}, dict)
            for item in orders
        ):
            return self._shopify_connection_failed(
                'shopify_connection_response_incomplete', _('Shopify did not return complete order access information.'),
            )
        self.sudo().write({'shopify_connected_at': checked_at, 'shopify_health': 'connected',
                           'shopify_scopes': ', '.join(sorted(scopes))})
        issues = self.env['qing.channel.exception'].sudo().search([
            ('store_id', '=', self.id), ('object_model', '=', self._name), ('object_id', '=', self.id),
            ('code', 'in', sorted(DISCONNECT_CODES | {'shopify_protected_customer_fields_missing'})),
            ('resolved', '=', False),
        ])
        issues.filtered(lambda issue: issue.code in DISCONNECT_CODES).resolved = True
        if not orders:
            return self._shopify_connection_notice(
                _('Connected. Create a test order to verify protected customer data.'), 'warning',
            )
        missing_by_order = []
        for sample in orders:
            address = sample.get('shippingAddress') or {}
            fields_present = {
                'name': address.get('firstName') or address.get('lastName'), 'address': address.get('address1'),
                'email': sample.get('email'), 'phone': sample.get('phone') or address.get('phone'),
            }
            missing_by_order.append([labels[field] for field, present in fields_present.items() if not present])
        missing_fields = min(missing_by_order, key=len)
        if missing_fields:
            self._record_shopify_failure('shopify_protected_customer_fields_missing')
            return self._shopify_connection_notice(
                _('Connected. Customer fields unverified on the sample order: %s. '
                  'Create a sample with those fields and check again.', ', '.join(missing_fields)),
                'warning',
            )
        issues.filtered(lambda issue: issue.code == 'shopify_protected_customer_fields_missing').resolved = True
        return self._shopify_connection_notice(_('Shopify connection and sample customer fields verified.'))
