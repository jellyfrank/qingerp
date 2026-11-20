/** @odoo-module **/

function setupLoginBackgroundFallback() {
    const image = document.querySelector('.qing-login-custom-image');
    if (!image) {
        return;
    }
    // The default starry sky is rendered underneath; removing the image shows it.
    const fallback = () => image.remove();
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
