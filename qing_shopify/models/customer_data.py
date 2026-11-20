import json
from datetime import timedelta

from odoo import api, fields, models
from odoo.addons.qing_base.tools.channel_queue import snapshot_digest
from odoo.addons.qing_channel.errors import ChannelReviewRequired

from ..services.credentials import open_secret, seal
from ..services.orders import CUSTOMER_QUERY


CUSTOMER_KEYS = ('customer', 'email', 'phone', 'billingAddress', 'shippingAddress')


class ShopifyCustomerAuditLog(models.Model):
    _inherit = 'qing.audit.log'

    operation = fields.Selection(selection_add=[('read', 'Read')], ondelete={'read': 'cascade'})


class ShopifyOrderCustomerData(models.Model):
    _inherit = 'qing.channel.order'

    shopify_customer_blob = fields.Text(copy=False, readonly=True, groups='base.group_system')
    shopify_customer_expires_at = fields.Datetime(copy=False, readonly=True, index=True)

    @api.model
    def _split_shopify_customer_data(self, snapshot):
        customer = {key: snapshot.get(key) for key in CUSTOMER_KEYS}
        public = {key: value for key, value in snapshot.items() if key not in CUSTOMER_KEYS}
        for key in ('billingAddress', 'shippingAddress'):
            address = customer[key] or {}
            public[key] = {field: address.get(field) for field in ('countryCodeV2', 'provinceCode')} if address else None
        public['shipping_address_hash'] = snapshot_digest(customer['shippingAddress'])
        public['customer_data_hash'] = snapshot_digest(customer)
        return public, customer

    def _retain_shopify_customer_data(self, customer):
        self.ensure_one()
        if self.snapshot.get('cancelledAt') or not (customer['shippingAddress'] or customer['billingAddress']):
            return
        person_hash = self.snapshot['customer_data_hash']
        if (self.shopify_customer_blob and self.shopify_customer_expires_at
                and self.shopify_customer_expires_at > fields.Datetime.now()
                and self.shopify_customer_blob.startswith(person_hash + '|')):
            return
        days = self.env['ir.config_parameter'].sudo().get_int('qing_shopify.customer_retention_days', 30)
        cipher = seal(json.dumps(customer, ensure_ascii=False, sort_keys=True))
        self.write({'shopify_customer_blob': person_hash + '|' + cipher,
                    'shopify_customer_expires_at': fields.Datetime.now() + timedelta(days=max(1, days))})

    def _read_shopify_customer_data(self):
        self.ensure_one()
        saved_hash, _separator, encoded = (self.shopify_customer_blob or '').partition('|')
        if (encoded and self.shopify_customer_expires_at and self.shopify_customer_expires_at > fields.Datetime.now()
                and saved_hash == self.snapshot['customer_data_hash']):
            data = json.loads(open_secret(encoded))
        else:
            data = self._fetch_shopify_customer_data()
        self._qing_audit_insert('read', [(self.id, ['Shopify customer details'], '{}')])
        return data

    def _fetch_shopify_customer_data(self):
        data = self.store_id._shopify_client().query(CUSTOMER_QUERY, {'id': self.external_id}).get('order')
        if not data:
            raise ChannelReviewRequired('shopify_customer_fields_unavailable')
        customer = {key: data.get(key) for key in CUSTOMER_KEYS}
        if snapshot_digest(customer) != self.snapshot['customer_data_hash']:
            raise ChannelReviewRequired('shopify_customer_snapshot_requires_refresh')
        return customer

    @api.model
    def _cron_shopify_purge_customer_data(self):
        domain = [('shopify_customer_expires_at', '<=', fields.Datetime.now())]
        self = self.sudo()
        while True:
            expired = self.search(domain, limit=1000, order='shopify_customer_expires_at, id')
            expired.write({'shopify_customer_blob': False, 'shopify_customer_expires_at': False})
            remaining = int(len(expired) == 1000)
            time_left = self.env['ir.cron']._commit_progress(processed=len(expired), remaining=remaining)
            if not remaining or not time_left:
                break
