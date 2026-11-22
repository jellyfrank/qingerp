import logging
from time import monotonic

from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import SQL
from odoo.addons.qing_base.tools.channel_queue import locked_job_environment
from odoo.addons.qing_channel.errors import ChannelReviewRequired, DeferredChannelError, retry_delay

from ..services.catalog import fetch_product, product_ids, variant_code_keys


_logger = logging.getLogger(__name__)
WORK_STATES = ('scanning', 'applying')
OPEN_STATES = ('scanning', 'preview', 'applying', 'review')
RUN_STATES = [
    ('scanning', 'Reading Catalog'), ('preview', 'Ready to Review'), ('applying', 'Importing'),
    ('done', 'Complete'), ('review', 'Needs Review'), ('cancelled', 'Cancelled'),
]


class ShopifyCatalogRun(models.Model):
    _name = 'qing.shopify.catalog.run'
    _description = 'Shopify Catalog Import'
    _check_company_auto = True
    _order = 'id desc'
    _rec_name = 'store_id'

    @api.model
    def _cron_shopify_catalog(self):
        domain = [
            ('store_id.active', '=', True), ('state', 'in', WORK_STATES),
            '|', ('retry_at', '=', False), ('retry_at', '<=', fields.Datetime.now()),
        ]
        for job in self.sudo().search(domain, limit=10, order='write_date, id'):
            key = f'qing-shopify-catalog-job:{job.store_id.id}'
            with locked_job_environment(self.env.registry, key) as env:
                if env is not None:
                    env[self._name].browse(job.id).with_company(job.company_id)._process_catalog_job()

    def _process_catalog_job(self):
        deadline = monotonic() + 10
        while self.state in WORK_STATES and monotonic() < deadline:
            try:
                with self.env.cr.savepoint():
                    self._step_catalog_job()
                    if self.issue or self.tries:
                        self.write({'tries': 0, 'issue': False, 'retry_at': False})
            except ChannelReviewRequired as exc:
                self.write({'resume_state': self.state, 'state': 'review', 'issue': exc.code})
                self.env.cr.commit()
                return
            except DeferredChannelError as exc:
                self._record_catalog_retry(exc)
                self.env.cr.commit()
                return
            self.env.cr.commit()

    def _record_catalog_retry(self, exc):
        if exc.code == 'shopify_page_cursor_invalid' and self.state == 'scanning' and not self.cursor_restarted:
            # The scan is ordered by product ID and staging rows are unique per store and product.
            self.write({'cursor': False, 'pending_ids': [], 'cursor_restarted': True})
            return
        tries = self.tries + 1
        self.write({'tries': tries, 'issue': exc.code, 'retry_at': fields.Datetime.now() + retry_delay(tries, exc.retry_after)})
        if tries >= 8:
            self.write({'resume_state': self.state, 'state': 'review'})

    def _step_catalog_job(self):
        self.ensure_one()
        if self.state == 'scanning':
            self._scan_catalog_step()
        if self.state == 'applying':
            self._apply_catalog_step()

    def _scan_catalog_step(self):
        store = self.store_id
        if not self.shop_currency:
            shop = store._catalog_read_permission()
            self.write({'shop_currency': shop['currencyCode'], 'shop_prices_include_tax': shop.get('taxesIncluded')})
        client = store._shopify_client()
        pending = self.pending_ids or []
        if not pending:
            ids, cursor = product_ids(client, self.cursor)
            if ids:
                self.write({'pending_ids': ids, 'cursor': cursor})
            else:
                self._finish_catalog_scan()
            return
        gid = pending[0]
        snapshot = fetch_product(client, gid)
        self.env['qing.shopify.catalog.item']._record_catalog_preview(self, gid, snapshot)
        self.pending_ids = pending[1:]
        if not self.pending_ids and not self.cursor:
            self._finish_catalog_scan()

    def _finish_catalog_scan(self):
        # Inspect all pages before approving new mappings. Existing explicit bindings retain their identity.
        self.env['qing.shopify.catalog.item'].flush_model(['catalog_code_keys', 'run_id'])
        duplicates = {key for key, in self.env.execute_query(SQL("""
            SELECT code
              FROM qing_shopify_catalog_item
                   CROSS JOIN LATERAL jsonb_array_elements_text(catalog_code_keys) AS codes(code)
             WHERE run_id = %s
             GROUP BY code
            HAVING COUNT(*) > 1
        """, self.id))}
        if duplicates:
            candidates = self.item_ids.filtered(lambda item: item.state in ('new', 'link', 'different'))
            conflicting = candidates.filtered(lambda item: any(
                (item.plan or {}).get(variant['id'], {}).get('action') in ('create', 'link')
                and duplicates.intersection(variant_code_keys(variant))
                for variant in item.remote_snapshot['variants']
            ))
            conflicting.write({'state': 'review', 'issue': 'shopify_catalog_codes_ambiguous', 'plan': {}})
        self.write({'state': 'preview', 'product_defaults': self.store_id._catalog_new_product_defaults()})

    def _apply_catalog_step(self):
        domain = [('run_id', '=', self.id), ('apply_pending', '=', True)]
        job = self.env['qing.shopify.catalog.item'].search(domain, order='id', limit=1)
        if not job:
            if not self.store_id.shopify_catalog_imported_at:
                self.store_id.shopify_catalog_imported_at = self.create_date
            self.state = 'done'
            return
        try:
            with self.env.cr.savepoint():
                if not self._catalog_preview_is_current(job):
                    job.write({'state': 'review', 'issue': 'shopify_catalog_preview_changed'})
                else:
                    self.store_id._apply_shopify_catalog_product(job)
        except ChannelReviewRequired as exc:
            job.write({'state': 'review', 'issue': exc.code})
        job.apply_pending = False

    def _catalog_preview_is_current(self, item):
        snapshot = fetch_product(self.store_id._shopify_client(), item.external_id)
        if not snapshot:
            return item.state == 'archived'
        return item.remote_snapshot == snapshot

    @api.depends('item_ids.state')
    def _compute_totals(self):
        grouped = self.env['qing.shopify.catalog.item']._read_group(
            [('run_id', 'in', self.ids)], ['run_id', 'state'], ['__count'],
        )
        totals = {(run.id, state): amount for run, state, amount in grouped}
        for run in self:
            run.new_count = totals.get((run.id, 'new'), 0)
            run.link_count = totals.get((run.id, 'link'), 0)
            run.linked_count = totals.get((run.id, 'linked'), 0)
            run.different_count = totals.get((run.id, 'different'), 0)
            run.review_count = totals.get((run.id, 'review'), 0)
            run.archived_count = totals.get((run.id, 'archived'), 0)
            run.skipped_count = totals.get((run.id, 'skipped'), 0)

    @api.depends('product_defaults')
    def _compute_product_defaults_preview(self):
        Catalog = self.env['product.template']
        policies = dict(Catalog._fields['invoice_policy']._description_selection(self.env))
        for run in self:
            values = run.product_defaults
            if not values:
                run.product_defaults_preview = False
                continue
            Tax = self.env['account.tax'].sudo()
            lines = [
                (_('Category'), self.env['product.category'].sudo().browse(values['categ_id']).exists().display_name or _('None')),
                (_('Unit'), self.env['uom.uom'].sudo().browse(values['uom_id']).exists().display_name or _('None')),
                (_('Sales Taxes'), ', '.join(Tax.browse(values['sale_tax_ids']).exists().mapped('display_name')) or _('None')),
                (_('Purchase Taxes'), ', '.join(Tax.browse(values['purchase_tax_ids']).exists().mapped('display_name')) or _('None')),
                (_('Can Be Purchased'), _('Yes') if values['purchase_ok'] else _('No')),
                (_('Invoicing Policy'), policies[values['invoice_policy']]),
            ]
            run.product_defaults_preview = Markup('<dl class="mb-0">{}</dl>').format(Markup('').join(
                Markup('<div><dt class="d-inline-block me-2">{}</dt><dd class="d-inline">{}</dd></div>').format(label, value)
                for label, value in lines
            ))

    def _open_catalog_run(self):
        return {
            'type': 'ir.actions.act_window', 'name': _('Shopify Catalog Preview'), 'res_model': self._name,
            'views': [(False, 'form')], 'res_id': self.id, 'target': 'current',
        }

    def _check_catalog_run_access(self):
        self.ensure_one()
        self.check_access('read')
        self.store_id._check_channel_manager()
        run = self.sudo()
        run.lock_for_update(allow_referencing=True)
        return run

    archived_count = fields.Integer(compute='_compute_totals')
    company_id = fields.Many2one(related='store_id.company_id', store=True, index=True, precompute=True)
    cursor = fields.Char(copy=False, readonly=True)
    cursor_restarted = fields.Boolean(copy=False)
    different_count = fields.Integer(compute='_compute_totals')
    issue = fields.Char(copy=False, readonly=True)
    item_ids = fields.One2many('qing.shopify.catalog.item', 'run_id', readonly=True)
    link_count = fields.Integer(compute='_compute_totals')
    linked_count = fields.Integer(compute='_compute_totals')
    new_count = fields.Integer(compute='_compute_totals')
    pending_ids = fields.Json(copy=False, readonly=True)
    product_defaults = fields.Json(copy=False, readonly=True)
    product_defaults_preview = fields.Html(compute='_compute_product_defaults_preview')
    resume_state = fields.Char(copy=False, readonly=True)
    retry_at = fields.Datetime(copy=False, readonly=True)
    review_count = fields.Integer(compute='_compute_totals')
    shop_currency = fields.Char(readonly=True, copy=False)
    shop_prices_include_tax = fields.Boolean(readonly=True, copy=False)
    skipped_count = fields.Integer(compute='_compute_totals')
    state = fields.Selection(RUN_STATES, default='scanning', required=True, readonly=True, index=True)
    store_id = fields.Many2one('qing.channel.store', required=True, ondelete='restrict', index=True, readonly=True)
    tries = fields.Integer(copy=False, readonly=True)

    _open_store_unique = models.UniqueIndex("(store_id) WHERE state IN ('scanning','preview','applying','review')")

    def action_catalog_confirm(self):
        run = self._check_catalog_run_access()
        if run.state != 'preview':
            raise UserError(_('Review the completed Shopify catalog preview first.'))
        current = run.store_id._catalog_new_product_defaults()
        if run.product_defaults != current:
            run.product_defaults = current
            return {
                'type': 'ir.actions.client', 'tag': 'display_notification',
                'params': {
                    'message': _('New product defaults changed. Review the updated preview before confirming.'),
                    'type': 'warning', 'next': run._open_catalog_run(),
                },
            }
        items = self.env['qing.shopify.catalog.item'].sudo().search([
            ('run_id', '=', run.id), ('state', 'in', ('new', 'link', 'different', 'linked', 'archived')),
        ])
        items.write({'apply_pending': True})
        run.state = 'applying'
        self.env.ref('qing_shopify.cron_shopify_catalog').sudo()._trigger()
        return True

    def action_catalog_retry(self):
        run = self._check_catalog_run_access()
        if run.state != 'review' or run.resume_state not in WORK_STATES:
            raise UserError(_('This catalog job does not need a retry.'))
        run.write({'state': run.resume_state, 'tries': 0, 'retry_at': False, 'issue': False})
        self.env.ref('qing_shopify.cron_shopify_catalog').sudo()._trigger()
        return True

    def action_catalog_cancel(self):
        run = self._check_catalog_run_access()
        if run.state in ('preview', 'review'):
            run.state = 'cancelled'
        return True
