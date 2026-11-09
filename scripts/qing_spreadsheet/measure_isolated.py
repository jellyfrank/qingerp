"""Synthetic 1k/10k read benchmark. Never run against a tenant database."""
import json
import time
from odoo import Command
assert env.cr.dbname == 'qing_spreadsheet_test'
Partner = env['res.partner'].with_context(tracking_disable=True, mail_create_nolog=True)
domain = [('name', 'ilike', 'Qing Sheet Perf ')]
if Partner.search_count(domain) != 10000:
    Partner.search(domain).unlink()
    Partner.create([{'name': f'Qing Sheet Perf {index:05d}', 'city': 'Bucket 0' if index % 5 == 0 else 'Bucket 1', 'company_id': env.company.id} for index in range(10000)])
admin = env.ref('base.user_admin')
book = env['qing.spreadsheet'].with_user(admin).create({'name': 'Performance fixture'})
env.flush_all()
env.cr.commit()
book.query_data('res.partner', 'formatted_read_grouping_sets', [], {
    'domain': domain, 'grouping_sets': [[], ['city']], 'aggregates': ['__count']})
results = []
for count, filters in [(1000, domain + [('name', '<=', 'Qing Sheet Perf 00999')]), (10000, domain)]:
    start_queries = env.cr.sql_log_count
    started = time.perf_counter()
    grouped = book.query_data('res.partner', 'formatted_read_grouping_sets', [], {
        'domain': filters, 'grouping_sets': [[], ['city']], 'aggregates': ['__count']})
    elapsed = time.perf_counter() - started
    assert grouped[0][0]['__count'] == count, grouped
    assert sorted(row['__count'] for row in grouped[1]) == [count // 5, count * 4 // 5], grouped
    results.append({'records': count, 'elapsed_ms': round(elapsed * 1000, 2),
                    'queries': env.cr.sql_log_count - start_queries, 'group_rows': sum(map(len, grouped))})
modules = env['ir.module.module'].search([('state', '=', 'installed')]).mapped('name')
assert not set(modules) & {'web_enterprise', 'spreadsheet_edition', 'documents', 'documents_spreadsheet'}
result = {'book_id': book.id, 'server_aggregation': results, 'enterprise_dependencies': [], 'installed_modules': len(modules)}
env.cr.commit()
with open('/tmp/qing_sheet_perf.json', 'w') as output:
    json.dump(result, output, indent=2)
print(json.dumps(result))
