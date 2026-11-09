import json
from copy import deepcopy

from odoo.tests import TransactionCase, tagged

from ..tools.presentation import style_dashboard


def contents(snapshot):
    return [{key: value.get('content', '') if isinstance(value, dict) else value
             for key, value in sheet.get('cells', {}).items()
             if (value.get('content', '') if isinstance(value, dict) else value) != ''}
            for sheet in snapshot.get('sheets', [])]


@tagged('post_install', '-at_install')
class TestDashboardPresentation(TransactionCase):
    def test_native_dashboards_preserve_content_and_sources(self):
        dashboards = self.env['spreadsheet.dashboard'].search([])
        self.assertTrue(dashboards)
        for dashboard in dashboards:
            original = json.loads(dashboard.spreadsheet_data)
            frozen = deepcopy(original)
            styled = style_dashboard(original)
            self.assertEqual(original, frozen)
            self.assertEqual(contents(original), contents(styled))
            for key in ('pivots', 'lists', 'globalFilters'):
                self.assertEqual(original.get(key), styled.get(key))
            self.assertEqual(style_dashboard(styled), styled)
            sample = dashboard._get_sample_dashboard()
            if sample:
                self.assertEqual(sample['settings']['qingDashboardPresentation'], 1)
            saved = dashboard.spreadsheet_data
            serialized = json.loads(dashboard._get_serialized_readonly_dashboard())
            self.assertEqual(serialized['snapshot']['settings']['qingDashboardPresentation'], 1)
            self.assertEqual(dashboard.spreadsheet_data, saved)

    def test_empty_styles_anchored_figures_and_unused_grid(self):
        original = {'sheets': [{'id': 'sheet1', 'name': 'Dashboard', 'rowNumber': 20,
            'colNumber': 100, 'cells': {'A8': '=ODOO.LIST(1,1,"name")'},
            'styles': {}, 'figures': [{'id': 'chart1', 'col': 0, 'row': 0,
                'offset': {'x': 0, 'y': 0}, 'width': 800, 'height': 150,
                'data': {'type': 'chart'}}]}]}
        result = style_dashboard(original)
        self.assertEqual(contents(original), contents(result))
        self.assertEqual(result['sheets'][0]['figures'][0]['width'], 800)
        self.assertIn('col', result['sheets'][0]['figures'][0])
        self.assertNotIn('x', result['sheets'][0]['figures'][0])

    def test_empty_sheet_and_existing_customer_adapter(self):
        for original in ({'sheets': []}, {'sheets': [{'rowNumber': 0, 'colNumber': 0}]},
                         {'settings': {'bxrDashboardPresentation': 1}, 'sheets': []}):
            frozen = deepcopy(original)
            style_dashboard(original)
            self.assertEqual(original, frozen)
