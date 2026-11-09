def uninstall_hook(env):
    """Restore the native form-only binding if our binding is still unchanged."""
    report = env.ref('l10n_cn.account_voucher_cn', raise_if_not_found=False)
    if report and report.binding_view_types == 'form,list':
        report.binding_view_types = 'form'
