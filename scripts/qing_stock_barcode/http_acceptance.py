"""Real HTTP concurrency/performance checks against the disposable test database only.

Start an SSH tunnel on localhost:19996 and run seed_browser.py first.
This creates synthetic products, transfers and ten disposable users.
"""
import os
import concurrent.futures
import http.cookiejar
import json
import statistics
import time
import urllib.request
import uuid

URL = 'http://127.0.0.1:19996'
DB = 'qing_barcode_test'
MODEL = 'qing.stock.scan.session'
PASSWORD = os.environ["QING_TEST_PASSWORD"]


class Client:
    def __init__(self, login='barcode_admin'):
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.call('/web/session/authenticate', {'db': DB, 'login': login, 'password': PASSWORD})

    def call(self, route, params):
        data = json.dumps({'jsonrpc': '2.0', 'method': 'call', 'params': params, 'id': 1}).encode()
        response = json.load(self.opener.open(urllib.request.Request(URL + route, data,
                              {'Content-Type': 'application/json'}), timeout=60))
        if response.get('error'):
            raise RuntimeError(response['error']['data'].get('message', str(response['error'])))
        return response['result']

    def rpc(self, model, method, args, **kwargs):
        return self.call(f'/web/dataset/call_kw/{model}/{method}',
                         {'model': model, 'method': method, 'args': args, 'kwargs': kwargs})


admin = Client()
company = admin.rpc('res.company', 'search', [[]], limit=1)[0]
warehouse = admin.rpc('stock.warehouse', 'search_read', [[['company_id', '=', company]]],
                      fields=['in_type_id', 'lot_stock_id'], limit=1)[0]
supplier = admin.rpc('stock.location', 'search', [[['usage', '=', 'supplier']]], limit=1)[0]
marker = str(uuid.uuid4())[:8]


def transfer(size=1):
    products = admin.rpc('product.product', 'create', [[{'name': f'HTTP {marker} {size} {i}',
        'is_storable': True, 'barcode': f'HTTP-{marker}-{uuid.uuid4()}'} for i in range(size)]])
    rows = admin.rpc('product.product', 'read', [products], fields=['uom_id', 'barcode'])
    values = {'origin': 'Synthetic concurrency and performance acceptance',
              'picking_type_id': warehouse['in_type_id'][0], 'location_id': supplier,
              'location_dest_id': warehouse['lot_stock_id'][0],
              'move_ids': [[0, 0, {'product_id': row['id'], 'product_uom_qty': 12,
                  'uom_id': row['uom_id'][0], 'location_id': supplier,
                  'location_dest_id': warehouse['lot_stock_id'][0]}] for row in rows]}
    picking = admin.rpc('stock.picking', 'create', [[values]])[0]
    admin.rpc('stock.picking', 'action_confirm', [[picking]])
    admin.rpc('stock.picking', 'action_assign', [[picking]])
    return picking, rows


def start(client, picking):
    return client.rpc(MODEL, 'start_picking', [picking, str(uuid.uuid4()), 'HTTP acceptance'])


def scan(client, session, code, key=None):
    return client.rpc(MODEL, 'execute', [[session], key or str(uuid.uuid4()), {'op': 'scan', 'code': code}])


with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
    picking, products = transfer()
    clients = [Client(), Client()]
    sessions = list(pool.map(lambda c: start(c, picking), clients))
    assert sessions[0]['id'] == sessions[1]['id'], sessions
    sid, code = sessions[0]['id'], products[0]['barcode']
    key = str(uuid.uuid4())
    results = list(pool.map(lambda c: scan(c, sid, code, key), clients))
    assert len({r['event_id'] for r in results}) == 1, results
    assert sum(l['quantity'] for l in admin.rpc(MODEL, 'get_session', [[sid]])['lines']) == 1
    results = list(pool.map(lambda c: scan(c, sid, code), clients))
    assert sum(l['quantity'] for l in admin.rpc(MODEL, 'get_session', [[sid]])['lines']) == 3
    rejected_key = str(uuid.uuid4())
    rejected = list(pool.map(lambda c: scan(c, sid, 'UNKNOWN-CONCURRENT', rejected_key), clients))
    assert all(not r['ok'] for r in rejected) and len({r['event_id'] for r in rejected}) == 1
    print('PASS: concurrent open, repeated UUID, independent scans and repeated rejection', flush=True)

    measurements = {}
    for size in (50, 150):
        picking, rows = transfer(size)
        session = start(admin, picking)
        durations = []
        for row in rows[:20]:
            begin = time.monotonic()
            result = scan(admin, session['id'], row['barcode'])
            durations.append(time.monotonic() - begin)
            assert result['ok'], result
        measurements[size] = {'samples': len(durations), 'median_ms': round(statistics.median(durations)*1000),
                              'p95_ms': round(sorted(durations)[18]*1000), 'max_ms': round(max(durations)*1000)}
    print('PERFORMANCE', json.dumps(measurements), flush=True)

    group = admin.rpc('ir.model.data', 'search_read', [[['module', '=', 'qing_stock_barcode'],
                     ['name', '=', 'group_scan_user']]], fields=['res_id'])[0]['res_id']
    logins = [f'http_{marker}_{i}' for i in range(10)]
    admin.rpc('res.users', 'create', [[{'name': login, 'login': login, 'password': PASSWORD,
        'company_id': company, 'company_ids': [[6, 0, [company]]], 'group_ids': [[6, 0, [group]]]}
        for login in logins]], context={'no_reset_password': True})
    operators = list(pool.map(Client, logins))
    picking, rows = transfer()
    def compete(client):
        try:
            return start(client, picking)
        except RuntimeError as error:
            return str(error)
    competition = list(pool.map(compete, operators[:2]))
    assert sum(isinstance(r, dict) for r in competition) == 1, competition
    assert any(isinstance(r, str) and 'Another operator' in r for r in competition), competition
    jobs = []
    for client in operators:
        picking, rows = transfer()
        jobs.append((client, start(client, picking)['id'], rows[0]['barcode']))
    begin = time.monotonic()
    results = list(pool.map(lambda job: scan(*job), jobs))
    assert all(r['ok'] and sum(l['quantity'] for l in r['session']['lines']) == 1 for r in results), results
    print('PASS: competing operators; ten authenticated operators on separate transfers; batch_ms',
          round((time.monotonic()-begin)*1000), flush=True)
