
/** @odoo-module **/

import { patch } from '@web/core/utils/patch';
import { PaymentForm } from '@payment/interactions/payment_form';

patch(PaymentForm.prototype, {
    /** @override */
    isMobilePage() {
        return window.matchMedia('(max-width: 767px)').matches ||
               document.body.classList.contains('o_mobile');
    },

    _prepareTransactionRouteParams() {
        const params = super._prepareTransactionRouteParams(...arguments);
        if (this.isMobilePage()) {
            params.mobile = true;
        }
        return params;
    }
});
