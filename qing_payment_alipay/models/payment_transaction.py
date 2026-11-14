from datetime import timedelta
import logging
import math
from urllib.parse import parse_qsl, urlsplit, urlunsplit
from uuid import uuid4

from requests.exceptions import RequestException

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.addons.payment import utils as payment_utils
from odoo.tools import SQL


_logger = logging.getLogger(__name__)
REFUND_PHASES = [('queued', 'Queued'), ('query', 'Awaiting confirmation'),
                 ('done', 'Refunded'), ('failed', 'Rejected')]
REFUND_IMMUTABLE_FIELDS = {
    'antom_refund_request_id', 'antom_refund_payload', 'antom_refund_client_id',
    'antom_refund_location', 'provider_id', 'source_transaction_id',
    'amount', 'currency_id', 'operation', 'is_live',
}


class PaymentTransaction(models.Model):
    _inherit = 'payment.transaction'

    def _get_specific_rendering_values(self, processing_values):
        if self.provider_code != 'alipay':
            return super()._get_specific_rendering_values(processing_values)
        url = self.provider_id._alipay_get_api_url(
            self.amount, self.reference, self.currency_id.name,
        )
        parts = urlsplit(url)
        if parts.scheme != 'https' or not parts.netloc:
            raise ValidationError(_("Alipay returned an invalid checkout URL."))
        return {
            'api_url': urlunsplit((parts.scheme, parts.netloc, parts.path, '', parts.fragment)),
            'params': parse_qsl(parts.query, keep_blank_values=True),
        }

    @api.model
    def _extract_reference(self, provider_code, payment_data):
        if provider_code != 'alipay':
            return super()._extract_reference(provider_code, payment_data)
        return payment_data.get('out_trade_no') or payment_data.get('paymentRequestId')

    def _extract_amount_data(self, payment_data):
        if self.provider_code != 'alipay':
            return super()._extract_amount_data(payment_data)
        if self.antom_refund_request_id or self.provider_id.is_global:
            amount = payment_data.get(
                'refundAmount' if self.operation == 'refund' else 'paymentAmount') or {}
            return {
                'amount': payment_utils.to_major_currency_units(
                    float(amount.get('value') or 0), self.currency_id,
                ),
                'currency_code': amount.get('currency'),
            }
        field = 'refund_fee' if self.operation == 'refund' else 'total_amount'
        return {'amount': float(payment_data.get(field) or 0), 'currency_code': 'CNY'}

    def _apply_updates(self, payment_data):
        if self.provider_code != 'alipay':
            return super()._apply_updates(payment_data)
        if self.antom_refund_request_id:
            return self._antom_apply_refund(payment_data)
        reference = self._extract_reference('alipay', payment_data)
        if reference != self.reference:
            raise ValidationError(_("The Alipay transaction reference does not match."))
        if self.state == 'done':
            return  # Do not demote a paid order or reset post-processing on duplicate callbacks.
        if self.provider_id.is_global:
            status = payment_data.get('paymentStatus')
            provider_reference = payment_data.get('paymentId')
            done, pending, canceled = {'SUCCESS'}, {'PAYMENT_IN_PROCESS'}, {'CANCELLED'}
        else:
            status = payment_data.get('trade_status')
            provider_reference = payment_data.get('trade_no')
            done = {'TRADE_SUCCESS', 'TRADE_FINISHED'}
            pending, canceled = {'WAIT_BUYER_PAY'}, {'TRADE_CLOSED'}
            if self.operation == 'refund':
                status = 'TRADE_SUCCESS' if payment_data.get('code') == '10000' else 'FAIL'
        if status in done:
            if not provider_reference:
                raise ValidationError(_("Alipay did not return a payment identifier."))
            self._validate_amount(payment_data)
            if self.state == 'error':
                return
            self.provider_reference = provider_reference
            self._set_done()
        elif status in pending:
            self._set_pending()
        elif status in canceled:
            self._set_canceled()
        else:
            self._set_error(
                payment_data.get('paymentResultMessage') or payment_data.get('sub_msg')
                or _("Alipay returned an unsupported payment status: %s", status),
            )

    def _alipay_query_and_record(self):
        """Queue server inquiry data; never trust browser status or amount."""
        self.ensure_one()
        if self.provider_code != 'alipay':
            return False
        if self.antom_refund_request_id:
            data = self._antom_refund_client().payment.inquiry_refund(
                refundRequestId=self.antom_refund_request_id)
            if data.get('result', {}).get('resultStatus') != 'S':
                return False  # Query failure is not refund failure.
            self._antom_record_refund(data, 'query')
            return True
        client = self.provider_id._get_alipay()
        # The domestic SDK wraps HTTP/JSON errors in a plain Exception. Translate
        # only the external call, never database writes or payment processing.
        try:
            data = (client.payment.inquiry(paymentRequestId=self.reference)
                    if self.provider_id.is_global
                    else client.pay.trade_query(out_trade_no=self.reference))
        except Exception as error:
            _logger.warning('Alipay inquiry failed for %s (%s).', self.reference, type(error).__name__)
            raise UserError(_("Alipay inquiry is temporarily unavailable. Please check payment status later; do not pay again.")) from error
        if not isinstance(data, dict):
            raise UserError(_("Alipay returned an invalid inquiry response. Please check payment status later."))
        if self.provider_id.is_global:
            if not isinstance(data.get('result'), dict):
                raise UserError(_("Antom returned an invalid inquiry result."))
            if data.get('result', {}).get('resultStatus') != 'S':
                return False
            reference_field = 'paymentRequestId'
        else:
            if data.get('code') != '10000':
                return False
            reference_field = 'out_trade_no'
        if data.get(reference_field, self.reference) != self.reference:
            raise ValidationError(_("The Alipay inquiry reference does not match."))
        self._record(dict(data, **{reference_field: self.reference}))
        return True

    def _send_refund_request(self):
        if self.provider_code != 'alipay':
            return super()._send_refund_request()
        self.ensure_one()
        if self.provider_id.is_global:
            # The child and its idempotency key must commit before any money moves.
            if not self.antom_refund_request_id:
                raise ValidationError(_("Create the Antom refund from its original payment."))
            self.with_context(payment_safe_write=True)._set_pending(
                state_message=_("Antom refund queued; awaiting provider confirmation."))
            self.env.ref('qing_payment_alipay.ir_cron_antom_refund')._trigger()
            return
        client = self.provider_id._get_alipay()
        data = client.pay.trade_refund(
            -self.amount, trade_no=self.source_transaction_id.provider_reference,
            out_request_no=self.reference,
        )
        # Alipay's out_trade_no is the original payment, not the refund child.
        self._record(dict(data, out_trade_no=self.reference))

    def _refund(self, amount_to_refund=None):
        self.ensure_one()
        if self.provider_code != 'alipay' or not self.provider_id.is_global:
            return super()._refund(amount_to_refund=amount_to_refund)
        self._ensure_provider_is_active()
        # UPDATE, not just SELECT FOR UPDATE: concurrent requests under PostgreSQL
        # repeatable-read must retry with a fresh snapshot before summing children.
        # ORM writes are deferred; execute the no-op UPDATE immediately. It changes
        # the MVCC row version without changing business/audit values.
        self.flush_recordset()
        self.env.cr.execute(SQL(
            'UPDATE payment_transaction SET write_date = write_date WHERE id = %s', self.id))
        if self.state != 'done' or self.operation == 'refund' or not self.provider_reference:
            raise ValidationError(_("Only a confirmed original Antom payment can be refunded."))
        if self.is_live != self.provider_id.is_live:
            raise ValidationError(_("Restore the payment provider's original live/sandbox mode."))
        amount = self.amount if amount_to_refund is None else amount_to_refund
        if not math.isfinite(amount) or amount <= 0 or self.currency_id.is_zero(amount):
            raise ValidationError(_("The refund amount must be strictly positive."))
        amount = self.currency_id.round(amount)
        Transaction = self.env['payment.transaction']
        domain = [('source_transaction_id', '=', self.id), ('operation', '=', 'refund')]
        children = Transaction.search(domain)
        # Unknown/malformed responses reserve funds even if another integration
        # changes the Odoo state. Only a verified final rejection releases them.
        reserved = sum(abs(tx.amount) for tx in children if (
            tx.antom_refund_request_id and tx.antom_refund_phase != 'failed'
            or not tx.antom_refund_request_id and tx.state not in ('error', 'cancel')))
        if self.currency_id.compare_amounts(amount + reserved, self.amount) > 0:
            raise ValidationError(_("Refund exceeds the remaining amount (including pending refunds)."))
        client = self.provider_id._get_global_instance(sandbox=not self.is_live)
        if not client.client_id:
            raise ValidationError(_("Configure the original Antom merchant client ID before refunding."))
        request_id = uuid4().hex
        payload = {
            'refundRequestId': request_id, 'paymentId': self.provider_reference,
            'refundAmount': {'currency': self.currency_id.name, 'value': str(
                payment_utils.to_minor_currency_units(amount, self.currency_id))},
            'refundNotifyUrl': self.provider_id.get_base_url().rstrip('/') + '/payment/alipay/refund/notify',
        }
        refund_tx = self._create_child_transaction(
            amount, is_refund=True, antom_refund_request_id=request_id,
            antom_refund_payload=payload, antom_refund_client_id=client.client_id,
            antom_refund_location=client.url, antom_refund_phase='queued',
            antom_refund_next_poll=fields.Datetime.now())
        refund_tx._log_sent_message()
        refund_tx._send_refund_request()
        return refund_tx

    def _antom_refund_client(self):
        """Use the original environment; never silently switch merchant or region."""
        self.ensure_one()
        client = self.provider_id._get_global_instance(sandbox=not self.is_live)
        if (not self.provider_id.is_global or client.client_id != self.antom_refund_client_id
                or client.url != self.antom_refund_location):
            raise ValidationError(_("Antom merchant or region changed. Restore the original configuration."))
        return client

    def _antom_validate_refund(self, data, origin):
        """Validate signed response identity before accepting even a rejection."""
        self.ensure_one()
        if not isinstance(data, dict) or not isinstance(data.get('result'), dict):
            raise ValidationError(_("Invalid Antom refund response."))
        result_status = data['result'].get('resultStatus')
        if origin not in ('submit', 'query', 'notify'):
            raise ValidationError(_("Invalid Antom refund response source."))
        status = ({'S': 'SUCCESS', 'F': 'FAIL', 'U': 'PROCESSING'}.get(result_status)
                  if origin == 'submit' else data.get('refundStatus'))
        if status not in ('SUCCESS', 'FAIL', 'PROCESSING'):
            raise ValidationError(_("Unsupported Antom refund status."))
        if origin == 'query' and result_status != 'S':
            raise ValidationError(_("Antom refund inquiry did not succeed."))
        if origin == 'notify' and (data.get('notifyType') != 'REFUND_RESULT'
                or (status, result_status) not in (('SUCCESS', 'S'), ('FAIL', 'F'))):
            raise ValidationError(_("Invalid Antom refund notification."))
        # Failed/unknown submission responses may omit all transaction fields.
        required = origin != 'submit' or status == 'SUCCESS'
        expected_id = self.antom_refund_request_id
        if (required or 'refundRequestId' in data) and data.get('refundRequestId') != expected_id:
            raise ValidationError(_("The Antom refund request does not match."))
        payment_id = self.antom_refund_payload['paymentId']
        if (origin == 'submit' and status == 'SUCCESS' or 'paymentId' in data
                ) and data.get('paymentId') != payment_id:
            raise ValidationError(_("The Antom original payment does not match."))
        if (required or 'refundAmount' in data
                ) and data.get('refundAmount') != self.antom_refund_payload['refundAmount']:
            raise ValidationError(_("The Antom refund amount or currency does not match."))
        refund_id = data.get('refundId')
        if required and (not isinstance(refund_id, str) or not refund_id or len(refund_id) > 64):
            raise ValidationError(_("Antom did not return a refund identifier."))
        if refund_id and self.provider_reference and refund_id != self.provider_reference:
            raise ValidationError(_("The Antom refund identifier changed."))
        return status

    def _antom_record_refund(self, data, origin):
        self._antom_validate_refund(data, origin)
        self._record({'antom_refund_origin': origin, 'antom_refund_data': data,
                      'refundAmount': self.antom_refund_payload['refundAmount']})

    def _antom_apply_refund(self, payment_data):
        data = payment_data.get('antom_refund_data') or {}
        status = self._antom_validate_refund(data, payment_data.get('antom_refund_origin'))
        if self.antom_refund_phase in ('done', 'failed'):
            return  # Terminal results are idempotent and never demoted by late messages.
        if data.get('refundId'):
            self.provider_reference = data['refundId']
        if status == 'SUCCESS':
            self.antom_refund_phase = 'done'
            self._set_done()
        elif status == 'FAIL':
            self.antom_refund_phase = 'failed'
            self._set_error(data['result'].get('resultMessage') or _("Antom rejected the refund."))
        else:
            self.antom_refund_phase = 'query'
            self._set_pending()

    def _antom_poll_refund(self):
        """Run on a persisted child, with its row locked by the refund cron."""
        self.ensure_one()
        if self.antom_refund_phase not in ('queued', 'query'):
            return
        self.antom_refund_next_poll = fields.Datetime.now() + timedelta(minutes=5)
        # Do not send again while a verified response awaits native processing.
        if self.payment_data_ids.filtered(lambda item: not item.errored):
            return
        send = self.antom_refund_phase == 'queued'
        try:
            client = self._antom_refund_client()
            self.antom_refund_phase = 'query'
            if send:
                data = client.payment.refund(**self.antom_refund_payload)
                self._antom_record_refund(data, 'submit')
            else:
                self._alipay_query_and_record()
        except (RequestException, ValueError, TypeError, UserError):
            # Timeout, bad signature/config or malformed response is NOT failure.
            # Keep funds reserved and query the same request ID, never a new child.
            self.state_message = _("Antom refund unconfirmed; automatic inquiry will retry. Check provider configuration if this persists.")
            _logger.warning('Antom refund %s remains unconfirmed.', self.reference)

    @api.model
    def _cron_antom_refund(self):
        Transaction = self.env['payment.transaction']
        domain = [('provider_code', '=', 'alipay'),
                  ('antom_refund_phase', 'in', ('queued', 'query')),
                  ('antom_refund_next_poll', '<=', fields.Datetime.now())]
        refunds = Transaction.search(domain, order='antom_refund_next_poll, id', limit=20)
        Cron = self.env['ir.cron']
        Cron._commit_progress(remaining=len(refunds))
        for refund in refunds:
            locked = (refund | refund.source_transaction_id).try_lock_for_update()
            if len(locked) != 2:
                Cron._rollback_progress()
                continue
            refund.with_context(payment_safe_write=True)._antom_poll_refund()
            if not Cron._commit_progress(processed=1):
                break

    @api.ondelete(at_uninstall=False)
    def _unlink_except_antom_refund(self):
        if self.filtered('antom_refund_request_id'):
            raise ValidationError(_("Keep Antom refund records for idempotency and reconciliation."))

    antom_refund_client_id = fields.Char(readonly=True, copy=False)
    antom_refund_location = fields.Char(readonly=True, copy=False)
    antom_refund_next_poll = fields.Datetime(readonly=True, copy=False, index=True)
    antom_refund_payload = fields.Json(readonly=True, copy=False)
    antom_refund_phase = fields.Selection(REFUND_PHASES, readonly=True, copy=False, index=True)
    antom_refund_request_id = fields.Char(readonly=True, copy=False, index=True)

    _antom_refund_request_unique = models.Constraint(
        'UNIQUE(antom_refund_request_id)', 'Antom refund request IDs must be unique.')
    _antom_refund_provider_unique = models.Constraint(
        'UNIQUE(antom_refund_client_id, antom_refund_location, is_live, provider_reference)',
        'An Antom refund identifier can belong to only one refund transaction.')

    def write(self, vals):
        if REFUND_IMMUTABLE_FIELDS.intersection(vals) and self.filtered('antom_refund_request_id'):
            raise ValidationError(_("An Antom refund request cannot be changed after creation."))
        return super().write(vals)
