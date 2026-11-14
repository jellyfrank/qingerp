import base64
import importlib.util
import io
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from odoo import Command
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, new_test_user, tagged
from odoo.tools.binary import EMPTY_BINARY


@tagged('post_install', '-at_install')
class TestRepairPhotos2606(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env['res.partner'].create({'name': '2606 Repair Customer'})
        cls.warehouse = cls.env['stock.warehouse'].search([('company_id', '=', cls.env.company.id)], limit=1)
        cls.images = []
        for color in ('red', 'blue', 'green', 'yellow'):
            stream = io.BytesIO()
            Image.new('RGB', (32, 32), color).save(stream, format='PNG')
            cls.images.append(base64.b64encode(stream.getvalue()).decode())

    def _repair(self, **values):
        return self.env['repair.order'].create({'partner_id': self.partner.id, 'picking_type_id': self.warehouse.repair_type_id.id, **values})

    def test_separate_galleries_and_no_copy(self):
        repair = self._repair(qing_photo_before_ids=[Command.create({'kind': 'before', 'image_1920': self.images[0]}), Command.create({'kind': 'before', 'image_1920': self.images[1]})], qing_photo_after_ids=[Command.create({'kind': 'after', 'image_1920': self.images[2]}), Command.create({'kind': 'after', 'image_1920': self.images[3]})])
        self.assertEqual((len(repair.qing_photo_before_ids), len(repair.qing_photo_after_ids)), (2, 2))
        self.assertFalse(repair.copy().qing_photo_before_ids)
        self.assertFalse(repair.copy().qing_photo_after_ids)

    def test_legacy_migration_is_idempotent_and_deletion_is_final(self):
        repair = self._repair(qing_photo_before=self.images[0], qing_photo_after=self.images[1])
        path = Path(__file__).resolve().parents[1] / 'migrations/20.0.1.0.5/post-migrate.py'
        spec = importlib.util.spec_from_file_location('qing_2606_photo_migrate', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.migrate(self.cr, '20.0.1.0.4')
        module.migrate(self.cr, '20.0.1.0.4')
        repair.invalidate_recordset()
        self.assertEqual((len(repair.qing_photo_before_ids), len(repair.qing_photo_after_ids)), (1, 1))
        self.assertEqual(repair.qing_photo_before_ids.legacy_source, 'before')
        before = Image.open(io.BytesIO(repair.qing_photo_before_ids.image_1920.content)).convert('RGB')
        after = Image.open(io.BytesIO(repair.qing_photo_after_ids.image_1920.content)).convert('RGB')
        self.assertEqual(before.getpixel((0, 0)), (255, 0, 0))
        self.assertEqual(after.getpixel((0, 0)), (0, 0, 255))
        repair.qing_photo_before_ids.unlink()
        self.assertTrue(repair.qing_photo_before)
        self.assertTrue(repair.qing_photo_legacy_before_migrated)
        self.assertFalse(repair.qing_photo_before_ids)
        self.assertTrue(repair.qing_photo_after)

    def test_legacy_migration_skips_missing_filestore_content(self):
        missing = self._repair(qing_photo_before=self.images[0], qing_photo_after=self.images[1])
        available = self._repair(qing_photo_before=self.images[2])
        attachments = self.env['ir.attachment'].search([
            ('res_model', '=', 'repair.order'),
            ('res_id', '=', missing.id),
            ('res_field', 'in', ['qing_photo_before', 'qing_photo_after']),
        ])
        self.assertEqual(len(attachments), 2)
        missing_ids = set(attachments.ids)
        original_read = type(attachments)._file_read

        def read_missing(record):
            if record.id in missing_ids:
                return EMPTY_BINARY
            return original_read(record)

        path = Path(__file__).resolve().parents[1] / 'migrations/20.0.1.0.5/post-migrate.py'
        spec = importlib.util.spec_from_file_location('qing_2606_missing_photo_migrate', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with patch.object(type(attachments), '_file_read', read_missing):
            self.env.invalidate_all()
            self.assertTrue(all(not attachment.raw.content for attachment in attachments))
            module.migrate(self.cr, '20.0.1.0.4')
            missing.invalidate_recordset()
            available.invalidate_recordset()
            self.assertFalse(missing.qing_photo_before_ids)
            self.assertFalse(missing.qing_photo_after_ids)
            self.assertFalse(missing.qing_photo_legacy_before_migrated)
            self.assertFalse(missing.qing_photo_legacy_after_migrated)
            self.assertEqual(len(available.qing_photo_before_ids), 1)
        self.assertEqual(len(attachments.exists()), 2)
        self.env.invalidate_all()
        module.migrate(self.cr, '20.0.1.0.4')
        missing.invalidate_recordset()
        available.invalidate_recordset()
        self.assertEqual(len(missing.qing_photo_before_ids), 1)
        self.assertEqual(len(missing.qing_photo_after_ids), 1)
        self.assertEqual(len(available.qing_photo_before_ids), 1)
        self.assertTrue(missing.qing_photo_legacy_before_migrated)
        self.assertTrue(missing.qing_photo_legacy_after_migrated)

    def test_photo_access_requires_repair_permissions(self):
        repair = self._repair(qing_photo_before_ids=[Command.create({'kind': 'before', 'image_1920': self.images[0]})])
        photo = repair.qing_photo_before_ids
        portal = new_test_user(self.env, login='repair_photo_2606_portal', groups='base.group_portal')
        with self.assertRaises(AccessError), self.cr.savepoint():
            photo.with_user(portal).read(['name'])

    def test_stock_user_can_manage_photos_on_accessible_repair(self):
        repair = self._repair()
        operator = new_test_user(self.env, login='repair_photo_2606_operator', groups='stock.group_stock_user')
        photo = self.env['qing.repair.photo'].with_user(operator).create({
            'repair_id': repair.id, 'kind': 'before', 'image_1920': self.images[0],
        })
        self.assertTrue(photo.read(['name']))
        photo.write({'name': 'Operator photo'})
        self.assertEqual(photo.name, 'Operator photo')
        photo.unlink()
