"""Run with Odoo shell only in an isolated qing_cnf_* database containing the mock addon."""
import hashlib
import hmac
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

from psycopg2.errors import SerializationFailure
from odoo import api, Command, SUPERUSER_ID
from odoo.exceptions import UserError

assert env.cr.dbname.startswith('qing_cnf_'), 'Disposable database required'
assert 'mommy.fapiao.order' in env.registry
registry = env.registry
company = env.company
company.currency_id = env.ref('base.CNY')
company.country_id = env.ref('base.cn')
company_id = company.id
if not company.chart_template:
    env['account.chart.template'].try_loading('generic_coa', company, install_demo=False)
prefix = 'PROVIDER-QA-' + uuid4().hex[:8]
env['ir.config_parameter'].sudo().set_bool('qing_fapiao.allow_test_registration', True)
partner = env['res.partner'].create({'name': prefix})
account = env['account.account'].search([('company_ids', 'in', [company_id]), ('account_type', '=', 'income')], limit=1)
tax = env['account.tax'].create({'name': prefix, 'amount': 13, 'type_tax_use': 'sale'})


def fixture(scenario='success'):
    bill = env['account.move'].create({'move_type': 'out_invoice', 'partner_id': partner.id,
        'invoice_date': '2026-09-19', 'invoice_line_ids': [Command.create({'name': prefix,
        'quantity': 1, 'price_unit': 100, 'account_id': account.id, 'tax_ids': [Command.set(tax.ids)]})]})
    bill.action_post()
    request = env['qing.fapiao.request'].browse(bill.action_create_fapiao_request()['res_id'])
    request.seller_tax_id = 'SYNTHETIC-SELLER'
    request.line_ids.tax_code = 'SYNTHETIC-CODE'
    request.action_submit()
    request.action_approve()
    provider = env['mommy.fapiao.provider'].create({'name': prefix, 'code': 'mock', 'enabled': True,
        'company_id': company_id, 'seller_tax_id': request.seller_tax_id,
        'environment': 'test', 'mock_scenario': scenario})
    order = request._create_provider_order(provider)
    env.cr.commit()
    return order.id, request.id


def transaction(callback):
    for retry in range(5):
        try:
            with registry.cursor() as cr:
                local = api.Environment(cr, SUPERUSER_ID, {'allowed_company_ids': [company_id]})
                result = callback(local)
                cr.commit()
                return result
        except SerializationFailure:
            if retry == 4:
                raise


def state(order_id):
    def read(local):
        order = local['mommy.fapiao.order'].browse(order_id)
        operations = [event.payload['operation'] for event in order.event_ids.sorted('id') if event.kind == 'attempt']
        return order.state, order.registration_state, operations, order.qing_request_id.fapiao_id.id
    return transaction(read)


def process(order_id):
    return env['mommy.fapiao.order']._process_one_order(order_id)


def concurrent(callback):
    barrier = Barrier(2)
    def run(index):
        first = True
        def action(local):
            nonlocal first
            if first:
                # Fix the transaction snapshot before either contender proceeds.
                local.cr.execute('SELECT count(*) FROM mommy_fapiao_order')
                barrier.wait(timeout=20)
                first = False
            return callback(local, index)
        return transaction(action)
    with ThreadPoolExecutor(max_workers=2) as pool:
        return list(pool.map(run, range(2)))


order_id, request_id = fixture()
with ThreadPoolExecutor(max_workers=2) as pool:
    list(pool.map(process, [order_id, order_id]))
assert state(order_id)[:3] == ('issued', 'done', ['submit']), state(order_id)
print('PROVIDER_QA concurrent_workers_one_submission OK')

order_id, request_id = fixture('timeout')
process(order_id)
assert state(order_id)[:3] == ('unknown', 'pending', ['submit']), state(order_id)
def cancel_unknown(local):
    request = local['qing.fapiao.request'].browse(request_id)
    request.cancel_reason = 'Cannot cancel an unknown external outcome'
    try:
        with local.cr.savepoint():
            request.action_cancel()
    except UserError:
        return True
    raise AssertionError('Unknown issuance released the financial reservation')
assert transaction(cancel_unknown)
transaction(lambda local: local['mommy.fapiao.order'].browse(order_id).action_query())
process(order_id)
assert state(order_id)[:3] == ('issued', 'done', ['submit', 'query']), state(order_id)
print('PROVIDER_QA timeout_query_only_reservation_retained OK')

