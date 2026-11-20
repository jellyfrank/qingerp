from contextlib import closing
from datetime import timedelta
from itertools import islice
from uuid import uuid4

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.addons.qing_base.tools.channel_queue import locked_job_environment
from odoo.addons.qing_channel.errors import ChannelReviewRequired, DeferredChannelError, retry_delay

from ..services.bulk import BULK_ID, BULK_STATUS, RECENT_BULKS, START_BULK, bulk_orders_query, iter_bulk_rows
from ..services.client import MUTATION_NOT_EXECUTED_CODES
from ..services.orders import parse_date


BATCH_SIZE = 500
RUNNING_STATUSES = {'CREATED', 'RUNNING', 'CANCELING'}
ACTIVE_STATES = ('queued', 'running')


class ShopifyBulkImport(models.Model):
    _name = 'qing.shopify.bulk.import'
    _description = 'Shopify Bulk Order History'
    _order = 'id desc'
    _rec_name = 'store_id'
    _check_company_auto = True

    store_id = fields.Many2one('qing.channel.store', required=True, ondelete='restrict', index=True)
    company_id = fields.Many2one(related='store_id.company_id', store=True, index=True, precompute=True)
    start_action_id = fields.Many2one('qing.channel.action', check_company=True, ondelete='restrict', copy=False)
    remote_id = fields.Char(copy=False, readonly=True)
    window_from = fields.Datetime(required=True, readonly=True)
    window_to = fields.Datetime(required=True, readonly=True)
    state = fields.Selection([('queued', 'Waiting for Shopify'), ('running', 'Importing'),
                              ('done', 'Complete'), ('review', 'Needs Review')],
                             default='queued', required=True, readonly=True, index=True)
    queued_orders = fields.Integer(copy=False, readonly=True)
    expected_orders = fields.Integer(copy=False, readonly=True)
    byte_offset = fields.Char(default='0', required=True, copy=False, readonly=True)
    last_issue = fields.Char(copy=False, readonly=True)
    retry_at = fields.Datetime(copy=False, index=True)
    tries = fields.Integer(copy=False)

    _active_store_unique = models.UniqueIndex("(store_id) WHERE state IN ('queued', 'running')")
    _remote_unique = models.UniqueIndex('(store_id, remote_id) WHERE remote_id IS NOT NULL')

    @api.model
    def _cron_shopify_bulk_import(self):
        due = [('store_id.active', '=', True), ('store_id.shopify_health', '=', 'connected'),
               ('state', 'in', ACTIVE_STATES),
               '|', ('retry_at', '=', False), ('retry_at', '<=', fields.Datetime.now())]
        for run in self.sudo().search(due, limit=20, order='id'):
            key = f'qing-shopify-scan:{run.store_id.id}'
            with locked_job_environment(self.env.registry, key) as work:
                if work is not None:
                    work[self._name].browse(run.id).with_company(run.company_id)._poll_batch()

    def _poll_batch(self):
        self.ensure_one()
        if not self.store_id._channel_worker_ready():
            return
        if self.state == 'queued':
            if self.start_action_id.state in ('review', 'obsolete'):
                self._review('shopify_bulk_start_requires_review')
                self.env.cr.commit()
            return
        if self.state != 'running':
            return
        try:
            with self.env.cr.savepoint():
                self._import_next_batch()
        except ChannelReviewRequired as exc:
            if self.store_id._channel_operation_failed(exc.code):
                self._review(exc.code)
        except DeferredChannelError as exc:
            if exc.retry_after is None:
                self.tries += 1
            if self.tries >= 8:
                self._review(exc.code)
            else:
                self.write({'retry_at': fields.Datetime.now() + retry_delay(self.tries, exc.retry_after),
                            'last_issue': exc.code})
        self.env.cr.commit()

    def _import_next_batch(self):
        client = self.store_id._shopify_client()
        remote = client.query(BULK_STATUS, {'id': self.remote_id}).get('bulkOperation')
        if not remote or remote.get('id') != self.remote_id:
            raise DeferredChannelError('shopify_bulk_status_unavailable')
        status = remote['status']
        if status in RUNNING_STATUSES:
            self.write({'tries': 0, 'retry_at': False, 'last_issue': False})
            return
        if status != 'COMPLETED':
            raise ChannelReviewRequired({
                'FAILED': 'shopify_bulk_failed',
                'CANCELED': 'shopify_bulk_cancelled',
                'EXPIRED': 'shopify_bulk_expired',
            }.get(status, 'shopify_bulk_status_unrecognized'))
        try:
            expected = int(remote['rootObjectCount'])
            size = int(remote.get('fileSize') or 0)
        except (KeyError, ValueError, TypeError):
            raise ChannelReviewRequired('shopify_bulk_result_metadata_invalid') from None
        if expected < 0 or size < 0:
            raise ChannelReviewRequired('shopify_bulk_result_metadata_invalid')
        if not expected and not size and not remote.get('url') and not self.queued_orders:
            self._complete_import()
            return
        if not expected or not size or not remote.get('url'):
            raise ChannelReviewRequired('shopify_bulk_result_metadata_invalid')

        offset, queued = int(self.byte_offset), self.queued_orders
        if offset > size or queued > expected or (queued and self.expected_orders != expected):
            raise ChannelReviewRequired('shopify_bulk_result_metadata_changed')
        delivery = self.env['qing.channel.delivery']
        if offset < size:
            with closing(iter_bulk_rows(remote['url'], offset, size)) as rows:
                for offset, (external_id, stamp) in islice(rows, BATCH_SIZE):
                    try:
                        updated_at = parse_date(stamp)
                    except (ValueError, OverflowError, TypeError):
                        raise ChannelReviewRequired('shopify_bulk_result_row_invalid') from None
                    if not updated_at or updated_at < self.window_from:
                        raise ChannelReviewRequired('shopify_bulk_result_row_outside_window')
                    delivery._receive(self.store_id, f'pull:{external_id}:{stamp}', external_id, 'orders/reconcile')
                    queued += 1
        if queued > expected or ((queued == expected) != (offset == size)):
            raise ChannelReviewRequired('shopify_bulk_result_count_mismatch')
        self.write({'queued_orders': queued, 'expected_orders': expected, 'byte_offset': str(offset),
                    'tries': 0, 'retry_at': False, 'last_issue': False})
        if offset == size:
            self._complete_import()

    def _complete_import(self):
        cursors = self.env['qing.channel.cursor']
        cursor = cursors.search([('store_id', '=', self.store_id.id), ('stream', '=', 'orders')], limit=1)
        if not cursor:
            cursors.create({'store_id': self.store_id.id, 'stream': 'orders', 'through': self.window_to})
        elif self.window_from <= cursor.through <= self.window_to:
            cursor._finish_window(self.window_to)
        self.write({'state': 'done', 'tries': 0, 'retry_at': False, 'last_issue': False})

    def _review(self, code):
        self.write({'state': 'review', 'last_issue': code, 'retry_at': False})
        self.env['qing.channel.exception']._flag(self.store_id, self, code)

    def action_recheck(self):
        self.ensure_one()
        self.check_access('read')
        self.store_id._check_channel_manager()
        run = self.sudo()
        store = run.store_id
        store.lock_for_update(allow_referencing=True)
        if run.state != 'review' or store.shopify_health != 'connected' or store.onboarded:
            raise UserError(_('Reconnect the Shopify store and keep inventory paused before rechecking this import.'))
        if store._shopify_active_bulk_import():
            raise UserError(_('A bulk import is already active for this Shopify store.'))
        reset = {'tries': 0, 'retry_at': False, 'last_issue': False}
        if run.remote_id:
            run.write(dict(reset, state='running'))
        else:
            request = run.start_action_id.request_ids
            if request and request.state not in ('review', 'obsolete'):
                raise UserError(_('The original Shopify export request is already being checked.'))
            run.write(dict(reset, state='queued'))
            if request:
                run.start_action_id.state = 'external'
                request.write({'state': 'verify' if request.first_sent_at else 'ready', 'checks': 0, 'retry_at': False})
            else:
                run.start_action_id.write({'state': 'pending', 'tries': 0, 'retry_at': False})
        exceptions = self.env['qing.channel.exception'].sudo().search([
            ('store_id', '=', store.id), ('object_model', '=', self._name), ('object_id', '=', run.id),
            ('resolved', '=', False),
        ])
        exceptions.resolved = True
        return True


