"""Run in an isolated Odoo shell: seed on the old addon, verify after upgrading.

Set QING_OCR_PROBE_MODE=seed or verify. Never use on a shared or production DB.
The snapshot is stored only in the isolated database's system parameters.
"""
import hashlib
import io
import json
import os
from unittest.mock import patch

from PIL import Image
from odoo.tools.binary import BinaryBytes

assert env.cr.dbname.startswith('qing_ocr_'), 'Use a dedicated OCR test database.'
MODE = os.environ['QING_OCR_PROBE_MODE']
KEY = 'qing_ocr.upgrade_probe'
FIELDS = ['recognized', 'reviewed', 'ocr_text', 'ocr_engine', 'number', 'invoice_date',
          'amount_untaxed', 'amount_tax', 'invoice_total', 'source_filename', 'fapiao_id']
TEXT = '''Synthetic document
发票号码：12345678901234567890
开票日期：2026年09月22日
购买方名称：合成采购公司
购买方税号：TESTBUYER
销售方名称：合成销售公司
销售方税号：TESTSELLER
不含税金额：100.00
税额：13.00
价税合计：113.00
'''


def snapshot(wizards):
    values = wizards.read(FIELDS)
    for row, wizard in zip(values, wizards):
        row['source_sha256'] = hashlib.sha256(bytes(wizard.source_file)).hexdigest()
        row['invoice_date'] = str(row['invoice_date'])
    references = env['ir.model.data'].search_read(
        [('module', '=', 'qing_account_fapiao_ocr')], ['name', 'model', 'res_id'], order='name')
    return json.loads(json.dumps({'wizards': values, 'references': references}))


if MODE == 'seed':
    assert env['ir.module.module'].search([('name', '=', 'qing_account_fapiao_ocr')]).installed_version == '20.0.1.3'
    partner = env['res.partner'].create({'name': 'Synthetic OCR migration partner'})
    output = io.BytesIO()
    Image.new('RGB', (50, 50), 'white').save(output, format='PNG')
    wizards = env['qing.fapiao.ocr'].create([
        {'source_file': BinaryBytes(output.getvalue()), 'source_filename': 'migration.png', 'partner_id': partner.id}
        for _ in range(2)
    ])
    for wizard in wizards:
        with patch('odoo.addons.qing_account_fapiao_ocr.wizard.fapiao_ocr.extract_text', return_value=(TEXT, 'Legacy engine label')):
            wizard.action_recognize()
        wizard.reviewed = True
    wizards[1].action_create_draft()
    original = snapshot(wizards)
    ticket = wizards[1].fapiao_id
    original['ticket'] = ticket.read(['number', 'state', 'note', 'source_filename'])[0]
    original['ticket_sha256'] = hashlib.sha256(bytes(ticket.source_file)).hexdigest()
    env['ir.config_parameter'].set_str(KEY, json.dumps(original))
    env.cr.commit()
    print('OCR_UPGRADE_SEEDED', wizards.ids, ticket.id)
elif MODE == 'verify':
    original = json.loads(env['ir.config_parameter'].get_str(KEY))
    wizards = env['qing.fapiao.ocr'].browse([row['id'] for row in original['wizards']])
    assert snapshot(wizards) == {key: original[key] for key in ('wizards', 'references')}
    ticket = wizards[1].fapiao_id
    assert ticket.read(['number', 'state', 'note', 'source_filename'])[0] == original['ticket']
    assert hashlib.sha256(bytes(ticket.source_file)).hexdigest() == original['ticket_sha256']
    # Existing reviewed records can finish the unchanged business flow after upgrade.
    assert wizards[1].action_create_draft()['res_id'] == ticket.id
    assert env['ir.module.module'].search([('name', '=', 'qing_ocr')]).state == 'installed'
    service = env['qing.ocr.service'].with_context(lang='zh_CN')
    from odoo.addons.qing_ocr.services.local_ocr import RecognitionError
    try:
        service._extract_text(b'')
    except RecognitionError as error:
        assert str(error) == '请上传不超过 10 MB 的 PNG、JPEG 或 PDF 文件。', str(error)
    else:
        raise AssertionError('Expected the translated validation error')
    print('OCR_UPGRADE_PRESERVATION_PASS', wizards.ids, ticket.id)
else:
    raise AssertionError('Unknown probe mode')
