"""Replace Odoo's own promotion in templates of apps QingERP does not depend on.

Some customer-facing templates of optional apps advertise Odoo: the vendor
portal's "Connect with your software" dialog, the digest e-mails, the point of
sale receipt, a few e-mail templates. This module cannot inherit them in XML
data, because that would make those apps dependencies. Instead:

* each entry of ``BRAND_PATCHES`` is an inheriting view that exists only while
  its parent template does. The views are registered as
  ``qing_base.brand_patch_<key>`` with ``noupdate``, so module upgrades leave
  them alone and their Chinese terms live in this module's ``zh_CN.po``;
* the e-mail templates of ``MAIL_TEMPLATE_FIELDS`` get the word "Odoo", the
  odoo.com link and the Odoo logo replaced in place, in every language, so
  the rest of a template the company edited is kept.

Both are synchronised at the end of every module install or upgrade.
"""
import logging
import re

from odoo import api, models
from odoo.tools import mute_logger

_logger = logging.getLogger(__name__)

# key: (parent template, inheritance arch). The xpaths only rely on the
# block structure, which translations keep, never on translated text.
BRAND_PATCHES = {
    # Vendor portal: the dialog pitched Odoo three times and linked odoo.com.
    # Keep the one useful part, the link to the order as structured data.
    'purchase_portal_connect': ('purchase.portal_my_purchase_order', """<data>
    <xpath expr="//div[@id='portal_connect_software_modal']//div[hasclass('modal-body')]/div[1]" position="replace"/>
    <xpath expr="//div[@id='portal_connect_software_modal']//div[hasclass('modal-body')]/div[last()]" position="replace"/>
    <xpath expr="//div[@id='portal_connect_software_modal']//div[hasclass('modal-body')]//h5" position="replace">
        <h5>Connect your software</h5>
    </xpath>
    <xpath expr="//div[@id='portal_connect_software_modal']//div[hasclass('modal-body')]//p" position="replace">
        <p>Use this link to download the purchase order as structured data (UBL) and import it into your own system.</p>
    </xpath>
</data>"""),
    # Periodic digest e-mail: "Sent by Odoo" and "Powered by Odoo".
    'digest_mail_main': ('digest.digest_mail_main', """<data>
    <xpath expr="//div[hasclass('by_odoo')]/a" position="replace">
        <span class="odoo_link_text">QingERP</span>
    </xpath>
    <xpath expr="//p[@id='powered']/a" position="replace">
        <span class="odoo_link_text">QingERP</span>
    </xpath>
</data>"""),
    # Digest e-mail: the banner advertising Odoo's mobile app.
    'digest_section_mobile': ('digest.digest_section_mobile', """<data>
    <xpath expr="//td[@colspan='3']" position="replace"/>
</data>"""),
    # Point of sale receipt: the last line of the footer, "Powered by Odoo".
    'pos_receipt_footer': ('point_of_sale.pos_order_receipt_footer', """<data>
    <xpath expr="//div[@name='footer']/following-sibling::div[not(@t-if)][last()]" position="replace">
        <div class="w-100 text-center mt-3">Powered by <strong>QingERP</strong></div>
    </xpath>
</data>"""),
}

# Stock e-mail templates that name Odoo to the people a company writes to.
MAIL_TEMPLATE_FIELDS = {
    'auth_signup.set_password_email': ('subject', 'body_html'),
    'auth_totp_mail.mail_template_totp_invite': ('subject', 'body_html'),
    'calendar.calendar_template_meeting_invitation': ('body_html',),
    'calendar.calendar_template_meeting_changedate': ('body_html',),
    'calendar.calendar_template_meeting_reminder': ('body_html',),
    'calendar.calendar_template_meeting_update': ('body_html',),
    'account.mail_template_einvoice_notification': ('body_html',),
    'account.mail_template_invoice_subscriber': ('body_html',),
}

ODOO_LINK = re.compile(r'<a\b[^>]*\bhref="https?://(?:www\.)?odoo\.com/?"[^>]*>\s*Odoo\s*</a>')
ODOO_LOGO = re.compile(r'<img\b[^>]*\bsrc="/web/static/img/logo_inverse_white_206px\.png"[^>]*>')
# The product name on its own: not OdooBot, not odoo.com, not a /odoo/ path.
ODOO_WORD = re.compile(r'(?<![A-Za-z0-9_./@-])Odoo(?![A-Za-z0-9_]|\.[A-Za-z])')
MARKUP = re.compile(r'(<[^>]*>|\{\{.*?\}\})')


