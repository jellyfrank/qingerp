from odoo import http

from odoo.addons.website.controllers.main import Website


class QingWebsite(Website):

    @http.route()
    def favicon(self, **kw):
        """Browsers ask for /favicon.ico on pages that name no icon (a PDF, the proxy's
        error page). Stock Odoo answers with a permanent redirect cached for a year, so
        a browser keeps showing the old icon long after the favicon was changed. Make
        it a temporary redirect that is checked again within the hour."""
        response = super().favicon(**kw)
        response.status_code = 302
        response.headers['Cache-Control'] = 'public, max-age=3600'
        return response
