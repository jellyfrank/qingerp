import logging

from odoo import SUPERUSER_ID, api


_logger = logging.getLogger(__name__)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    Repair = env['repair.order'].with_context(active_test=False)
    Photo = env['qing.repair.photo']
    for kind in ('before', 'after'):
        field_name = 'qing_photo_' + kind
        marker = 'qing_photo_legacy_' + kind + '_migrated'
        last_id = 0
        missing_count = 0
        while True:
            repairs = Repair.search([(field_name, '!=', False), ('id', '>', last_id)],
                                    order='id', limit=100)
            if not repairs:
                break
            last_id = repairs[-1].id
            existing = set(Photo.search([
                ('repair_id', 'in', repairs.ids), ('legacy_source', '=', kind),
            ]).mapped('repair_id').ids)
            completed = set(existing)
            values = []
            for repair in repairs:
                if repair.id in existing:
                    continue
                image = repair[field_name]
                if not image or not image.content:
                    missing_count += 1
                    continue
                values.append({
                    'repair_id': repair.id,
                    'kind': kind,
                    'legacy_source': kind,
                    'name': env._('Before Repair') if kind == 'before' else env._('After Repair'),
                    'image_1920': image,
                })
                completed.add(repair.id)
            if values:
                Photo.create(values)
            repairs.filtered(lambda repair: repair.id in completed and not repair[marker]).write({marker: True})
        if missing_count:
            _logger.warning('Skipped %s %s repair photos whose stored files are unavailable', missing_count, kind)
