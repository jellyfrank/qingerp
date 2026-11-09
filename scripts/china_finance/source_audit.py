"""Read-only audit of installed image manifests and relevant source hashes."""
import ast
import hashlib
import json
from pathlib import Path

roots = [Path('/usr/lib/python3/dist-packages/odoo/addons'), Path('/mnt/cnf')]
modules = {}


def visit(name):
    if name in modules:
        return
    path = next((root / name for root in roots if (root / name / '__manifest__.py').exists()), None)
    if path is None:
        raise RuntimeError('Missing dependency: ' + name)
    manifest = ast.literal_eval((path / '__manifest__.py').read_text())
    modules[name] = {'license': manifest.get('license', 'LGPL-3 (Odoo default)'),
                     'version': manifest.get('version'), 'depends': manifest.get('depends', [])}
    for dependency in modules[name]['depends']:
        visit(dependency)


for name in ('qing_account', 'l10n_cn', 'sale_stock', 'purchase_stock'):
    visit(name)
files = ['stock_account/models/res_company.py', 'stock_account/models/product.py',
         'purchase_stock/models/account_invoice.py', 'l10n_cn/models/template_cn.py',
         'l10n_cn/models/template_cn_large_bis.py']
print(json.dumps({'modules': modules, 'source_sha256': {
    name: hashlib.sha256((roots[0] / name).read_bytes()).hexdigest() for name in files
}}, indent=2, sort_keys=True))