def rebrand_mail_text(text):
    """Return ``text`` (a template subject or body) naming QingERP, not Odoo.

    Tags and ``{{ }}`` expressions are left as they are; only the text
    between them is changed.
    """
    text = ODOO_LOGO.sub('', ODOO_LINK.sub('QingERP', text))
    parts = MARKUP.split(text)
    return ''.join(
        part if index % 2 else ODOO_WORD.sub('QingERP', part)
        for index, part in enumerate(parts)
    )


class QingBrandPatch(models.AbstractModel):
    _name = 'qing.brand.patch'
    _description = 'QingERP brand patches for optional apps'

    @api.model
    def _sync(self):
        """Create, update or drop the brand patches; return what changed."""
        if self.env.cr.readonly:
            return []
        changed = self._sync_views()
        if changed:
            langs = [code for code, _label in self.env['res.lang'].get_installed() if code != 'en_US']
            if langs:
                self.env['ir.module.module'].sudo()._load_module_terms(['qing_base'], langs)
        changed += self._sync_mail_templates()
        return changed

    @api.model
    def _sync_views(self):
        View = self.env['ir.ui.view'].sudo().with_context(active_test=False, lang=None)
        Data = self.env['ir.model.data'].sudo()
        langs = [code for code, _label in self.env['res.lang'].get_installed()]
        changed = []
        for key, (parent_xmlid, arch) in BRAND_PATCHES.items():
            name = 'brand_patch_%s' % key
            parent = self.env.ref(parent_xmlid, raise_if_not_found=False)
            data = Data.search([('module', '=', 'qing_base'), ('name', '=', name)], limit=1)
            view = View.browse(data.res_id).exists() if data else View
            if not parent:
                if view:
                    view.unlink()
                    changed.append(key)
                data.exists().unlink()
                continue
            if view and view.inherit_id == parent and (view.arch_db or '').strip() == arch.strip():
                continue
            try:
                # A template that changed upstream must not break module
                # loading, nor the page itself: when the patch does not apply
                # in every language, leave the template as it is and say so.
                with self.env.cr.savepoint(), mute_logger('odoo.addons.base.models.ir_ui_view'):
                    values = {'arch': arch, 'inherit_id': parent.id}
                    if view:
                        view.write(values)
                    else:
                        view = View.create({
                            'name': 'QingERP brand patch: %s' % key,
                            'type': 'qweb',
                            'mode': 'extension',
                            'priority': 99,
                            'key': 'qing_base.%s' % name,
                            **values,
                        })
                        data.unlink()
                        Data.create({'module': 'qing_base', 'name': name, 'model': 'ir.ui.view',
                                     'res_id': view.id, 'noupdate': True})
                    for lang in langs:
                        parent.with_context(lang=lang).get_combined_arch()
                changed.append(key)
            except Exception as error:  # noqa: BLE001
                _logger.warning("QingERP brand patch %s does not apply to %s: %s", key, parent_xmlid, error)
        return changed

    @api.model
    def _sync_mail_templates(self):
        changed = []
        # The source language first: a language without its own text reads
        # the source, and must not get a copy of it as a "translation".
        langs = ['en_US'] + [code for code, _label in self.env['res.lang'].get_installed() if code != 'en_US']
        for xmlid, field_names in MAIL_TEMPLATE_FIELDS.items():
            template = self.env.ref(xmlid, raise_if_not_found=False)
            if not template:
                continue
            for lang in langs:
                localized = template.sudo().with_context(lang=lang)
                for field_name in field_names:
                    value = str(localized[field_name] or '')
                    cleaned = rebrand_mail_text(value)
                    if cleaned != value:
                        localized[field_name] = cleaned
                        if xmlid not in changed:
                            changed.append(xmlid)
        return changed


class IrModelData(models.Model):
    _inherit = 'ir.model.data'

    @api.model
    def _process_end(self, modules):
        result = super()._process_end(modules)
        try:
            with self.env.cr.savepoint():
                self.env['qing.brand.patch']._sync()
        except Exception:  # noqa: BLE001
            _logger.exception("QingERP brand patches could not be synchronised")
        return result
