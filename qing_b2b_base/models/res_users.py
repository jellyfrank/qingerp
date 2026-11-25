from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, ValidationError
from odoo.tools import SQL

from ..constants import B2B_SCOPE


class ResUsers(models.Model):
    _inherit = "res.users"

    def _b2b_check_buyer(self, website):
        self.ensure_one()
        buyer = self.sudo()
        if not (buyer.active and buyer.b2b_website_id == website and buyer._is_portal()
                and website.b2b_enabled and buyer.b2b_company_id == website.company_id
                and (not buyer.partner_id.company_id or buyer.partner_id.company_id == website.company_id)):
            raise AccessError(_("This account is not enabled for ordering on this website."))
        return buyer.partner_id

    def _b2b_issue_device(self, website, remember, name):
        self.ensure_one()
        # Serialize issuance with native password changes and ordering revocation.
        self.env.cr.execute(SQL("SELECT id FROM res_users WHERE id = %s FOR SHARE", self.id))
        self._b2b_check_buyer(website)
        max_days = max(self.sudo().all_group_ids.mapped("api_key_duration"), default=1) or 1
        duration = min(website.b2b_device_days if remember else 1, max_days)
        expires_at = fields.Datetime.now() + timedelta(days=duration)
        keys = self.env["res.users.apikeys"].with_user(self)
        token = keys._generate(B2B_SCOPE, name, expires_at)
        return {"token": token, "expires_at": fields.Datetime.to_string(expires_at)}

    def _b2b_profile(self, website):
        """Describe the signed-in buyer for the account page of the ordering app."""
        self.ensure_one()
        partner = self._b2b_check_buyer(website)
        company = partner.commercial_partner_id
        return {
            "name": partner.name or "",
            "company": company.name or "" if company != partner else partner.company_name or "",
            "login": self.sudo().login,
            "email": partner.email or "",
            "phone": partner.phone or "",
        }

    def _b2b_revoke_devices(self):
        self.env["res.users.apikeys"].sudo().search([
            ("user_id", "in", self.ids), ("scope", "=", B2B_SCOPE),
        ])._remove()

    def _set_encrypted_password(self, uid, pw):
        super()._set_encrypted_password(uid, pw)
        self.browse(uid)._b2b_revoke_devices()

    @api.constrains("b2b_website_id", "b2b_company_id", "group_ids")
    def _check_b2b_website(self):
        for buyer in self.filtered("b2b_website_id"):
            if not buyer._is_portal():
                raise ValidationError(_("B2B ordering requires a portal account."))
            if buyer.b2b_website_id.company_id != buyer.b2b_company_id:
                raise ValidationError(_("The ordering authorization must specify the website company."))
            if buyer.partner_id.company_id and buyer.partner_id.company_id != buyer.b2b_company_id:
                raise ValidationError(_("The buyer contact belongs to a different company."))
            if buyer.website_id and buyer.website_id != buyer.b2b_website_id:
                raise ValidationError(_("The account and ordering website must match."))
            buyer.b2b_website_id._b2b_check_ready()

    b2b_company_id = fields.Many2one("res.company", string="B2B Selling Company",
                                    groups="base.group_system", copy=False, readonly=True)
    b2b_device_ids = fields.One2many("res.users.apikeys", "user_id", string="B2B Devices",
                                    domain=[("scope", "=", B2B_SCOPE)], groups="base.group_system")
    b2b_website_id = fields.Many2one("website", string="B2B Ordering Website",
                                    groups="base.group_system", copy=False, ondelete="restrict")

    @api.model_create_multi
    def create(self, vals_list):
        websites = self.env["website"].sudo()
        vals_list = [
            dict(vals, b2b_company_id=websites.browse(vals["b2b_website_id"]).company_id.id)
            if vals.get("b2b_website_id") else vals
            for vals in vals_list
        ]
        return super().create(vals_list)

    def write(self, values):
        if "b2b_website_id" in values:
            website = self.env["website"].sudo().browse(values["b2b_website_id"] or [])
            values = dict(values, b2b_company_id=website.company_id.id)
        monitored = {"active", "b2b_website_id", "b2b_company_id", "group_ids"}.intersection(values)
        changes = {user.id: (user.active, user.b2b_website_id, user.b2b_company_id, user._is_portal())
                   for user in self.sudo()} if monitored else {}
        result = super().write(values)
        if monitored:
            affected = self.sudo().filtered(lambda user: changes[user.id] != (
                user.active, user.b2b_website_id, user.b2b_company_id, user._is_portal(),
            ))
            affected._b2b_revoke_devices()
        return result