order_id, request_id = fixture()
assert transaction(lambda local: local['mommy.fapiao.order'].browse(order_id)._prepare_attempt()) == 'submit'
def lose_response(local):
    order = local['mommy.fapiao.order'].browse(order_id)
    assert order.provider_id._submit_invoice(order)['status'] == 'issued'
    # Simulate process loss after the external service accepted the request.
    local.cr.rollback()
transaction(lose_response)
transaction(lambda local: local['mommy.fapiao.order'].browse(order_id).action_query())
process(order_id)
assert state(order_id)[:3] == ('issued', 'done', ['submit', 'query']), state(order_id)
print('PROVIDER_QA response_loss_durable_marker OK')

order_id, request_id = fixture('pending')
process(order_id)
def callback(local, index):
    order = local['mommy.fapiao.order'].browse(order_id)
    provider = order.provider_id
    body = json.dumps({'event_id': 'same-event', 'request_key': order.request_key,
                       'result': provider._mock_result(order)}).encode()
    signature = hmac.new(provider.mock_signing_secret.encode(), body, hashlib.sha256).hexdigest()
    return provider._process_notification({'X-Mock-Signature': signature}, body).id
event_ids = concurrent(callback)
assert event_ids[0] == event_ids[1], event_ids
process(order_id)
assert state(order_id)[:3] == ('issued', 'done', ['submit']), state(order_id)
print('PROVIDER_QA duplicate_notifications_one_registration OK')

order_id, request_id = fixture()
transaction(lambda local: local['ir.config_parameter'].sudo().set_bool('qing_fapiao.allow_test_registration', False))
process(order_id)
assert state(order_id)[:3] == ('issued', 'error', ['submit']), state(order_id)
assert not state(order_id)[3]
transaction(lambda local: local['ir.config_parameter'].sudo().set_bool('qing_fapiao.allow_test_registration', True))
def register(local, index):
    order = local['mommy.fapiao.order'].browse(order_id)
    order.action_record_result()
    return order.qing_request_id.fapiao_id.id
tickets = concurrent(register)
assert tickets[0] and tickets[0] == tickets[1], tickets
assert state(order_id)[:3] == ('issued', 'done', ['submit']), state(order_id)
print('PROVIDER_QA registration_failure_retries_only_local_once OK')

order_id, request_id = fixture()
def cancel_or_claim(local, index):
    order = local['mommy.fapiao.order'].browse(order_id)
    try:
        with local.cr.savepoint():
            if index:
                operation = order._prepare_attempt()
                return operation or 'no-send'
            order.action_cancel()
            request = order.qing_request_id
            request.cancel_reason = 'Synthetic cancellation race'
            request.action_cancel()
            return 'cancelled'
    except UserError:
        return 'blocked'
outcomes = concurrent(cancel_or_claim)
final_state = state(order_id)[0]
assert final_state in ('cancelled', 'unknown'), outcomes
assert not ('cancelled' in outcomes and 'submit' in outcomes), outcomes
if final_state == 'unknown':
    assert transaction(lambda local: local['qing.fapiao.request'].browse(request_id).state) == 'approved'
print('PROVIDER_QA cancel_vs_first_send_serialized OK', outcomes)

env.cr.rollback()
env.invalidate_all()
provider = env['mommy.fapiao.provider'].create({'name': prefix + '-identity', 'code': 'mock', 'enabled': True,
    'seller_tax_id': 'SYNTHETIC-SELLER', 'environment': 'test'})
provider_id = provider.id
payload = dict(env['mommy.fapiao.order'].browse(order_id).snapshot, request_key=str(uuid4()))
env.cr.commit()
def create_or_change(local, index):
    provider = local['mommy.fapiao.provider'].browse(provider_id)
    try:
        with local.cr.savepoint():
            if index:
                provider.seller_tax_id = 'OTHER-SYNTHETIC-SELLER'
                return 'changed'
            local['mommy.fapiao.order']._create_order(provider, payload)
            return 'created'
    except UserError:
        return 'blocked'
outcomes = concurrent(create_or_change)
assert 'blocked' in outcomes, outcomes
assert not ('changed' in outcomes and 'created' in outcomes), outcomes
print('PROVIDER_QA first_order_vs_provider_identity_serialized OK', outcomes)

env.cr.rollback()
env.invalidate_all()
print('PROVIDER_QA ALL_OK')
