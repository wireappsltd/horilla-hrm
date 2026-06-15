/**
 * sessionExpiry.js
 *
 * When the inactivity timeout expires, the server responds to any background
 * request (jQuery AJAX, fetch, XHR) with an empty `401` carrying the
 * `X-Session-Expired` / `X-Login-Redirect` headers instead of the login page
 * HTML. Without this interceptor those responses could leave the user staring
 * at a stale module, or the login form could be injected into the page.
 *
 * This script performs a single, full-page redirect to the standalone login
 * screen the moment any such response is observed. HTMX handles the
 * `HX-Redirect` header natively, so it is intentionally not duplicated here.
 */
(function () {
    "use strict";

    var SESSION_EXPIRED_HEADER = "X-Session-Expired";
    var LOGIN_REDIRECT_HEADER = "X-Login-Redirect";
    var DEFAULT_LOGIN_URL = "/login";
    var redirecting = false;

    function redirectToLogin(location) {
        if (redirecting) {
            return;
        }
        redirecting = true;
        try {
            localStorage.clear();
            sessionStorage.clear();
        } catch (e) {
            /* ignore storage access errors */
        }
        window.location.href = location || DEFAULT_LOGIN_URL;
    }

    function isSessionExpired(getHeader) {
        try {
            return getHeader(SESSION_EXPIRED_HEADER) === "1";
        } catch (e) {
            return false;
        }
    }

    // ----- jQuery AJAX (covers $.ajax, $.get, $.post, .load, etc.) -----
    if (window.jQuery) {
        window.jQuery(document).ajaxComplete(function (event, jqXHR) {
            if (isSessionExpired(function (name) {
                return jqXHR.getResponseHeader(name);
            })) {
                redirectToLogin(jqXHR.getResponseHeader(LOGIN_REDIRECT_HEADER));
            }
        });
    }

    // ----- Native fetch() -----
    if (window.fetch) {
        var originalFetch = window.fetch.bind(window);
        window.fetch = function () {
            return originalFetch.apply(this, arguments).then(function (response) {
                if (response && response.headers &&
                    isSessionExpired(function (name) {
                        return response.headers.get(name);
                    })) {
                    redirectToLogin(response.headers.get(LOGIN_REDIRECT_HEADER));
                }
                return response;
            });
        };
    }

    // ----- Raw XMLHttpRequest (anything not going through jQuery) -----
    if (window.XMLHttpRequest) {
        var originalSend = XMLHttpRequest.prototype.send;
        XMLHttpRequest.prototype.send = function () {
            this.addEventListener("load", function () {
                if (isSessionExpired(function (name) {
                    return this.getResponseHeader(name);
                }.bind(this))) {
                    redirectToLogin(this.getResponseHeader(LOGIN_REDIRECT_HEADER));
                }
            });
            return originalSend.apply(this, arguments);
        };
    }
})();

