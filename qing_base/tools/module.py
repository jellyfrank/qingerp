"""Module catalog helpers shared by Qing addons."""


def addon_status(manifest, state):
    """Keep missing source distinct from an outdated module catalog."""
    if not manifest:
        return 'missing'
    if state in ('installed', 'to upgrade', 'to remove'):
        return 'installed'
    if not manifest.get('installable', True) or state == 'uninstallable':
        return 'unavailable'
    if not state:
        return 'unindexed'
    return 'available'
