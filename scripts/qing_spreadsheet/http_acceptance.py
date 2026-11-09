"""Independent HTTP sessions; run only against the disposable test fixture."""
import os
import concurrent.futures
import copy
import json
import os
import re
import uuid
import requests

BASE = os.environ.get('TEST_URL', 'http://127.0.0.1:18998')
DB = 'qing_spreadsheet_test'


def login(name):
    session = requests.Session()
    session.trust_env = False  # The disposable loopback fixture must not use a host proxy.
    response = session.post(BASE + '/web/session/authenticate', json={'jsonrpc': '2.0', 'method': 'call',
        'params': {'db': DB, 'login': name, 'password': os.environ["QING_TEST_PASSWORD"]}, 'id': 1}, timeout=30).json()
    assert 'error' not in response, response.get('error')
    return session, response['result']


def rpc(session, model, method, args, kwargs=None, denied=False):
    response = session.post(BASE + f'/web/dataset/call_kw/{model}/{method}', json={
        'jsonrpc': '2.0', 'method': 'call', 'id': 1,
        'params': {'model': model, 'method': method, 'args': args, 'kwargs': kwargs or {}}}, timeout=30).json()
    if denied:
        assert 'error' in response, response
        return response['error']
    assert 'error' not in response, response.get('error')
    return response['result']


def csrf(session):
    html = session.get(BASE + '/odoo', timeout=30).text
    match = re.search(r'csrf_token:\s*[\"\']([^\"\']+)', html)
    assert match, 'CSRF token not found'
    return match[1]


owner, owner_info = login('sheet_owner')
editor, editor_info = login('sheet_editor')
viewer, viewer_info = login('sheet_viewer')
outsider, _ = login('sheet_outsider')
manager, _ = login('sheet_manager')
book = rpc(owner, 'qing.spreadsheet', 'create', [{'name': 'HTTP acceptance',
    'editor_ids': [[4, editor_info['uid']]], 'reader_ids': [[4, viewer_info['uid']]]}])
call = lambda session, method, args=None, **kw: rpc(session, 'qing.spreadsheet', method, [[book], *(args or [])], **kw)
data = call(owner, 'open_workbook')['data']
assert call(viewer, 'open_workbook')['id'] == book
assert call(manager, 'open_workbook')['id'] == book
call(outsider, 'open_workbook', denied=True)
call(viewer, 'acquire_lease', denied=True)
call(editor, 'write', [{'reader_ids': [[4, owner_info['uid']]]}], denied=True)
token = call(owner, 'acquire_lease')['token']
assert call(editor, 'acquire_lease')['acquired'] is False
data['sheets'][0]['cells'] = {'A1': '42'}
# Different HTTP sessions use independent transactions and compete on the same row.
other_owner, _ = login('sheet_owner')
request_id = str(uuid.uuid4())
with concurrent.futures.ThreadPoolExecutor(2) as pool:
    results = list(pool.map(lambda session: call(session, 'save_workbook', [data, 0, token, request_id]), [owner, other_owner]))
assert sorted(result['replayed'] for result in results) == [False, True], results
assert call(owner, 'open_workbook')['version'] == 1
call(owner, 'save_workbook', [data, 0, token, str(uuid.uuid4())], denied=True)
revisions = call(viewer, 'list_versions')
assert len(revisions) == 2
rpc(outsider, 'qing.spreadsheet.revision', 'read', [[revisions[0]['id']], ['data']], denied=True)
# Export checks apply on the HTTP route, including the native export group.
response = owner.post(BASE + f'/qing_spreadsheet/{book}/export', data={'format': 'json', 'csrf_token': csrf(owner)}, timeout=30)
assert response.status_code == 200 and response.json()['sheets'][0]['cells']['A1'] == '42'
for session in [viewer, outsider]:
    response = session.post(BASE + f'/qing_spreadsheet/{book}/export', data={'format': 'json', 'csrf_token': csrf(session)}, timeout=30)
    assert response.status_code >= 400, response.status_code
# Company B is allowed for the owner but absent from the editor's companies.
companies = list(owner_info['user_companies']['allowed_companies'])
other_company = next(int(value) for value in companies if int(value) != owner_info['user_companies']['current_company'])
foreign_book = rpc(owner, 'qing.spreadsheet', 'create', [{'name': 'Other company', 'company_id': other_company}],
                   {'context': {'allowed_company_ids': [other_company]}})
rpc(editor, 'qing.spreadsheet', 'open_workbook', [[foreign_book]], denied=True)
# Bound record application is also idempotent across independent HTTP transactions.
record = rpc(owner, 'qing.spreadsheet.fixture', 'create', [{'name': 'HTTP fixture', 'value': 12}])
action = rpc(owner, 'qing.spreadsheet.fixture', 'action_open_qing_spreadsheet', [[record]])
bound_id = action['params']['workbook_id']
bound = lambda session, method, args=None: rpc(session, 'qing.spreadsheet', method, [[bound_id], *(args or [])])
bound_data = bound(owner, 'open_workbook')['data'];bound_data['sheets'][0]['cells']['B1'] = '27'
bound_token = bound(owner, 'acquire_lease')['token'];apply_id = str(uuid.uuid4())
with concurrent.futures.ThreadPoolExecutor(2) as pool:
    results = list(pool.map(lambda session: bound(session, 'apply_record', [bound_data, 0, bound_token, apply_id]), [owner, other_owner]))
assert results[0] == results[1]
assert rpc(owner, 'qing.spreadsheet.fixture', 'read', [[record], ['value', 'apply_count']])[0]['apply_count'] == 1
# Revoking sharing blocks direct reads and the guarded data query immediately.
call(owner, 'write', [{'reader_ids': [[5]], 'editor_ids': [[5]]}])
call(viewer, 'open_workbook', denied=True)
call(viewer, 'query_data', ['res.partner', 'search_count', [[]], {}], denied=True)
print(json.dumps({'roles': 5, 'company_isolation': True, 'http_export_acl': True,
                  'concurrent_save_replayed_once': True, 'concurrent_apply_once': True,
                  'revocation': True, 'book': book, 'bound_book': bound_id}, indent=2))
