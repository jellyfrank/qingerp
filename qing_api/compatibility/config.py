def get_config_parameter(env, key, default=None):
    """Read a string configuration value with the Odoo 20 typed API."""
    return env["ir.config_parameter"].sudo().get_str(key, default=default)


def set_config_parameter(env, key, value):
    """Write a string configuration value with the Odoo 20 typed API."""
    return env["ir.config_parameter"].sudo().set_str(key, value)
