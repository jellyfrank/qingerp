import base64
from datetime import timedelta
from io import BytesIO
from zipfile import ZipFile

from psycopg2 import IntegrityError
from psycopg2.errors import SerializationFailure

from odoo import SUPERUSER_ID, _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools.binary import BinaryBytes

from ..protocol import ProtocolError, event_summary, invoice_identity, validate_payload, validate_result

ORDER_STATES = [
    ('queued', 'Queued'), ('pending', 'Provider Processing'), ('unknown', 'Outcome Unknown'),
    ('issued', 'Issued'), ('rejected', 'Definitively Rejected'), ('cancelled', 'Cancelled Before Sending'),
]
TASK_STATES = [(key, 'Cancelled' if key == 'cancelled' else label) for key, label in ORDER_STATES]
REGISTRATION_STATES = [('pending', 'Pending Registration'), ('done', 'Registered'), ('error', 'Registration Error')]
PENDING_ACTIONS = [('submit', 'Submit Invoice'), ('query', 'Query Result'), ('documents', 'Fetch Documents')]
POLL_INTERVAL = timedelta(minutes=5)
LOCK_NAMESPACE = 6849202


class FapiaoOrder(models.Model):
    _name = 'qing.fapiao.order'
    _description = 'Invoice Provider Order'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'id desc'
    _rec_name = 'request_key'
    _check_company_auto = True

    _request_unique = models.Constraint('UNIQUE(company_id, request_key)', 'The invoice request identifier already exists.')

    @api.depends('result')
    def _compute_has_pdf(self):
        for order in self:
            order.has_pdf = any(document.get('mimetype') == 'application/pdf' and document.get('data')
                                for invoice in (order.result or {}).get('invoices', [])
                                for document in invoice.get('files', []))

    @api.depends('result')
    def _compute_pdf_file(self):
        for order in self:
            documents = [document for invoice in (order.result or {}).get('invoices', [])
                         for document in invoice.get('files', [])
                         if document.get('mimetype') == 'application/pdf' and document.get('data')]
            order.pdf_file = False
            order.pdf_filename = False
            if len(documents) == 1:
                order.pdf_file = BinaryBytes(base64.b64decode(documents[0]['data']), filename=documents[0]['name'])
                order.pdf_filename = documents[0]['name']
            elif documents:
                stream = BytesIO()
                with ZipFile(stream, 'w') as archive:
                    for index, document in enumerate(documents, 1):
                        archive.writestr(f"{index}-{document['name']}", base64.b64decode(document['data']))
                order.pdf_file = BinaryBytes(stream.getvalue(), filename='invoices.zip')
                order.pdf_filename = 'invoices.zip'

    def _check_manager(self):
        if not self.env.su and not self.env.user.has_group('qing_fapiao.group_fapiao_manager'):
            raise AccessError(_('Only invoice interface managers may perform this operation.'))
        self.check_access('read')
        if not self.env.su and any(order.company_id != self.env.company for order in self):
            raise AccessError(_('Switch to the invoice order company before continuing.'))

    def _lock(self):
        if self:
            self.flush_recordset()
            self.env.cr.execute('SELECT id FROM qing_fapiao_order WHERE id IN %s ORDER BY id FOR UPDATE', [tuple(sorted(self.ids))])
            self.env.cr.execute('UPDATE qing_fapiao_order SET revision = revision + 1 WHERE id IN %s', [tuple(self.ids)])
            self.invalidate_recordset()
        return self

    def _write_internal(self, vals):
        """Trusted Python extension point; deliberately unavailable to RPC/context flags."""
        return super().write(vals)

    @api.model
    def _create_order(self, provider, payload):
        provider.ensure_one()
        provider._check_manager()
        Currency = self.env['res.currency']
        domain = [('name', '=', payload.get('currency'))] if isinstance(payload, dict) else [('id', '=', 0)]
        currency = Currency.with_context(active_test=False).search(domain, limit=1)
        if not currency:
            raise ValidationError(_('Unknown invoice currency.'))
        try:
            snapshot = validate_payload(payload, currency.decimal_places)
        except ProtocolError as error:
            raise ValidationError(str(error)) from error
        # Freeze the provider row before the task is visible, including concurrent account edits.
        self.env.cr.execute('SELECT id FROM qing_fapiao_provider WHERE id = %s FOR UPDATE', [provider.id])
        self.env.cr.execute('UPDATE qing_fapiao_provider SET revision = revision + 1 WHERE id = %s', [provider.id])
        provider.invalidate_recordset()
        lock_key = f'{provider.company_id.id}:{snapshot["request_key"]}'
        self.env.cr.execute('SELECT pg_advisory_xact_lock(%s, hashtext(%s))', [LOCK_NAMESPACE, lock_key])
        domain = [('company_id', '=', provider.company_id.id), ('request_key', '=', snapshot['request_key'])]
        existing = self.search(domain, limit=1)
        if existing:
            if existing.provider_id != provider or existing.snapshot != snapshot:
                raise ValidationError(_('The request identifier is already bound to another provider or payload.'))
            return existing
        provider._check_order(snapshot)
        clean = self.with_context({key: value for key, value in self.env.context.items() if not key.startswith('default_')})
        try:
            with self.env.cr.savepoint():
                order = super(FapiaoOrder, clean).create({
                    'company_id': provider.company_id.id, 'currency_id': currency.id,
                    'provider_id': provider.id, 'request_key': snapshot['request_key'],
                    'snapshot': snapshot, 'provider_snapshot': {
                        'code': provider.code, 'company_id': provider.company_id.id,
                        'environment': provider.environment, 'seller_tax_id': provider.seller_tax_id,
                    },
                    'state': 'queued', 'pending_action': 'submit',
                    'next_attempt_at': fields.Datetime.now(),
                    'registration_state': 'pending',
                })
        except IntegrityError as error:
            if error.diag.constraint_name == 'qing_fapiao_order_request_unique':
                raise SerializationFailure('Concurrent invoice request creation; retry with a fresh snapshot.') from error
            raise
        order._append_event('queued', {'schema_version': snapshot['schema_version']})
        return order

    def _append_event(self, kind, payload=None):
        self.ensure_one()
        return self.env['qing.fapiao.event']._append(self, kind, payload)

    def _check_provider_identity(self):
        self.ensure_one()
        provider = self.provider_id
        provider.invalidate_recordset()
        actual = {
            'code': provider.code, 'company_id': provider.company_id.id,
            'environment': provider.environment,
        }
        expected = {key: self.provider_snapshot[key] for key in actual}
        if actual != expected:
            raise ValidationError(_('The provider routing configuration no longer matches the frozen invoice task.'))

    def _check_submit_allowed(self):
        """Business bridges may additionally recheck approvals while the order row is locked."""
        self.ensure_one()
        self.provider_id._check_existing_order(self)

    def _apply_result(self, result):
        self.ensure_one()
        self._lock()
        self._check_provider_identity()
        try:
            normalized = validate_result(result, self.snapshot, self.currency_id.decimal_places)
        except ProtocolError as error:
            raise ValidationError(str(error)) from error
        status = normalized['status']
        if self.state in ('queued', 'cancelled'):
            raise ValidationError(_('An unsent or cancelled invoice task cannot accept a provider result.'))
        if self.state == 'issued':
            if status != 'issued':
                self._append_event('ignored', event_summary(normalized))
                return False
            if invoice_identity(normalized) != invoice_identity(self.result):
                raise ValidationError(_('The issued invoice identity or amount changed in a later result.'))
            # File-poor repeated callbacks must never erase already obtained documents.
            previous = {(item.get('code', ''), item['number']): item for item in self.result['invoices']}
            for invoice in normalized['invoices']:
                if not invoice.get('files'):
                    invoice['files'] = previous[(invoice.get('code', ''), invoice['number'])].get('files', [])
        if self.state == 'rejected':
            if status == 'rejected':
                return False
            raise ValidationError(_('A conflicting result followed a definitive provider rejection. Investigate with the provider.'))
        values = {
            'state': status, 'result': normalized,
            'external_id': normalized.get('external_id') or self.external_id,
            'last_error': False,
            'pending_action': 'query' if status in ('pending', 'unknown') and not self.processing_cancelled else False,
            'next_attempt_at': fields.Datetime.now() + POLL_INTERVAL if status in ('pending', 'unknown') and not self.processing_cancelled else False,
        }
        if self.external_id and normalized.get('external_id') and self.external_id != normalized['external_id']:
            raise ValidationError(_('The provider reference changed for an existing invoice task.'))
        if status in ('issued', 'rejected'):
            values['processing_cancelled'] = False
        if status == 'issued':
            values['issued_at'] = self.issued_at or fields.Datetime.now()
        self._write_internal(values)
        self._append_event('result', event_summary(normalized))
        return True

    def _record_business_result(self):
        """A business bridge performs atomic, idempotent local registration here."""
        raise UserError(_('No business registration adapter is installed for this invoice task.'))

    def _process_notifications(self):
        self.ensure_one()
        Event = self.env['qing.fapiao.event']
        domain = [('order_id', '=', self.id), ('kind', '=', 'notification'), ('processed', '=', False)]
        for event in Event.search(domain, order='id'):
            try:
                with self.env.cr.savepoint():
                    self._apply_result(event.payload)
            except (UserError, ValidationError):
                self._write_internal({'last_error': _('A provider notification conflicts with this task; inspect the audit events.')})
                self._append_event('error', {'operation': 'notification', 'event_id': event.id})
            event._mark_processed()

    def _prepare_attempt(self):
        """Persist this phase before calling any adapter; a crash thereafter is query-only."""
        self.ensure_one()
        self._lock()
        self._process_notifications()
        operation = self.pending_action
        if self.processing_cancelled or not operation or (self.next_attempt_at and self.next_attempt_at > fields.Datetime.now()):
            return False
        self._check_provider_identity()
        if operation == 'submit':
            if self.state != 'queued' or self.submitted_at:
                operation = 'query'
            else:
                self._check_submit_allowed()
        if operation == 'documents' and self.state != 'issued':
            raise ValidationError(_('Invoice documents can only be fetched for issued tasks.'))
        values = {
            'attempt_count': self.attempt_count + 1,
            'last_attempt_at': fields.Datetime.now(),
            'pending_action': 'query' if operation != 'documents' else 'documents',
            'next_attempt_at': fields.Datetime.now() + POLL_INTERVAL,
        }
        if operation == 'submit':
            values.update({'state': 'unknown', 'submitted_at': fields.Datetime.now()})
        self._write_internal(values)
        self._append_event('attempt', {'operation': operation, 'attempt': values['attempt_count']})
        return operation

    @api.model
    def _process_one_order(self, order_id):
        """Worker-only independent transaction boundary, never called by an interactive action."""
        registry = self.env.registry
        # Session-level lock survives the explicit phase commits and prevents concurrent workers.
        with registry.cursor() as cr:
            cr.execute('SELECT pg_try_advisory_lock(%s, %s)', [LOCK_NAMESPACE, order_id])
            if not cr.fetchone()[0]:
                return False
            try:
                env = api.Environment(cr, SUPERUSER_ID, {})
                order = env[self._name].browse(order_id).exists()
                if not order:
                    return False
                order = order.with_company(order.company_id)
                try:
                    with cr.savepoint():
                        operation = order._prepare_attempt()
                except (UserError, ValidationError):
                    order._write_internal({'last_error': _('Task validation failed before calling the provider.'), 'pending_action': False})
                    order._append_event('error', {'operation': 'preflight'})
                    cr.commit()
                    return False
                cr.commit()
                if operation:
                    try:
                        provider = order.provider_id
                        method = {'submit': '_submit_invoice', 'query': '_query_invoice', 'documents': '_fetch_documents'}[operation]
                        response = getattr(provider, method)(order)
                        with cr.savepoint():
                            order._apply_result(response)
                    except Exception as error:
                        # Never expose raw HTTP exceptions (URLs, credentials, response bodies).
                        cr.rollback()
                        order.invalidate_recordset()
                        order._lock()
                        order._write_internal({'last_error': _('The provider outcome is not confirmed. Query the result before any further action.')})
                        order._append_event('error', {'operation': operation, 'error_type': type(error).__name__})
                    cr.commit()
                order.invalidate_recordset()
                if order.state == 'issued' and order.registration_state == 'pending':
                    order.action_record_result()
                    cr.commit()
                return True
            finally:
                cr.rollback()
                cr.execute('SELECT pg_advisory_unlock(%s, %s)', [LOCK_NAMESPACE, order_id])
                cr.commit()

    @api.model
    def _cron_process_orders(self, limit=20):
        """Discover work in a fresh cursor so the scheduler never commits its caller's transaction."""
        with self.env.registry.cursor() as cr:
            cr.execute('''
                SELECT DISTINCT orders.id FROM qing_fapiao_order orders
                LEFT JOIN qing_fapiao_event event ON event.order_id = orders.id
                    AND event.kind = 'notification' AND NOT event.processed
                WHERE (orders.pending_action IS NOT NULL AND orders.next_attempt_at <= NOW())
                    OR (orders.state = 'issued' AND orders.registration_state = 'pending')
                    OR event.id IS NOT NULL
                ORDER BY orders.id LIMIT %s
            ''', [min(max(int(limit), 1), 100)])
            ids = [row[0] for row in cr.fetchall()]
        for order_id in ids:
            for retry in range(3):
                try:
                    self._process_one_order(order_id)
                    break
                except SerializationFailure:
                    if retry == 2:
                        self._store_worker_error(order_id, 'concurrent_update')
                except Exception:
                    self._store_worker_error(order_id, 'worker_error')
                    break

    @api.model
    def _store_worker_error(self, order_id, code):
        # Failure of one provider/order must not prevent other companies from processing.
        try:
            with self.env.registry.cursor() as cr:
                env = api.Environment(cr, SUPERUSER_ID, {})
                order = env[self._name].browse(order_id).exists()
                if order:
                    order._lock()
                    values = {'last_error': _('Background processing failed. Review the task and retry the safe next operation.')}
                    if order.state == 'issued' and order.registration_state != 'done':
                        values.update({'registration_state': 'error', 'registration_error': values['last_error']})
                    order._write_internal(values)
                    order._append_event('error', {'operation': code})
                    cr.commit()
        except Exception:
            # The next cron run still discovers durable pending/unknown work after a DB outage.
            return False
        return True

    @api.depends('state', 'processing_cancelled')
    def _compute_task_state(self):
        for order in self:
            order.task_state = 'cancelled' if order.processing_cancelled and order.state in ('pending', 'unknown') else order.state

    attempt_count = fields.Integer(readonly=True, default=0)
    company_id = fields.Many2one('res.company', required=True, readonly=True, index=True)
    currency_id = fields.Many2one('res.currency', required=True, readonly=True)
    event_ids = fields.One2many('qing.fapiao.event', 'order_id', readonly=True)
    external_id = fields.Char(readonly=True, index=True)
    has_pdf = fields.Boolean(string='PDF Available', compute='_compute_has_pdf', store=True)
    issued_at = fields.Datetime(readonly=True)
    last_attempt_at = fields.Datetime(readonly=True)
    last_error = fields.Text(readonly=True)
    next_attempt_at = fields.Datetime(readonly=True, index=True)
    pdf_file = fields.Binary(string='Invoice PDF', compute='_compute_pdf_file', attachment=False)
    pdf_filename = fields.Char(compute='_compute_pdf_file')
    pending_action = fields.Selection(PENDING_ACTIONS, readonly=True)
    processing_cancelled = fields.Boolean(string='Automatic Processing Cancelled', readonly=True)
    processing_cancelled_at = fields.Datetime(string='Task Cancelled At', readonly=True)
    processing_cancelled_by = fields.Many2one('res.users', string='Task Cancelled By', readonly=True)
    provider_id = fields.Many2one('qing.fapiao.provider', required=True, readonly=True, ondelete='restrict', check_company=True, index=True)
    provider_snapshot = fields.Json(required=True, readonly=True)
    registration_error = fields.Text(readonly=True)
    registration_state = fields.Selection(REGISTRATION_STATES, readonly=True, default='pending', required=True, index=True)
    request_key = fields.Char(required=True, readonly=True, index=True)
    result = fields.Json(readonly=True)
    revision = fields.Integer(readonly=True, default=0)
    snapshot = fields.Json(required=True, readonly=True)
    state = fields.Selection(ORDER_STATES, required=True, readonly=True, default='queued', index=True)
    submitted_at = fields.Datetime(readonly=True)
    task_state = fields.Selection(TASK_STATES,
                                  string='Task Status', compute='_compute_task_state', store=True, index=True)

    @api.model_create_multi
    def create(self, vals_list):
        raise AccessError(_('Invoice tasks can only be created by an approved business workflow.'))

    def action_download_pdf(self):
        self.ensure_one()
        self._check_manager()
        if not self.has_pdf:
            raise UserError(_('The PDF is not available yet. Fetch the invoice documents first.'))
        return {
            'type': 'ir.actions.act_url',
            'url': f'/web/content/{self._name}/{self.id}/pdf_file?download=true&filename_field=pdf_filename',
            'target': 'self',
        }

    def write(self, vals):
        raise AccessError(_('Invoice tasks and their snapshots are immutable. Use the workflow actions.'))

    def unlink(self):
        if not self.env.su and not self.env.user.has_group('base.group_system'):
            raise AccessError(_('Only system administrators may delete cancelled or definitively rejected invoice tasks.'))
        self._check_manager()
        self.check_access('unlink')
        for order_id in sorted(self.ids):
            self.env.cr.execute('SELECT pg_try_advisory_xact_lock(%s, %s)', [LOCK_NAMESPACE, order_id])
            if not self.env.cr.fetchone()[0]:
                raise UserError(_('This task is being processed. Wait for the current operation to finish before deleting it.'))
        self._lock()
        if any(not (order.state in ('cancelled', 'rejected') or (order.processing_cancelled and order.state in ('pending', 'unknown')))
               or order.issued_at or (order.result or {}).get('invoices') or order.registration_state == 'done'
               for order in self):
            raise UserError(_('Only cancelled or definitively rejected tasks without issued invoices can be deleted.'))
        return super().unlink()

    def action_delete_cancelled(self):
        self.ensure_one()
        self.unlink()
        return {'type': 'ir.actions.act_window_close'}

    def action_queue(self):
        self.ensure_one()
        self._check_manager()
        self._lock()
        if self.state != 'queued' or self.submitted_at:
            raise UserError(_('This task was already submitted. Query its result instead.'))
        self._check_submit_allowed()
        self._write_internal({'pending_action': 'submit', 'next_attempt_at': fields.Datetime.now(), 'last_error': False})
        return True

    def action_query(self):
        self.ensure_one()
        self._check_manager()
        self._lock()
        if self.state not in ('pending', 'unknown', 'issued'):
            raise UserError(_('Only submitted invoice tasks can be queried.'))
        if not self.provider_id._get_capabilities().get('query'):
            raise UserError(_('This provider does not support result queries.'))
        if self.processing_cancelled:
            self._append_event('resumed', {'user_id': self.env.uid, 'operation': 'query'})
        self._write_internal({'processing_cancelled': False, 'pending_action': 'query', 'next_attempt_at': fields.Datetime.now()})
        return True

    def action_fetch_documents(self):
        self.ensure_one()
        self._check_manager()
        self._lock()
        if self.state != 'issued' or not self.provider_id._get_capabilities().get('documents'):
            raise UserError(_('Document retrieval is not available for this task.'))
        self._write_internal({'pending_action': 'documents', 'next_attempt_at': fields.Datetime.now()})
        return True

    def action_cancel(self):
        self.ensure_one()
        self._check_manager()
        self._lock()
        if self.state != 'queued' or self.submitted_at:
            raise UserError(_('Only an invoice task that has never been sent can be cancelled.'))
        self._write_internal({'state': 'cancelled', 'pending_action': False, 'next_attempt_at': False})
        self._append_event('cancelled')
        return True

    def action_cancel_processing(self):
        self.ensure_one()
        self._check_manager()
        self._lock()
        if self.state == 'queued' and not self.submitted_at:
            return self.action_cancel()
        if self.state not in ('pending', 'unknown'):
            raise UserError(_('Only unfinished invoice tasks can be cancelled.'))
        if self.processing_cancelled:
            return True
        self._write_internal({
            'processing_cancelled': True, 'processing_cancelled_at': fields.Datetime.now(),
            'processing_cancelled_by': self.env.uid, 'pending_action': False, 'next_attempt_at': False,
        })
        self._append_event('stopped', {'user_id': self.env.uid, 'provider_state': self.state})
        return True

    def action_record_result(self):
        self.ensure_one()
        self._check_manager()
        self._lock()
        if self.state != 'issued':
            raise UserError(_('Only an issued invoice can be registered in the business ledger.'))
        if self.registration_state == 'done':
            return True
        try:
            with self.env.cr.savepoint():
                result = self._record_business_result()
                self._write_internal({'registration_state': 'done', 'registration_error': False})
                self._append_event('registration', {'status': 'done'})
        except SerializationFailure:
            raise
        except Exception as error:
            message = str(error)[:2000] if isinstance(error, (UserError, ValidationError, AccessError)) else _('Business registration failed. Correct the cause and retry registration; do not issue another invoice.')
            self._write_internal({'registration_state': 'error', 'registration_error': message})
            self._append_event('registration', {'status': 'error', 'error_type': type(error).__name__})
            return False
        return result or True
