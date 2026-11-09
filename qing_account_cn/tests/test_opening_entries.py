import base64
import json
import csv
import io
from pathlib import Path

from odoo.tools.binary import BinaryBytes
from odoo import Command
from odoo.exceptions import AccessError, UserError
from odoo.tests import Form, TransactionCase, new_test_user, tagged


@tagged('post_install', '-at_install')
class TestOpeningEntries(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        company = cls.env['res.company'].with_context(chart_template_load=True).create({
            'name': 'Qing opening import test', 'country_id': cls.env.ref('base.cn').id,
            'currency_id': cls.env.ref('base.CNY').id,
        })
        cls.env = cls.env(context=dict(cls.env.context, allowed_company_ids=company.ids))
        cls.company = company.with_env(cls.env)
        cls.env['account.chart.template'].try_loading('cn', cls.company, install_demo=False)
        cls.journal = cls.env['account.journal'].search([
            ('company_id', '=', company.id), ('type', '=', 'general'),
        ], limit=1)
        cls.equity = cls.env['account.account'].search([
            ('company_ids', 'in', company.ids), ('account_type', '=', 'equity'),
        ], limit=1)
        cls.partner = cls.env['res.partner'].create({'name': 'Opening sample partner'})
        cls.manager = new_test_user(cls.env, login='opening_manager', groups='account.group_account_manager',
                                   company_id=company.id, company_ids=[Command.set(company.ids)])
        cls.billing = new_test_user(cls.env, login='opening_billing', groups='account.group_account_invoice',
                                   company_id=company.id, company_ids=[Command.set(company.ids)])
        cls.mapping = {key: record.export_data(['id'])['datas'][0][0] for key, record in {
            'company': cls.company, 'journal': cls.journal, 'equity': cls.equity,
            'partner': cls.partner, 'receivable': cls.company.receivable_account_id,
            'payable': cls.company.payable_account_id,
        }.items()}

    def _data(self):
        fixture = json.loads((Path(__file__).parent / 'fixtures/opening_entries.json').read_text())
        return fixture['fields'], [[cell.format(**self.mapping) for cell in row] for row in fixture['rows']]

    def _load(self, fields=None, rows=None):
        if fields is None:
            fields, rows = self._data()
        return self.env['account.move'].with_user(self.manager).with_context(import_file=True).load(fields, rows)

    def _import_move(self):
        result = self._load()
        self.assertFalse(result['messages'], result['messages'])
        self.assertEqual(len(result['ids']), 1)
        return self.env['account.move'].browse(result['ids'])

    def test_native_import_and_post(self):
        move = self._import_move()
        self.assertEqual(move.state, 'draft')
        self.assertEqual(len(move.line_ids), 3)
        self.assertEqual(sum(move.line_ids.mapped('debit')), 700)
        self.assertEqual(sum(move.line_ids.mapped('credit')), 700)
        move.with_user(self.manager).action_post()
        self.assertEqual(move.state, 'posted')
        self.assertEqual(self.env.ref('__import__.qing_opening_ar').date_maturity.isoformat(), '2026-09-15')
        self.assertEqual(self.env.ref('__import__.qing_opening_ap').amount_residual, -500)

    def test_repeat_external_ids_do_not_duplicate_lines(self):
        move = self._import_move()
        line_ids = move.line_ids.ids
        self.assertEqual(self._import_move(), move)
        self.assertEqual(move.line_ids.ids, line_ids)
        fields, rows = self._data()
        rows[0][fields.index('line_ids/name')] = 'Corrected source reference'
        result = self._load(fields, rows)
        self.assertFalse(result['messages'], result['messages'])
        self.assertEqual(self.env.ref('__import__.qing_opening_ar').name, 'Corrected source reference')
        self.assertEqual(move.line_ids.ids, line_ids)

    def test_invalid_mapping_is_rejected_without_partial_entry(self):
        fields, rows = self._data()
        rows[1][fields.index('line_ids/account_id/id')] = '__import__.missing_account'
        result = self._load(fields, rows)
        self.assertFalse(result['ids'])
        self.assertTrue(result['messages'])
        self.assertFalse(self.env.ref('__import__.qing_opening_demo', raise_if_not_found=False))

    def test_unbalanced_import_is_rejected(self):
        fields, rows = self._data()
        rows[0][fields.index('line_ids/debit')] = '701.00'
        rows[0][fields.index('line_ids/amount_currency')] = '701.00'
        result = self._load(fields, rows)
        self.assertFalse(result['ids'])
        self.assertTrue(result['messages'])
        self.assertFalse(self.env.ref('__import__.qing_opening_demo', raise_if_not_found=False))

    def test_foreign_currency_keeps_original_and_company_amounts(self):
        fields, rows = self._data()
        rows[0][fields.index('line_ids/currency_id/id')] = 'base.USD'
        rows[0][fields.index('line_ids/amount_currency')] = '100.00'
        result = self._load(fields, rows)
        self.assertFalse(result['messages'], result['messages'])
        line = self.env.ref('__import__.qing_opening_ar')
        self.assertEqual(line.amount_currency, 100)
        self.assertEqual(line.balance, 700)
        self.assertEqual(line.currency_id, self.env.ref('base.USD'))

    def test_check_is_read_only_and_reports_scope(self):
        move = self._import_move()
        before = move.read(['write_date', 'state'])
        line_before = move.line_ids.read(['write_date', 'debit', 'credit', 'amount_residual'])
        summary = move.with_user(self.manager)._qing_cn_opening_summary()
        self.assertEqual(summary['trade']['asset_receivable']['balance'], 700)
        self.assertEqual(summary['trade']['liability_payable']['balance'], -500)
        self.assertTrue(any('草稿' in issue for issue in summary['issues']))
        action = self.env.ref('qing_account_cn.action_check_opening_entries').with_user(self.manager).with_context(
            active_model='account.move', active_ids=move.ids, active_id=move.id,
        ).run()
        self.assertIn('未与来源试算表核对', action['params']['message'])
        self.assertEqual(before, move.read(['write_date', 'state']))
        self.assertEqual(line_before, move.line_ids.read(['write_date', 'debit', 'credit', 'amount_residual']))

    def test_check_rejects_billing_and_wrong_company(self):
        move = self._import_move()
        with self.assertRaises(AccessError):
            move.with_user(self.billing).action_qing_cn_check_opening_entries()
        other = self.env['res.company'].with_context(chart_template_load=True).create({'name': 'Other opening'})
        with self.assertRaises(UserError):
            move.with_company(other).action_qing_cn_check_opening_entries()
        with self.assertRaises(UserError):
            self.env['account.move'].action_qing_cn_check_opening_entries()

    def test_current_residual_after_partial_reconcile(self):
        move = self._import_move()
        move.action_post()
        receivable = self.env.ref('__import__.qing_opening_ar')
        payment = self.env['account.move'].create({
            'journal_id': self.journal.id, 'date': '2026-09-10',
            'line_ids': [Command.create({
                'account_id': self.company.receivable_account_id.id, 'partner_id': self.partner.id,
                'credit': 300, 'date_maturity': '2026-09-10',
            }), Command.create({'account_id': self.equity.id, 'debit': 300})],
        })
        payment.action_post()
        (receivable | payment.line_ids.filtered(lambda line: line.account_id == receivable.account_id)).reconcile()
        summary = move._qing_cn_opening_summary()
        self.assertEqual(summary['trade']['asset_receivable']['balance'], 700)
        self.assertEqual(summary['trade']['asset_receivable']['residual'], 400)
        self.assertTrue(any('已经发生核销' in issue for issue in summary['issues']))

    def test_import_cannot_target_unauthorized_company(self):
        other = self.env['res.company'].with_context(chart_template_load=True).create({'name': 'Forbidden opening'})
        other_xmlid = other.export_data(['id'])['datas'][0][0]
        fields, rows = self._data()
        rows[0][fields.index('company_id/id')] = other_xmlid
        result = self._load(fields, rows)
        self.assertFalse(result['ids'])
        self.assertTrue(result['messages'])
        self.assertFalse(self.env.ref('__import__.qing_opening_demo', raise_if_not_found=False))

    def _compare_wizard(self, move=None, content=None):
        move = move or self._import_move()
        content = content or ('account_external_id,balance\n'
                              + self.mapping['receivable'] + ',700\n'
                              + self.mapping['payable'] + ',-500\n'
                              + self.mapping['equity'] + ',-200\n')
        return self.env['qing.cn.opening.balance.compare'].with_user(self.manager).create({
            'company_id': self.company.id, 'move_ids': [Command.set(move.ids)],
            'source_date': move.date, 'source_file': BinaryBytes(content.encode('utf-8-sig')),
        })

    def test_source_compare_matches_without_financial_write(self):
        move = self._import_move()
        before = move.line_ids.read(['write_date', 'balance', 'amount_residual'])
        wizard = self._compare_wizard(move)
        action = wizard.action_compare()
        self.assertEqual(action['res_id'], wizard.id)
        self.assertIn('差异或缺失 0 个', wizard.result)
        self.assertIn('包含草稿', wizard.result)
        self.assertEqual(before, move.line_ids.read(['write_date', 'balance', 'amount_residual']))
        self.assertEqual(move.state, 'draft')
        entry_action = move.with_user(self.manager).action_qing_cn_compare_opening_balances()
        self.assertEqual(entry_action['res_model'], wizard._name)
        self.assertEqual(entry_action['context']['default_move_ids'], [Command.set(move.ids)])

    def test_source_compare_difference_and_missing_accounts(self):
        wizard = self._compare_wizard(content='account_external_id,balance\n' + self.mapping['receivable'] + ',699\n')
        rows = wizard._compare_rows(wizard._read_source())
        ar = next(row for row in rows if row[0] == self.company.receivable_account_id)
        self.assertEqual(ar[3], 1)
        self.assertEqual(ar[4], '有差额')
        self.assertEqual(sum(row[4] == '来源缺失' for row in rows), 2)
        extra = self.env['account.account'].search([
            ('company_ids', 'in', self.company.ids), ('id', 'not in', wizard.move_ids.line_ids.account_id.ids),
        ], limit=1)
        extra_id = extra.export_data(['id'])['datas'][0][0]
        wizard.source_file = BinaryBytes(('account_external_id,balance\n' + extra_id + ',0\n').encode())
        rows = wizard._compare_rows(wizard._read_source())
        self.assertEqual(next(row[4] for row in rows if row[0] == extra), '选中分录缺失')

    def test_source_compare_rejects_invalid_files(self):
        wizard = self._compare_wizard()
        for content in (
            'wrong,balance\n', 'account_external_id,balance\n',
            'account_external_id,balance\nmissing.account,0\n',
            'account_external_id,balance\nbase.CNY,0\n',
            'account_external_id,balance\n' + self.mapping['equity'] + ',NaN\n',
            'account_external_id,balance\n' + self.mapping['equity'] + ',1e3\n',
            'account_external_id,balance\n' + (self.mapping['equity'] + ',0\n') * 2,
        ):
            wizard.source_file = BinaryBytes(content.encode())
            with self.assertRaises(UserError):
                wizard.action_compare()
        wizard.source_file = BinaryBytes(b'x' * (1024 * 1024 + 1))
        with self.assertRaises(UserError):
            wizard.action_compare()

    def test_source_compare_scope_and_access(self):
        wizard = self._compare_wizard()
        with self.assertRaises(AccessError):
            wizard.with_user(self.billing).action_compare()
        with self.assertRaises(AccessError):
            self.env['qing.cn.opening.balance.compare'].with_user(self.billing).create({})
        wizard.source_date = '2020-01-01'
        with self.assertRaises(UserError):
            wizard.action_compare()
        wizard.source_date = wizard.move_ids.date
        wizard.move_ids.button_cancel()
        with self.assertRaises(UserError):
            wizard.action_compare()

    def test_source_compare_rejects_foreign_company_account(self):
        wizard = self._compare_wizard()
        other = self.env['res.company'].create({'name': 'Source compare other'})
        account = self.env['account.account'].create({
            'name': 'Other source', 'code': '991', 'account_type': 'equity',
            'company_ids': [Command.set(other.ids)],
        })
        xmlid = account.export_data(['id'])['datas'][0][0]
        wizard.source_file = BinaryBytes(('account_external_id,balance\n' + xmlid + ',0\n').encode())
        with self.assertRaises((UserError, AccessError)):
            wizard.action_compare()

    def test_source_compare_result_clears_when_input_changes(self):
        wizard = self._compare_wizard()
        wizard.action_compare()
        self.assertTrue(wizard.result)
        wizard.source_date = '2020-01-01'
        self.assertFalse(wizard.result)

    def test_source_compare_server_action_and_form(self):
        move = self._import_move()
        action = self.env.ref('qing_account_cn.action_compare_opening_balances').with_user(self.manager).with_context(
            active_model='account.move', active_ids=move.ids,
        ).run()
        model = self.env[action['res_model']].with_user(self.manager).with_context(**action['context'])
        with Form(model) as form:
            form.source_file = self._compare_wizard(move).source_file
        wizard = form.record
        self.assertEqual(wizard.move_ids, move)
        self.assertEqual(wizard.company_id, self.company)
        self.assertEqual(wizard.source_date, move.date)
        wizard.write({'source_file': base64.b64encode(wizard.source_file.content).decode('ascii')})
        wizard.action_compare()
        self.assertIn('差异或缺失 0 个', wizard.result)

    def _batch_rows(self):
        return [
            ['__import__.qing_opening_demo', '__import__.qing_opening_ar', self.mapping['receivable'], self.mapping['partner'], 'base.CNY', '2026-09-15', '700', '700'],
            ['__import__.qing_opening_demo', '__import__.qing_opening_ap', self.mapping['payable'], self.mapping['partner'], 'base.CNY', '2026-09-20', '-500', '-500'],
            ['__import__.qing_opening_demo', '__import__.qing_opening_balance', self.mapping['equity'], '', 'base.CNY', '', '-200', '-200'],
        ]

    def _set_batch_source(self, wizard, rows):
        from ..wizard.opening_balance_compare import BATCH_HEADERS
        stream = io.StringIO()
        writer = csv.writer(stream)
        writer.writerow(BATCH_HEADERS)
        writer.writerows(rows)
        wizard.write({'comparison_mode': 'batch', 'source_file': BinaryBytes(stream.getvalue().encode('utf-8-sig'))})

    def test_batch_complete_and_read_only(self):
        move = self._import_move()
        wizard = self._compare_wizard(move)
        self._set_batch_source(wizard, self._batch_rows())
        before = move.line_ids.read(['write_date', 'balance', 'amount_currency', 'amount_residual'])
        xmlids_before = self.env['ir.model.data'].search_count([])
        wizard.action_compare()
        self.assertIn('逐行差异或缺失：0；选中多余分录：0', wizard.result)
        self.assertIn('来源 1 张凭证、3 行', wizard.result)
        self.assertIn('原币 700 → 700', wizard.result)
        self.assertEqual(before, move.line_ids.read(['write_date', 'balance', 'amount_currency', 'amount_residual']))
        self.assertEqual(xmlids_before, self.env['ir.model.data'].search_count([]))
        self.assertEqual(move.state, 'draft')

    def test_batch_partner_currency_due_and_fx_mismatch(self):
        wizard = self._compare_wizard()
        other_partner = self.env['res.partner'].create({'name': 'Other source partner'})
        rows = self._batch_rows()
        rows[0][3] = other_partner.export_data(['id'])['datas'][0][0]
        rows[0][4:6] = ['base.USD', '2026-10-01']
        rows[0][7] = '100'
        self._set_batch_source(wizard, rows)
        wizard.action_compare()
        for message in ('往来单位不符', '币种不符', '到期日不符', '原币金额不符', '来源缺失', '选中分录缺失'):
            self.assertIn(message, wizard.result)

    def test_batch_original_fx_matches(self):
        fields, data = self._data()
        data[0][fields.index('line_ids/currency_id/id')] = 'base.USD'
        data[0][fields.index('line_ids/amount_currency')] = '100'
        loaded = self._load(fields, data)
        self.assertFalse(loaded['messages'])
        wizard = self._compare_wizard(self.env['account.move'].browse(loaded['ids']))
        rows = self._batch_rows()
        rows[0][4], rows[0][7] = 'base.USD', '100'
        self._set_batch_source(wizard, rows)
        wizard.action_compare()
        self.assertIn('逐行差异或缺失：0', wizard.result)
        self.assertIn('原币 100 → 100', wizard.result)

    def test_batch_missing_and_extra_lines(self):
        wizard = self._compare_wizard()
        rows = self._batch_rows()
        rows[0][0:2] = ['__import__.not_imported_move', '__import__.not_imported_line']
        self._set_batch_source(wizard, rows)
        wizard.action_compare()
        self.assertIn('来源凭证在目标库缺失', wizard.result)
        self.assertIn('来源分录在目标库缺失', wizard.result)
        self.assertIn('选中多余分录：1', wizard.result)
        self.assertIn('来源清单中有 2 张凭证的本位币借贷不平衡', wizard.result)

    def test_batch_detects_entire_omitted_balanced_move(self):
        wizard = self._compare_wizard()
        fields, data = self._data()
        for row in data:
            row[0] = row[0].replace('qing_opening_demo', 'qing_second_move')
            row[fields.index('line_ids/id')] += '_second'
        loaded = self._load(fields, data)
        self.assertFalse(loaded['messages'])
        second = self.env['account.move'].browse(loaded['ids'])
        extra_source = [list(row) for row in self._batch_rows()]
        for row in extra_source:
            row[0] = '__import__.qing_second_move'
            row[1] += '_second'
        self._set_batch_source(wizard, self._batch_rows() + extra_source)
        wizard.action_compare()
        self.assertIn('来源凭证未选中', wizard.result)
        self.assertIn('逐行差异或缺失：3', wizard.result)
        wizard.move_ids = [Command.link(second.id)]
        wizard.action_compare()
        self.assertIn('逐行差异或缺失：0；选中多余分录：0', wizard.result)
        self._set_batch_source(wizard, self._batch_rows())
        wizard.action_compare()
        self.assertIn('有 1 张未出现在来源清单', wizard.result)
        self.assertIn('选中多余分录：3', wizard.result)

    def test_batch_duplicate_alias_and_invalid_source(self):
        wizard = self._compare_wizard()
        line = self.env.ref('__import__.qing_opening_ar')
        self.env['ir.model.data'].create({'module': '__import__', 'name': 'ar_alias', 'model': line._name, 'res_id': line.id})
        rows = self._batch_rows()
        alias = list(rows[0]); alias[1] = '__import__.ar_alias'
        self._set_batch_source(wizard, rows + [alias])
        with self.assertRaises(UserError):
            wizard.action_compare()
        for index, value in [(3, ''), (4, 'base.cn'), (5, '2026-02-30'), (6, 'NaN'), (7, '1e3')]:
            rows = self._batch_rows(); rows[0][index] = value
            self._set_batch_source(wizard, rows)
            with self.assertRaises(UserError):
                wizard.action_compare()

    def test_batch_permissions_and_mode_clears_result(self):
        wizard = self._compare_wizard()
        self._set_batch_source(wizard, self._batch_rows())
        with self.assertRaises(AccessError):
            wizard.with_user(self.billing).action_compare()
        wizard.action_compare()
        wizard.comparison_mode = 'account'
        self.assertFalse(wizard.result)
        with self.assertRaises(UserError):
            wizard.action_compare()

    def test_batch_keeps_original_after_partial_reconcile(self):
        move = self._import_move()
        move.action_post()
        payment = self.env['account.move'].create({
            'journal_id': self.journal.id, 'date': '2026-09-10',
            'line_ids': [Command.create({
                'account_id': self.company.receivable_account_id.id, 'partner_id': self.partner.id,
                'credit': 300, 'date_maturity': '2026-09-10',
            }), Command.create({'account_id': self.equity.id, 'debit': 300})],
        })
        payment.action_post()
        receivable = self.env.ref('__import__.qing_opening_ar')
        (receivable | payment.line_ids.filtered(lambda line: line.account_id == receivable.account_id)).reconcile()
        self.assertEqual(receivable.amount_residual, 400)
        wizard = self._compare_wizard(move)
        self._set_batch_source(wizard, self._batch_rows())
        wizard.action_compare()
        self.assertIn('逐行差异或缺失：0', wizard.result)
        self.assertIn('已经发生核销', wizard.result)
        self.assertIn('原币 700 → 700', wizard.result)

    def test_batch_rejects_foreign_company_mapping(self):
        wizard = self._compare_wizard()
        other = self.env['res.company'].create({'name': 'Batch other'})
        account = self.env['account.account'].create({
            'name': 'Forbidden batch account', 'code': '991', 'account_type': 'equity',
            'company_ids': [Command.set(other.ids)],
        })
        rows = self._batch_rows()
        rows[2][2] = account.export_data(['id'])['datas'][0][0]
        self._set_batch_source(wizard, rows)
        with self.assertRaises((AccessError, UserError)):
            wizard.action_compare()

    def test_batch_form_mode_and_file(self):
        wizard = self._compare_wizard()
        self._set_batch_source(wizard, self._batch_rows())
        action = wizard.move_ids.with_user(self.manager).action_qing_cn_compare_opening_balances()
        model = self.env[action['res_model']].with_user(self.manager).with_context(**action['context'])
        with Form(model) as form:
            form.comparison_mode = 'batch'
            form.source_file = wizard.source_file
        form.record.action_compare()
        self.assertIn('逐行差异或缺失：0', form.record.result)

    def _control_wizard(self):
        wizard = self._compare_wizard()
        self._set_batch_source(wizard, self._batch_rows())
        wizard.write({'control_enabled': True, 'control_move_count': 1, 'control_line_count': 3,
                      'control_debit': '700.00', 'control_credit': '700.00'})
        return wizard

    def test_control_totals_match_and_read_only(self):
        wizard = self._control_wizard()
        before = wizard.move_ids.line_ids.read(['write_date', 'debit', 'credit', 'amount_residual'])
        wizard.action_compare()
        self.assertIn('独立控制数差异：0 项', wizard.result)
        self.assertEqual(before, wizard.move_ids.line_ids.read(['write_date', 'debit', 'credit', 'amount_residual']))
        wizard.control_enabled = False
        self.assertFalse(wizard.result)
        wizard.action_compare()
        self.assertIn('未启用独立控制数', wizard.result)

    def test_control_totals_detect_both_sides_omitting_move(self):
        wizard = self._control_wizard()
        wizard.write({'control_move_count': 2, 'control_line_count': 6,
                      'control_debit': '1400', 'control_credit': '1400'})
        wizard.action_compare()
        self.assertIn('逐行差异或缺失：0', wizard.result)
        self.assertIn('独立控制数差异：4 项', wizard.result)
        self.assertIn('来源清单不符；选中凭证不符', wizard.result)

    def test_control_totals_detect_equal_count_amount_errors(self):
        wizard = self._control_wizard()
        wizard.write({'control_debit': '701', 'control_credit': '702'})
        wizard.action_compare()
        self.assertIn('独立控制数差异：2 项', wizard.result)
        self.assertIn('独立控制数本身借贷不平衡', wizard.result)
        for field, value in [('control_move_count', 2), ('control_line_count', 4),
                             ('control_debit', '702'), ('control_credit', '703')]:
            wizard.write({field: value})
            self.assertFalse(wizard.result)
            wizard.action_compare()

    def test_control_totals_validate_inputs_and_precision(self):
        wizard = self._control_wizard()
        for values in [{'control_move_count': 0}, {'control_line_count': -1},
                       {'control_debit': ''}, {'control_debit': 'NaN'},
                       {'control_credit': '1e3'}, {'control_credit': '-1'},
                       {'control_credit': '1,000'}]:
            with self.env.cr.savepoint():
                originals = {key: wizard[key] for key in values}
                wizard.write(values)
                with self.assertRaises(UserError):
                    wizard.action_compare()
                wizard.write(originals)
        wizard.write({'control_debit': '700.001', 'control_credit': '700.001'})
        wizard.action_compare()
        self.assertIn('独立控制数差异：0 项', wizard.result)
        wizard.write({'control_debit': '0', 'control_credit': '0'})
        wizard.action_compare()
        self.assertIn('独立控制数差异：2 项', wizard.result)

    def test_control_totals_compare_both_sides_separately(self):
        wizard = self._control_wizard()
        rows = self._batch_rows()
        rows[0][6:8] = ['800', '800']
        rows[2][6:8] = ['-300', '-300']
        self._set_batch_source(wizard, rows)
        wizard.action_compare()
        self.assertIn('借方总额 | 700.00 | 800 | 700.0 | 来源清单不符', wizard.result)
        wizard.write({'control_debit': '800', 'control_credit': '800'})
        wizard.action_compare()
        self.assertIn('借方总额 | 800 | 800 | 700.0 | 选中凭证不符', wizard.result)

    def test_control_totals_form_and_permissions(self):
        wizard = self._control_wizard()
        with Form(wizard) as form:
            form.control_enabled = True
            form.control_move_count = 1
            form.control_line_count = 3
            form.control_debit = '700'
            form.control_credit = '700'
        form.record.action_compare()
        self.assertIn('独立控制数差异：0 项', form.record.result)
        with self.assertRaises(AccessError):
            wizard.with_user(self.billing).action_compare()
