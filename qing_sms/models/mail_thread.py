import json

import phonenumbers

from odoo import Command, models, _
from odoo.exceptions import AccessError, UserError


def _mainland_number(number):
    try:
        parsed = phonenumbers.parse(number or '', 'CN')
        return parsed.country_code == 86
    except phonenumbers.NumberParseException:
        return bool(number and (str(number).startswith(('+86', '0086', '1'))))


class MailThread(models.AbstractModel):
    _inherit = 'mail.thread'

    def _notify_thread(self, message, **kwargs):
        recipients = super()._notify_thread(message, **kwargs)
        if kwargs.get('qing_domestic_template_id') and not kwargs.get('qing_domestic_business_event') and self._is_notification_scheduled(kwargs.get('scheduled_date')):
            schedule = self.env['mail.message.schedule'].sudo().search([('mail_message_id', '=', message.id)], limit=1)
            if schedule:
                event = self._qing_sms_capture_native(message, recipients, kwargs, schedule.scheduled_datetime)
                parameters = json.loads(schedule.notification_parameters or '{}')
                for key in ('qing_domestic_values', 'qing_domestic_sender_user_id', 'qing_domestic_binding_id'):
                    parameters.pop(key, None)
                parameters['qing_domestic_frozen'] = True
                if event:
                    parameters['qing_domestic_event_id'] = event.id
                schedule.write({'notification_parameters': json.dumps(parameters)})
        return recipients

    def _message_sms_with_template(self, template=False, template_xmlid=False,
                                   template_fallback='', partner_ids=False, **kwargs):
        self.ensure_one()
        if not template and template_xmlid:
            template = self.env.ref(template_xmlid, raise_if_not_found=False)
        if template and template.model != self._name:
            raise UserError(_('SMS template belongs to another business model.'))
        if template:
            company = self.company_id if 'company_id' in self._fields and self.company_id else self.env.company
            domain = [('template_id', '=', template.id), ('company_id', '=', company.id),
                      ('active', '=', True), ('accept_new', '=', True)]
            bindings = self.env['qing.sms.binding'].sudo().search(domain)
            if len(bindings) > 1:
                raise UserError(_('More than one domestic binding matches this SMS template.'))
            if bindings and bindings.remote_template_id.category == 'otp':
                raise UserError(_('One-time codes require the dedicated secure SMS flow.'))
            kwargs.update(qing_domestic_template_id=template.id,
                          qing_domestic_binding_id=bindings.id if bindings else False,
                          qing_domestic_sender_user_id=self.env.user.id)
        return super()._message_sms_with_template(template=template, template_xmlid=template_xmlid,
            template_fallback=template_fallback, partner_ids=partner_ids, **kwargs)

    def _qing_sms_targets(self, recipients_data, sms_numbers, overrides):
        partners = self.env['res.partner'].sudo().browse([item['id'] for item in recipients_data
                                                          if item.get('notif') == 'sms' and item.get('id')])
        targets = []
        for partner in partners:
            number = overrides.get(partner.id) or overrides.get(str(partner.id)) or partner.phone
            targets.append((partner.id, partner._phone_format(number=number) or number))
        for number in sms_numbers or []:
            targets.append((False, self._phone_format(number=number) or number if number else False))
        domestic, native_ids, native_numbers, seen = [], set(), [], set()
        for partner_id, number in targets:
            key = (number or '').replace(' ', '').replace('-', '')
            if key in seen:
                continue
            seen.add(key)
            if _mainland_number(number):
                domestic.append({'slot': len(domestic), 'partner_id': partner_id, 'number': number})
            elif partner_id:
                native_ids.add(partner_id)
            else:
                native_numbers.append(number)
        return domestic, native_ids, native_numbers

    def _qing_sms_capture_native(self, message, recipients_data, kwargs, not_before=False):
        domestic, native_ids, native_numbers = self._qing_sms_targets(
            recipients_data, kwargs.get('sms_numbers'), kwargs.get('sms_pid_to_number') or {})
        if not domestic:
            return self.env['qing.sms.event']
        company = self.company_id if 'company_id' in self._fields and self.company_id else self.env.company
        sender = self.env['res.users'].sudo().browse(kwargs.get('qing_domestic_sender_user_id')).exists()
        binding = self.env['qing.sms.binding'].sudo().browse(kwargs.get('qing_domestic_binding_id')).exists()
        template = self.env['sms.template'].sudo().browse(kwargs['qing_domestic_template_id']).exists()
        error, frozen = False, {}
        try:
            if not sender or sender != message.create_uid or not sender.active or company not in sender.company_ids or not sender.has_group('qing_sms.group_sms_user'):
                raise AccessError(_('The native SMS sender has no company or SMS permission.'))
            record = self.with_user(sender).with_company(company)
            record.check_access('read')
            if not binding or binding.company_id != company or binding.template_id != template:
                raise UserError(_('Domestic SMS binding does not match the message template and company.'))
            if binding.remote_template_id.category == 'otp':
                raise UserError(_('One-time codes require the dedicated secure SMS flow.'))
            values = {}
            supplied = kwargs.get('qing_domestic_values') or {}
            for variable in binding.variable_ids.filtered(lambda item: item.source == 'input'):
                if variable.key in supplied:
                    values[variable.key] = supplied[variable.key]
                elif variable.key in record._fields:
                    values[variable.key] = binding._read_field(record, variable.key)
                else:
                    raise UserError(_('Native SMS variable has no matching readable business field.'))
            normalized = []
            for target in domestic:
                number, parameters, body = binding.with_user(sender).with_company(company)._prepare_payload(
                    target['number'], values, self.id)
                normalized.append({**target, 'number': number})
            domestic = normalized
            frozen = {'parameters': parameters, 'body': body}
        except (AccessError, UserError) as caught:
            error = str(caught)[:250]
        return self.env['qing.sms.event']._capture_native(self, message, template, binding, sender,
            domestic, frozen, not_before=not_before, preparation_error=error)

    def _get_notify_valid_parameters(self):
        return super()._get_notify_valid_parameters() | {
            'qing_domestic_template_id', 'qing_domestic_binding_id',
            'qing_domestic_sender_user_id', 'qing_domestic_event_key',
            'qing_domestic_values', 'qing_domestic_event_id', 'qing_domestic_frozen',
            'qing_domestic_business_event',
        }

    def _notify_thread_by_sms(self, message, recipients_data, sms_content=None,
                              sms_numbers=None, sms_pid_to_number=None, put_in_queue=False, **kwargs):
        template_id = kwargs.pop('qing_domestic_template_id', False)
        binding_id = kwargs.pop('qing_domestic_binding_id', False)
        sender_id = kwargs.pop('qing_domestic_sender_user_id', False)
        event_key = kwargs.pop('qing_domestic_event_key', False)
        supplied_values = kwargs.pop('qing_domestic_values', {}) or {}
        event_id = kwargs.pop('qing_domestic_event_id', False)
        frozen = kwargs.pop('qing_domestic_frozen', False)
        business_event = kwargs.pop('qing_domestic_business_event', False)
        if not template_id:
            return super()._notify_thread_by_sms(message, recipients_data, sms_content=sms_content,
                sms_numbers=sms_numbers, sms_pid_to_number=sms_pid_to_number, put_in_queue=put_in_queue, **kwargs)
        self.ensure_one()
        company = self.company_id if 'company_id' in self._fields and self.company_id else self.env.company
        overrides = sms_pid_to_number or {}
        domestic, native_ids, native_numbers = self._qing_sms_targets(recipients_data, sms_numbers, overrides)
        if business_event:
            native_data = [item for item in recipients_data if item.get('notif') != 'sms' or item.get('id') in native_ids]
            if native_ids or native_numbers:
                return super()._notify_thread_by_sms(message, native_data, sms_content=sms_content,
                    sms_numbers=native_numbers, sms_pid_to_number=sms_pid_to_number,
                    put_in_queue=put_in_queue, **kwargs)
            return True
        if domestic or event_id:
            if event_id:
                event = self.env['qing.sms.event'].sudo().browse(event_id).exists()
                if not event or event.event_code != 'native.template' or event.mail_message_id != message or event.company_id != company or event.native_template_id.id != template_id:
                    raise UserError(_('Scheduled domestic SMS event is invalid.'))
            elif not frozen:
                event = self._qing_sms_capture_native(message, recipients_data, {
                    'qing_domestic_sender_user_id': sender_id, 'qing_domestic_binding_id': binding_id,
                    'qing_domestic_template_id': template_id, 'qing_domestic_values': supplied_values,
                    'sms_numbers': sms_numbers, 'sms_pid_to_number': overrides})
            else:
                event = self.env['qing.sms.event']
                for target in domestic:
                    self._qing_sms_blocked_notification(message, target['partner_id'], target['number'])
            if not event:
                domestic = []
            else:
                targets = event.recipient_snapshot or []
                sender = event.sender_user_id
                binding = event.binding_id
                for target in targets:
                    partner_id, number = target['partner_id'], target['number']
                    if event.state == 'blocked' or not binding:
                        self._qing_sms_blocked_notification(message, partner_id, number)
                        continue
                    try:
                        if not sender.active or company not in sender.company_ids or not sender.has_group('qing_sms.group_sms_user'):
                            raise AccessError(_('The scheduled sender no longer has SMS permission.'))
                        record = self.with_user(sender).with_company(company)
                        record.check_access('read')
                        binding.with_user(sender).with_company(company)._check_usable(for_enqueue=False)
                        if not binding.accept_new and (not binding.retired_at or event.binding_frozen_at > binding.retired_at):
                            raise UserError(_('The native SMS binding was retired before capture.'))
                        key = (f'{event_key}:to:{target["slot"]}' if event_key and len(targets) > 1 else event_key)
                        key = key or f"native:{event.event_key.split(':')[-1]}:to:{target['slot']}"
                        snapshot = event.value_snapshot
                        queued = self.env['qing.sms.message']._enqueue(binding, number,
                            snapshot['parameters'], snapshot['body'], key, self.id)
                        if not queued.event_id:
                            queued.sudo()._write_internal({'event_id': event.id})
                    except (AccessError, UserError) as error:
                        event._write_internal({'state': 'blocked', 'reason_code': 'NATIVE_PREPARE_BLOCKED',
                                               'reason_message': str(error)[:250]})
                        self._qing_sms_blocked_notification(message, partner_id, number)
                        continue
                    sms = queued.sms_id.sudo()
                    if not sms.mail_message_id:
                        sms.write({'mail_message_id': message.id, 'partner_id': partner_id})
                    notification = self.env['mail.notification'].sudo().search([
                        ('sms_id_int', '=', sms.id), ('mail_message_id', '=', message.id)], limit=1)
                    if not notification:
                        self.env['mail.notification'].sudo().create({
                            'author_id': message.author_id.id, 'mail_message_id': message.id,
                            'res_partner_id': partner_id, 'sms_number': sms.number,
                            'notification_type': 'sms', 'sms_id_int': sms.id,
                            'sms_tracker_ids': [Command.create({'sms_uuid': sms.uuid})],
                            'is_read': True, 'notification_status': 'ready'})
                        sms._update_sms_state_and_trackers('process')
                if event.state == 'pending' and event.message_ids:
                    event._write_internal({'state': 'enqueued'})
        frozen_partner_ids = {row['partner_id'] for row in event.recipient_snapshot or [] if row.get('partner_id')} if event_id else set()
        native_data = [item for item in recipients_data if item.get('notif') != 'sms' or
                       (item.get('id') in native_ids and item.get('id') not in frozen_partner_ids)]
        if native_ids or native_numbers:
            return super()._notify_thread_by_sms(message, native_data, sms_content=sms_content,
                sms_numbers=native_numbers, sms_pid_to_number=sms_pid_to_number,
                put_in_queue=put_in_queue, **kwargs)
        return True

    def _qing_sms_blocked_notification(self, message, partner_id, number):
        existing = self.env['mail.notification'].sudo().search([
            ('mail_message_id', '=', message.id), ('sms_number', '=', number),
            ('notification_type', '=', 'sms')], limit=1)
        if existing:
            return
        sms = self.env['sms.sms'].sudo().create({'number': number, 'partner_id': partner_id,
            'mail_message_id': message.id, 'body': _('Domestic SMS template is not configured.'),
            'state': 'error', 'failure_type': 'sms_registration_needed'})
        self.env['mail.notification'].sudo().create({
            'author_id': message.author_id.id, 'mail_message_id': message.id,
            'res_partner_id': partner_id, 'sms_number': number, 'notification_type': 'sms',
            'sms_id_int': sms.id, 'is_read': True, 'notification_status': 'exception',
            'failure_type': 'sms_registration_needed'})
