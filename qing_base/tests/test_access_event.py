import json
import os
import queue
from collections import Counter

import psycopg2
from unittest.mock import Mock, patch

from odoo import Command
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, new_test_user, tagged

from ..tools import access_events


@tagged('post_install', '-at_install')
class TestAccessEvent(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.other_company = cls.env['res.company'].create({'name': 'Private company'})
        cls.viewer = new_test_user(
            cls.env, login='diagnostic_company_viewer',
            groups='base.group_user,qing_base.group_access_diagnostic',
            company_id=cls.env.company.id, company_ids=[Command.set(cls.env.company.ids)])
        cls.other = new_test_user(cls.env, login='ordinary_diagnostic_viewer', groups='base.group_user')

    def _event(self, company, key):
        self.env.cr.execute("""
            INSERT INTO qing_access_event
            (event_key, event_day, event_type, impact, user_id, company_id,
             allowed_companies_raw, active_companies_raw, model_name, operation, cause,
             entry, first_seen, last_seen, occurrences)
            VALUES (%s, CURRENT_DATE, 'restricted', 'masked_readonly', %s, %s, %s, %s,
                    'sale.order', 'read', 'unknown', '/web/dataset/call_kw', NOW(), NOW(), 1)
            RETURNING id
        """, (key, self.viewer.id, company.id,
              json.dumps([self.env.company.id, self.other_company.id]),
              json.dumps([self.env.company.id, self.other_company.id])))
        return self.env['qing.access.event'].browse(self.env.cr.fetchone()[0])

    def test_diagnostic_scope_and_immutable_records(self):
        visible = self._event(self.env.company, 'test-visible-' + str(self.env.uid))
        hidden = self._event(self.other_company, 'test-hidden-' + str(self.env.uid))
        value = visible.with_user(self.viewer).read(['allowed_companies', 'active_companies'])[0]
        self.assertEqual(value['allowed_companies'], str(self.env.company.id))
        self.assertEqual(value['active_companies'], str(self.env.company.id))
        with self.assertRaises(AccessError):
            hidden.with_user(self.viewer).read(['event_type'])
        with self.assertRaises(AccessError):
            visible.with_user(self.other).read(['event_type'])
        with self.assertRaises(AccessError):
            visible.with_user(self.viewer).read(['allowed_companies_raw'])
        with self.assertRaises(AccessError):
            visible.with_user(self.viewer).write({'occurrences': 100})

    def test_only_nonempty_real_markers_count(self):
        self.assertTrue(access_events.contains_restricted(
            {'records': [{'lines': [{'__qing_restricted_fields': ['cost']}]}]}))
        self.assertFalse(access_events.contains_restricted(
            {'records': [{'__qing_restricted_fields': []}, {'text': 'not a marker'}]}))

    def test_matching_events_are_batched_without_losing_occurrences(self):
        class TwoEventQueue:
            def __init__(self):
                self.remaining = 2
                self.completed = 0
            def get(self, timeout=None):
                if self.remaining:
                    self.remaining -= 1
                    return ('isolated-test', {'user_id': 1, 'event_type': 'denied'})
                if timeout is not None and timeout < 1:
                    raise queue.Empty()
                raise StopIteration()
            def task_done(self):
                self.completed += 1
        events = TwoEventQueue()
        with patch.object(access_events, '_events', events), \
             patch.object(access_events, '_write') as write:
            with self.assertRaises(StopIteration):
                access_events._run()
        write.assert_called_once_with('isolated-test', {'user_id': 1, 'event_type': 'denied'}, 2)
        self.assertEqual(events.completed, 2)

    def test_storage_and_connection_failures_are_counted_by_worker(self):
        class OneEventQueue:
            def __init__(self):
                self.called = False
            def get(self, timeout=None):
                if self.called:
                    if timeout is not None and timeout < 1:
                        raise queue.Empty()
                    raise StopIteration()
                self.called = True
                return ('isolated-test', {'user_id': 1})
            def task_done(self):
                pass
        for failure in (psycopg2.OperationalError('connection unavailable'),
                        psycopg2.errors.QueryCanceled('statement timed out')):
            before = access_events._drops['write_failed']
            with patch.object(access_events, '_events', OneEventQueue()), \
                 patch.object(access_events, '_ensure_worker'), \
                 patch.object(access_events, '_write', side_effect=failure):
                with self.assertRaises(StopIteration):
                    access_events._run()
            self.assertEqual(access_events._drops['write_failed'], before + 1)

    def test_worker_start_failure_never_raises_to_caller(self):
        before = access_events._drops['worker_start_failed']
        with patch.object(access_events, '_worker', None), \
             patch.object(access_events, '_worker_pid', None), \
             patch.object(access_events.threading.Thread, 'start', side_effect=RuntimeError('unavailable')):
            access_events.enqueue(self.env.cr.dbname, {'user_id': self.viewer.id})
        self.assertEqual(access_events._drops['worker_start_failed'], before + 1)

    def test_drop_summary_reports_current_process_totals(self):
        with patch.object(access_events, '_drops', Counter(queue_full=7)), \
             patch.object(access_events, '_reported_drops', Counter()), \
             patch.object(access_events._logger, 'warning') as warning:
            access_events._report_drops()
            warning.assert_called_once_with(
                "Permission diagnostic drops (process totals): %s", {'queue_full': 7})
            access_events._report_drops()
            self.assertEqual(warning.call_count, 1)

    def test_full_diagnostic_queue_never_raises_to_caller(self):
        fake = Mock()
        fake.put_nowait.side_effect = queue.Full
        worker = Mock()
        worker.is_alive.return_value = True
        event = {'user_id': self.viewer.id}
        with patch.object(access_events, '_events', fake), \
             patch.object(access_events, '_worker', worker), \
             patch.object(access_events, '_worker_pid', os.getpid()):
            before = access_events._drops['queue_full']
            access_events.enqueue(self.env.cr.dbname, event)
            self.assertEqual(access_events._drops['queue_full'], before + 1)
