"""Detect Solutions addons that upgraded databases may still have installed.

Qing addons do not depend on them. When one is loaded, the Qing feature that
reimplements it stays dormant so that the legacy addon remains authoritative and
behavior is never applied twice.
"""


def legacy_addon_loaded(env, module_name):
    """Return whether ``module_name`` is loaded in the current registry."""
    return module_name in env.registry._init_modules


def mommy_base_loaded(env):
    return legacy_addon_loaded(env, 'mommy_base')
