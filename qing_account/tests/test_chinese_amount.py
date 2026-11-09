from decimal import Decimal

from odoo.tests import TransactionCase, tagged

from ..tools.chinese_amount import cncurrency


@tagged('post_install', '-at_install')
class TestChineseAmount(TransactionCase):

    def test_amounts_and_zero_groups(self):
        cases = {
            '0': '零元整', '700': '柒佰元整', '1002.53': '壹仟零贰元伍角叁分',
            '-700': '负柒佰元整', '0.01': '壹分', '0.10': '壹角整',
            '10001': '壹万零壹元整', '100000001': '壹亿零壹元整',
            '100010001': '壹亿零壹万零壹元整', '100100000': '壹亿零壹拾万元整',
            '1000000000000': '壹万亿元整',
        }
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(cncurrency(value), expected)

    def test_decimal_float_and_rounding(self):
        for value in [700, 700.0, '700', Decimal('700'), Decimal('700.000')]:
            self.assertEqual(cncurrency(value), '柒佰元整')
        self.assertEqual(cncurrency(Decimal('0.1')), '壹角整')
        for value in [1.005, '1.005', Decimal('1.005')]:
            self.assertEqual(cncurrency(value), '壹元零壹分')
        self.assertEqual(cncurrency('-0.004'), '零元整')
        self.assertEqual(cncurrency('-0.005'), '负壹分')
        self.assertEqual(cncurrency('9.995'), '壹拾元整')

    def test_modes(self):
        self.assertEqual(cncurrency(700, capital=False), '七百元整')
        self.assertEqual(cncurrency(0, capital=False), '〇元整')
        self.assertEqual(cncurrency(700, classical=False), '柒佰圆整')
        self.assertEqual(cncurrency(-700, prefix=True), '人民币负柒佰元整')

    def test_invalid_values_and_limits(self):
        for value in [None, True, False, '', 'oops', 'NaN', 'Infinity', '-Infinity',
                      '1e100', '10000000000000000', '9999999999999999.995']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                cncurrency(value)
        self.assertTrue(cncurrency('9999999999999999.99').endswith('玖分'))

    def test_qweb_options(self):
        widget = self.env['ir.qweb.field.qing_chinese_amount']
        self.assertEqual(widget.value_to_html(Decimal('700'), {}), '柒佰元整')
        self.assertEqual(widget.value_to_html(700, {'capital': False, 'prefix': True}), '人民币七百元整')
        view = self.env['ir.ui.view'].create({
            'name': 'Qing amount widget integration test', 'type': 'qweb',
            'arch_db': '''<t><span t-field="currency.rounding"
                t-options="{'widget': 'qing_chinese_amount'}"/>
                <span t-out="value" t-options="{'widget': 'qing_chinese_amount', 'capital': False}"/></t>''',
        })
        rendered = str(self.env['ir.qweb']._render(view.id, {
            'currency': self.env.ref('base.CNY'), 'value': Decimal('700'),
        }))
        self.assertIn('壹分', rendered)
        self.assertIn('七百元整', rendered)
