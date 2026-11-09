from odoo import api, fields, models
from odoo.exceptions import AccessError, ValidationError


class ResUsersSettings(models.Model):
    _inherit = "res.users.settings"

    @api.model
    def _qing_theme_preference_fields(self):
        # Optional appearance addons extend this mapping, never the base dependency list.
        return {"base": ("qing_ui_preferences", {
            "schema": (3, 4),
            "density": ("standard", "compact", "relaxed"),
            "sidebarState": ("expanded", "collapsed"),
            "sidebarGroupsDefault": ("expanded", "collapsed"),
            "sidebarStyle": ("tree", "islands", "rail"),
            "listActionsAlignment": ("left", "right"),
            "chatterPosition": ("auto", "right", "bottom"),
            "reducedMotion": bool,
            "themeMode": ("system", "light", "dark"),
            "accentColor": ("teal", "blue", "purple", "amber", "green"),
            "textSize": ("small", "medium", "large"),
            "autoOpenLauncher": bool,
            "swipeActions": bool,
        })}

    @api.model
    def _validate_qing_theme_preferences(self, values, options):
        if not isinstance(values, dict) or values.keys() - options.keys():
            raise ValidationError(self.env._("Invalid theme preferences."))
        for name, value in values.items():
            allowed = options[name]
            valid = type(value) is bool if allowed is bool else any(
                type(value) is type(option) and value == option for option in allowed
            )
            if not valid:
                raise ValidationError(self.env._("Invalid theme preferences."))

    @api.model
    def _get_qing_theme_preferences(self):
        # Session bootstrap is read-only and scoped to the authenticated user.
        settings = self.env.user.sudo().res_users_settings_ids
        return {name: settings[field] or None for name, (field, _options)
                in self._qing_theme_preference_fields().items()}

    @api.constrains("qing_ui_preferences")
    def _check_qing_ui_preferences(self):
        options = self._qing_theme_preference_fields()["base"][1]
        for settings in self:
            if settings.qing_ui_preferences is not False and settings.qing_ui_preferences is not None:
                self._validate_qing_theme_preferences(settings.qing_ui_preferences, options)

    qing_app_menu_order = fields.Json(string="Application menu order", default=list, copy=False)

    qing_ui_preferences = fields.Json(string="Qing theme preferences", copy=False)

    @api.constrains("qing_app_menu_order")
    def _check_qing_app_menu_order(self):
        for settings in self:
            order = settings.qing_app_menu_order
            if order is False or order is None:
                continue
            if (not isinstance(order, list) or len(order) > 1000
                    or any(type(menu_id) is not int or menu_id <= 0 for menu_id in order)
                    or len(order) != len(set(order))):
                raise ValidationError(self.env._("Invalid application menu order."))

    @api.model
    def set_qing_app_menu_order(self, menu_ids):
        # No caller-supplied user/record ID: the native helper targets this user only.
        if not self.env.user._is_internal():
            raise AccessError(self.env._("Only internal users can customize application menus."))
        if (not isinstance(menu_ids, list) or len(menu_ids) > 1000
                or any(type(menu_id) is not int or menu_id <= 0 for menu_id in menu_ids)):
            raise ValidationError(self.env._("Invalid application menu order."))
        allowed = set(self.env["ir.ui.menu"].load_menus(True)["root"]["children"])
        order = list(dict.fromkeys(menu_id for menu_id in menu_ids if menu_id in allowed))
        settings = self._find_or_create_for_user(self.env.user)
        settings.qing_app_menu_order = order
        return order

    @api.model
    def set_qing_theme_preferences(self, preferences):
        """Save validated appearance changes for the current internal user only."""
        if not self.env.user._is_internal():
            raise AccessError(self.env._("Only internal users can customize the theme."))
        fields_by_name = self._qing_theme_preference_fields()
        if not isinstance(preferences, dict) or not preferences or preferences.keys() - fields_by_name.keys():
            raise ValidationError(self.env._("Invalid theme preferences."))
        for name, values in preferences.items():
            self._validate_qing_theme_preferences(values, fields_by_name[name][1])
        settings = self._find_or_create_for_user(self.env.user)
        settings.write({field: {**(settings[field] or {}), **preferences[name]}
                        for name, (field, _options) in fields_by_name.items() if name in preferences})
        return self._get_qing_theme_preferences()
