from odoo import fields, models


class ChannelCursor(models.Model):
    _name = 'qing.channel.cursor'
    _description = 'Channel Reconciliation Cursor'
    _check_company_auto = True

    company_id = fields.Many2one(related='store_id.company_id', store=True, index=True, precompute=True)
    page_cursor = fields.Char(copy=False)
    page_rows = fields.Integer(copy=False)
    page_restarts = fields.Integer(copy=False)
    store_id = fields.Many2one('qing.channel.store', required=True, ondelete='restrict', index=True)
    stream = fields.Char(required=True)
    through = fields.Datetime(required=True, copy=False)
    window_end = fields.Datetime(copy=False)
    window_start = fields.Datetime(copy=False)

    _stream_unique = models.UniqueIndex('(store_id, stream)')

    def _finish_window(self, through):
        self.write({
            'through': through, 'window_start': False, 'window_end': False,
            'page_cursor': False, 'page_rows': 0, 'page_restarts': 0,
        })
