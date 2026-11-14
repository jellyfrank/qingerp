(function () {
    const POLL_INTERVAL = 5000;

    async function queryPayment() {
        const orderElement = document.querySelector(".wechatpay_order");
        const order = orderElement?.dataset?.order || orderElement?.textContent?.trim();
        const accessToken = orderElement?.dataset?.accessToken;
        if (!order || !accessToken) {
            return;
        }

        const url = new URL("/shop/wechatpay/result", window.location.origin);
        url.searchParams.set("access_token", accessToken);
        url.searchParams.set("order", order);
        try {
            const response = await fetch(url, {
                headers: {Accept: "application/json"},
                method: "GET",
            });
            if (!response.ok) {
                return;
            }
            const result = await response.json();
            if (result.result === 0) {
                window.location.assign("/payment/status");
            }
        } catch {
            // A later poll retries transient network failures.
        }
    }

    function startPolling() {
        queryPayment();
        window.setInterval(queryPayment, POLL_INTERVAL);
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", startPolling, {once: true});
    } else {
        startPolling();
    }
})();
