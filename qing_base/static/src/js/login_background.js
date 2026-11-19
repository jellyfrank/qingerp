/** @odoo-module **/

function setupLoginBackgroundFallback() {
    const image = document.querySelector('.qing-login-custom-image');
    if (!image) {
        return;
    }
    const fallback = () => {
        image.src = '/qing_base/static/img/qingdao_low_angle.webp';
    };
    if (image.complete && !image.naturalWidth) {
        fallback();
    } else {
        image.addEventListener('error', fallback, { once: true });
    }
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', setupLoginBackgroundFallback, { once: true });
} else {
    setupLoginBackgroundFallback();
}
