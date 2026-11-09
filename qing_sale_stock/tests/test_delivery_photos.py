from io import BytesIO

from PIL import Image

from odoo import Command
from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, tagged
from odoo.tools.binary import BinaryBytes


@tagged('post_install', '-at_install')
class TestDeliveryPhotos(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = cls.env['res.users'].create({
            'name': 'Photo Salesperson', 'login': 'photo-salesperson',
            'group_ids': [Command.set([
                cls.env.ref('base.group_user').id,
                cls.env.ref('sales_team.group_sale_salesman').id,
            ])],
        })
        cls.partner = cls.env['res.partner'].create({'name': 'Photo Customer'})
        cls.order = cls.env['sale.order'].create({
            'partner_id': cls.partner.id, 'user_id': cls.user.id,
        })
        cls.other = cls.env['sale.order'].create({
            'partner_id': cls.partner.id, 'user_id': cls.env.user.id,
        })
        stream = BytesIO()
        Image.new('RGB', (2000, 1000), 'blue').save(stream, format='PNG')
        cls.image = BinaryBytes(stream.getvalue())

    def _photo(self, order, **vals):
        return self.env['qing.sale.delivery.photo'].create({
            'order_id': order.id, 'image_1920': self.image, **vals,
        })

    def test_multiple_images_resize_order_and_copy(self):
        self._photo(self.order, sequence=20)
        first = self._photo(self.order, sequence=10)
        self._photo(self.order, sequence=30)
        self.assertEqual(len(self.order.qing_delivery_photo_ids), 3)
        self.assertEqual(self.order.qing_delivery_photo_ids[0], first)
        image = Image.open(BytesIO(first.image_1920.content))
        self.assertEqual(image.size, (1600, 800))
        self.assertFalse(self.order.copy().qing_delivery_photo_ids)

    def test_photo_permissions_follow_order(self):
        photo = self._photo(self.other)
        with self.assertRaises(AccessError):
            photo.with_user(self.user).read(['image_1920'])
        with self.assertRaises(AccessError):
            photo.with_user(self.user).write({'name': 'Forbidden'})
        with self.assertRaises(AccessError):
            photo.with_user(self.user).unlink()
        Photo = self.env['qing.sale.delivery.photo'].with_user(self.user)
        with self.assertRaises(AccessError):
            Photo.create({'order_id': self.other.id, 'image_1920': self.image})
        own = Photo.create({'order_id': self.order.id, 'image_1920': self.image})
        own.write({'name': 'Allowed'})
        with self.assertRaises(AccessError):
            own.write({'order_id': self.other.id})
        own.unlink()

    def test_invalid_image_and_portal_denied(self):
        with self.assertRaises(UserError):
            self._photo(self.order, image_1920=BinaryBytes(b'not an image'))
        photo = self._photo(self.order)
        portal = self.env['res.users'].create({
            'name': 'Photo Portal', 'login': 'photo-portal',
            'group_ids': [Command.set([self.env.ref('base.group_portal').id])],
        })
        with self.assertRaises(AccessError):
            photo.with_user(portal).read(['image_1920'])
