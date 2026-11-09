from odoo import api, models, _
from odoo.exceptions import AccessError


class DiscussChannelMember(models.Model):
    _inherit = 'discuss.channel.member'

    @api.constrains('channel_id', 'partner_id', 'guest_id')
    def _check_qing_members(self):
        for member in self.sudo():
            conversation = member.channel_id.qing_conversation_id
            if conversation and (member.guest_id or member.partner_id not in conversation._discuss_operators().partner_id):
                raise AccessError(_('Only assigned WhatsApp operators may join this chat.'))
