from odoo.tests import TransactionCase, new_test_user, tagged


@tagged('post_install', '-at_install')
class TestProductStatistics(TransactionCase):
    """Product statistics group by fields Odoo can group, such as the category of a variant."""

    def test_products_group_by_category(self):
        user = new_test_user(
            self.env, login='qing-ai-product-statistics', lang='en_US',
            groups='base.group_user,sales_team.group_sale_salesman',
        )
        categories = self.env['product.category'].create([{'name': 'Stat Chairs'}, {'name': 'Stat Desks'}])
        self.env['product.product'].create([
            {'name': 'Stat Chair A', 'categ_id': categories[0].id, 'sale_ok': True},
            {'name': 'Stat Chair B', 'categ_id': categories[0].id, 'sale_ok': True},
            {'name': 'Stat Desk', 'categ_id': categories[1].id, 'sale_ok': True},
        ])
        tool = self.env.ref('qing_ai_sale.tool_product_group').with_user(user)
        report = tool._orm_query({
            'groupby': ['categ_id'], 'aggregates': ['__count'],
            'domain': [['name', 'ilike', 'Stat ']],
        })
        self.assertIn('| Stat Chairs | 2 |', report)
        self.assertIn('| Stat Desks | 1 |', report)

    def test_an_unavailable_choice_is_named(self):
        tool = self.env.ref('qing_ai_sale.tool_product_group')
        with self.assertRaisesRegex(Exception, 'not available: no_such_field'):
            tool._orm_query({'groupby': ['no_such_field']})