class ShopifyStoreBulkImport(models.Model):
    _inherit = 'qing.channel.store'

    def _shopify_active_bulk_import(self):
        return self.env['qing.shopify.bulk.import'].sudo().search([
            ('store_id', '=', self.id), ('state', 'in', ACTIVE_STATES),
        ], limit=1)

    def action_shopify_start_bulk_history(self):
        self._check_channel_manager()
        store = self.sudo()
        store.lock_for_update(allow_referencing=True)
        if store.platform != 'shopify' or store.shopify_health != 'connected' or store.onboarded:
            raise UserError(_('Connect Shopify and keep inventory publishing paused before importing bulk history.'))
        if store._shopify_active_bulk_import():
            raise UserError(_('A bulk import is already active for this Shopify store.'))
        unconfirmed = store.env['qing.channel.request'].search_count([
            ('store_id', '=', store.id), ('operation', '=', 'shopify_bulk_start'), ('first_sent_at', '!=', False),
            ('confirmed_rejection', '=', False), ('state', 'in', ('uncertain', 'verify', 'review')),
        ], limit=1)
        if unconfirmed:
            raise UserError(_('Recheck the previous Shopify export request before starting a new one.'))
        now = fields.Datetime.now()
        try:
            start = store._shopify_history_start(now)
        except ChannelReviewRequired:
            raise UserError(_('Shopify approval for historical orders is required for the configured start date.')) from None
        through = now - timedelta(minutes=2)
        if through < start:
            raise UserError(_('Choose a history start before the current reconciliation window.'))
        run = store.env['qing.shopify.bulk.import'].create({
            'store_id': store.id, 'window_from': start, 'window_to': through,
        })
        query = bulk_orders_query(f'QingBulkOrders{uuid4().hex}', start, through)
        run.start_action_id = store.env['qing.channel.action']._ensure_action(
            store, f'bulk-history:{run.id}', 'bulk_history', data={'run': run.id, 'query': query},
        )
        action = self.env['ir.actions.act_window']._for_xml_id('qing_shopify.action_shopify_bulk_import')
        action['domain'] = [('store_id', '=', self.id)]
        return action

    def _execute_channel_action(self, action):
        if self.platform != 'shopify' or action.kind != 'bulk_history':
            return super()._execute_channel_action(action)
        if self.shopify_health != 'connected':
            raise DeferredChannelError('shopify_disconnected')
        run = self.env['qing.shopify.bulk.import'].browse(action.data['run']).exists()
        if not run or run.store_id != self or run.state != 'queued' or self.onboarded:
            return 'obsolete'
        self.env['qing.channel.request']._prepare_request(
            action, 'shopify_bulk_start', 'bulk-orders', action.data,
        )
        return 'external'

    def _execute_channel_request(self, request, recovering):
        if self.platform != 'shopify' or request.operation != 'shopify_bulk_start':
            return super()._execute_channel_request(request, recovering)
        if self.shopify_health != 'connected':
            raise DeferredChannelError('shopify_disconnected')
        run = self.env['qing.shopify.bulk.import'].browse(request.payload['run']).exists()
        if not run or run.store_id != self or run.state != 'queued' or self.onboarded:
            if request.first_sent_at and not request.confirmed_rejection:
                raise ChannelReviewRequired('shopify_bulk_remote_result_unconfirmed')
            return 'obsolete'
        client = self._shopify_client()
        if recovering and not request.confirmed_rejection:
            term = f"operation_type:query AND created_at:>='{(request.first_sent_at - timedelta(minutes=1)).isoformat()}Z'"
            recent = client.query(RECENT_BULKS, {'term': term})['bulkOperations']['nodes']
            matches = [row for row in recent if row['type'] == 'QUERY' and row['query'] == request.payload['query']]
            if len(matches) != 1:
                raise DeferredChannelError('shopify_bulk_remote_result_unconfirmed')
            remote_id = matches[0]['id']
        else:
            request._mark_before_send()
            try:
                result = client.query(START_BULK, {'document': request.payload['query']})['bulkOperationRunQuery']
            except DeferredChannelError as exc:
                if exc.code in MUTATION_NOT_EXECUTED_CODES:
                    request._record_remote_rejection(exc.code)
                raise
            remote_id = (result.get('bulkOperation') or {}).get('id')
            if not remote_id and result.get('userErrors'):
                request._record_remote_rejection('shopify_bulk_start_rejected')
                raise ChannelReviewRequired('shopify_bulk_start_rejected')
        if not remote_id or not BULK_ID.fullmatch(remote_id):
            raise DeferredChannelError('shopify_bulk_remote_result_unconfirmed')
        run.write({'state': 'running', 'remote_id': remote_id, 'last_issue': False})
        request.remote_reference = remote_id
        request._log_attempt('shopify_bulk_export_confirmed')
        return 'success'
