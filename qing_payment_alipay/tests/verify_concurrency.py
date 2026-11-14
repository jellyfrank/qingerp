"""Run via Odoo shell ONLY on a disposable test_antom_refund_* database.

Commits fake merchant configuration and mock transactions; never import as a
TransactionCase. Validates real concurrent cursors and real cron commits.
"""
import concurrent.futures
import threading
from unittest.mock import patch

from Crypto.PublicKey import RSA
from psycopg2.errors import SerializationFailure

from odoo import api
from odoo.exceptions import ValidationError
from odoo.tools import BinaryBytes

assert env.cr.dbname.startswith('test_antom_refund_'), 'Disposable refund test database required'
provider = env.ref('qing_payment_alipay.payment_provider_alipay')
key = RSA.generate(2048)
provider.write({'is_global': True, 'is_live': False, 'active': True,
                'antom_sandbox_client_id': 'concurrency-test-only',
                'antom_sandbox_private_key': BinaryBytes(key.export_key()),
                'antom_sandbox_public_key': BinaryBytes(key.public_key().export_key())})
source = env['payment.transaction'].create({
    'provider_id': provider.id,
    'payment_method_id': env.ref('qing_payment_alipay.payment_method_alipay').id,
    'partner_id': env.user.partner_id.id, 'amount': 10, 'currency_id': env.ref('base.USD').id,
    'operation': 'online_redirect', 'state': 'done', 'provider_reference': 'mock-concurrency-payment',
})
source_id = source.id
env.cr.commit()
barrier = threading.Barrier(2)
registry = env.registry
uid = env.uid

def refund_worker():
    retried = False
    for attempt in range(3):
        try:
            with registry.cursor() as cr:
                worker_env = api.Environment(cr, uid, {})
                tx = worker_env['payment.transaction'].browse(source_id)
                assert tx.state == 'done'
                if attempt == 0:
                    barrier.wait(timeout=15)
                try:
                    child = tx._refund(7)
                except ValidationError:
                    return ('limit_rejected', retried)
                cr.commit()
                return ('created', retried)
        except SerializationFailure:
            retried = True
    raise AssertionError('Concurrent transaction did not resolve')

with patch('requests.sessions.Session.request', side_effect=AssertionError('Real HTTP forbidden')):
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: refund_worker(), range(2)))
    assert sorted(result[0] for result in results) == ['created', 'limit_rejected'], results
    assert any(result[1] for result in results), results
    env.cr.rollback()
    children = env['payment.transaction'].search([('source_transaction_id', '=', source_id)])
    assert len(children) == 1 and children.amount == -7 and children.antom_refund_phase == 'queued'
    print('CONCURRENT_REFUND_LIMIT_OK', results)

    def response(**payload):
        return {'result': {'resultStatus': 'S'}, 'refundRequestId': payload['refundRequestId'],
                'refundId': 'mock-' + payload['refundRequestId'], 'paymentId': payload['paymentId'],
                'refundAmount': payload['refundAmount']}

    client = provider._get_global_instance(sandbox=True)
    with patch.object(type(client.payment), 'refund', side_effect=response) as submit:
        env['payment.transaction']._cron_antom_refund()
        own_calls = [call for call in submit.call_args_list
                     if call.kwargs['refundRequestId'] == children.antom_refund_request_id]
        assert len(own_calls) == 1
    env['payment.data']._cron_process()
    children.invalidate_recordset()
    assert children.state == 'done' and children.antom_refund_phase == 'done'
    assert not children.payment_data_ids
    print('REAL_CRON_AND_NATIVE_PROCESSING_OK')

module = env['ir.module.module'].search([('name', '=', 'qing_payment_alipay')])
assert module.latest_version == '20.0.1.3'
assert provider.get_view(view_type='form')['arch']
assert provider.support_refund == 'partial'
print('MODULE_VERSION_AND_PROVIDER_OK', module.latest_version)
