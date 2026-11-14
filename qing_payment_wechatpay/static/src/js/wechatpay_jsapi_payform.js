(function () {
    function message(key) {
        var node = document.querySelector('#wechatpay_jsapi_messages [data-key="' + key + '"]');
        return node ? node.textContent.trim() : key;
    }

    function setStatus(text, isError) {
        var statusNode = document.getElementById("wechatpay_jsapi_status");
        if (!statusNode) {
            return;
        }
        statusNode.textContent = text;
        statusNode.classList.toggle("text-danger", !!isError);
        statusNode.classList.toggle("text-muted", !isError);
    }

    function parseParams() {
        var paramsNode = document.getElementById("wechatpay_jsapi_params");
        if (!paramsNode) {
            setStatus(message("missing"), true);
            return null;
        }
        try {
            return JSON.parse(paramsNode.dataset.params || paramsNode.textContent || "{}");
        } catch (error) {
            setStatus(message("invalid"), true);
            return null;
        }
    }

    function invokePay() {
        var params = parseParams();
        if (!params) {
            return;
        }
        if (typeof WeixinJSBridge === "undefined") {
            setStatus(message("no_bridge"), true);
            return;
        }

        setStatus(message("opening"));
        WeixinJSBridge.invoke("getBrandWCPayRequest", params, function (res) {
            var result = (res && (res.err_msg || res.errMsg) || "").toLowerCase();
            if (result === "get_brand_wcpay_request:ok") {
                setStatus(message("paid"));
                window.location.href = "/payment/status";
                return;
            }
            if (result === "get_brand_wcpay_request:cancel") {
                setStatus(message("cancelled"), true);
                return;
            }
            setStatus(message("failed") + " (" + result + ")", true);
        });
    }

    function bindRetry() {
        var retryBtn = document.getElementById("wechatpay_jsapi_retry");
        if (!retryBtn) {
            return;
        }
        retryBtn.addEventListener("click", function () {
            invokePay();
        });
    }

    function boot() {
        bindRetry();
        if (typeof WeixinJSBridge === "undefined") {
            document.addEventListener("WeixinJSBridgeReady", invokePay, false);
            setStatus(message("waiting"));
        } else {
            invokePay();
        }
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", boot);
    } else {
        boot();
    }
})();
