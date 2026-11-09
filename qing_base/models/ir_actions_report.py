"""Online preview, PDF appendices and qpdf password protection for PDF reports."""
import base64
import io
import logging
import os
import subprocess
import tempfile

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools.pdf import PdfFileReader, merge_pdf

from ..tools import mommy_base_loaded

_logger = logging.getLogger(__name__)

PDF_PASSWORD_EDIT_PARAM = 'qing_base.pdf_password_edit'
PDF_PASSWORD_READ_PARAM = 'qing_base.pdf_password_read'
PRINT_PREVIEW_PARAM = 'qing_base.report.print_preview'


class IrActionsReport(models.Model):
    _inherit = 'ir.actions.report'

    def _compute_qing_legacy_mommy_base(self):
        legacy = mommy_base_loaded(self.env)
        for report in self:
            report.qing_legacy_mommy_base = legacy

    @api.depends('qing_print_preview', 'report_type')
    def _compute_qing_print_preview_effective(self):
        # On upgraded databases mommy_base keeps its own preview handler.
        legacy = mommy_base_loaded(self.env)
        global_preview = not legacy and self._qing_global_print_preview()
        for report in self:
            report.qing_print_preview_effective = (
                report.report_type == 'qweb-pdf' and not legacy
                and (global_preview or report.qing_print_preview)
            )

    @api.constrains('model', 'qing_pdf_appendix_fields')
    def _check_qing_pdf_appendix_fields(self):
        for report in self:
            names = self._qing_split_field_names(report.qing_pdf_appendix_fields)
            if not names:
                continue
            model_fields = self.env[report.model]._fields if report.model in self.env else {}
            invalid = [name for name in names if getattr(model_fields.get(name), 'type', None) != 'binary']
            if invalid:
                raise ValidationError(self.env._(
                    'PDF appendix fields must be binary fields of %(model)s: %(fields)s',
                    model=report.model, fields=', '.join(invalid),
                ))

    def _get_readable_fields(self):
        return super()._get_readable_fields() | {'qing_print_preview_effective'}

    @api.model
    def _qing_global_print_preview(self):
        return self.env['ir.config_parameter'].sudo().get_bool(PRINT_PREVIEW_PARAM, default=False)

    @api.model
    def _qing_split_field_names(self, value):
        return [name.strip() for name in (value or '').split(',') if name.strip()]

    def _qing_pdf_appendix_field_names(self):
        """Binary fields appended to this report, from mommy_base on legacy databases."""
        self.ensure_one()
        if self.qing_legacy_mommy_base:
            legacy_value = self['attachment_merge_fields'] if 'attachment_merge_fields' in self._fields else ''
            return self._qing_split_field_names(legacy_value)
        return self._qing_split_field_names(self.qing_pdf_appendix_fields)

    def _qing_print_signature(self):
        """Settings that change the delivered PDF, for callers that pin a rendered result."""
        self.ensure_one()
        report = self.sudo()
        if report.qing_legacy_mommy_base:
            names = ('attachment_merge_fields', 'pdf_qpdf_enabled', 'pdf_password_read', 'pdf_password_edit')
        else:
            names = ('qing_pdf_appendix_fields', 'qing_pdf_password_enabled',
                     'qing_pdf_password_read', 'qing_pdf_password_edit')
        return [report[name] if name in report._fields else False for name in names]

    def _qing_merge_pdf_appendices(self, pdf_content, res_ids):
        """Append the PDF files stored in the configured binary fields of each record.

        With ``report_pdf_strict_merge`` in the context, a missing field or an
        unreadable appendix stops delivery instead of returning an incomplete document.
        """
        self.ensure_one()
        names = [] if self.qing_legacy_mommy_base else self._qing_split_field_names(self.qing_pdf_appendix_fields)
        ids = [res_ids] if isinstance(res_ids, int) else list(res_ids or [])
        if not names or not ids:
            return pdf_content
        strict = bool(self.env.context.get('report_pdf_strict_merge'))
        records = self.env[self.model].browse(ids)
        missing = [name for name in names if getattr(records._fields.get(name), 'type', None) != 'binary']
        if missing:
            if strict:
                raise UserError(self.env._(
                    'A configured PDF appendix field is missing. The incomplete report was not delivered.'
                ))
            _logger.warning('Report %s skips missing PDF appendix fields: %s', self.display_name, ', '.join(missing))
            names = [name for name in names if name not in missing]
        documents = [pdf_content]
        for record in records:
            for name in names:
                value = record[name]
                if not value:
                    continue
                try:
                    content = value.content if hasattr(value, 'content') else base64.b64decode(value)
                    if not len(PdfFileReader(io.BytesIO(content), strict=False).pages):
                        raise ValueError('empty PDF')
                except Exception as error:  # noqa: BLE001 - every unreadable file is handled the same way
                    if strict:
                        raise UserError(self.env._(
                            'A PDF appendix could not be merged. The incomplete report was not delivered.'
                        )) from None
                    _logger.warning('Report %s skips appendix %s of %s,%s: %s',
                                    self.display_name, name, record._name, record.id, error)
                    continue
                documents.append(content)
        if len(documents) == 1:
            return pdf_content
        try:
            return merge_pdf(documents)
        except Exception as error:  # noqa: BLE001
            if strict:
                raise UserError(self.env._(
                    'The main PDF could not be merged. The incomplete report was not delivered.'
                )) from None
            _logger.warning('Report %s could not merge its PDF appendices: %s', self.display_name, error)
            return pdf_content

    def _qing_pdf_passwords(self):
        """Return (read, edit) passwords; one configured password covers both."""
        self.ensure_one()
        parameters = self.env['ir.config_parameter'].sudo()
        report = self.sudo()
        read_password = report.qing_pdf_password_read or parameters.get_str(PDF_PASSWORD_READ_PARAM)
        edit_password = report.qing_pdf_password_edit or parameters.get_str(PDF_PASSWORD_EDIT_PARAM)
        return read_password or edit_password, edit_password or read_password

    def _qing_protect_pdf(self, pdf_content):
        """Encrypt a rendered PDF with qpdf; never deliver it unprotected on failure."""
        self.ensure_one()
        if not self.qing_pdf_password_enabled or self.qing_legacy_mommy_base:
            return pdf_content
        read_password, edit_password = self._qing_pdf_passwords()
        if not read_password:
            raise UserError(self.env._(
                'PDF password protection is enabled for this report, but no password is configured.'
            ))
        paths = []
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as source:
                source.write(pdf_content)
                paths.append(source.name)
            with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as target:
                paths.append(target.name)
            # Pass passwords through a private argument file, not the process list.
            with tempfile.NamedTemporaryFile('w', delete=False, suffix='.args', encoding='utf-8') as arguments:
                os.chmod(arguments.name, 0o600)
                arguments.write('\n'.join(['--encrypt', read_password, edit_password, '256', '--']))
                paths.append(arguments.name)
            subprocess.run(
                ['qpdf', f'@{paths[2]}', paths[0], paths[1]],
                check=True, capture_output=True, timeout=60,
            )
            with open(paths[1], 'rb') as protected:
                return protected.read()
        except FileNotFoundError:
            _logger.error('qpdf is not installed for password-protected report %s', self.display_name)
            raise UserError(self.env._(
                'PDF password protection is unavailable because qpdf is not installed. '
                'Contact your administrator.'
            )) from None
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
            _logger.error('qpdf failed for report %s: %s', self.display_name, type(error).__name__)
            raise UserError(self.env._(
                'PDF password protection failed, so the report was not delivered. '
                'Contact your administrator.'
            )) from None
        finally:
            for path in paths:
                if os.path.exists(path):
                    os.unlink(path)

    def _render_qweb_pdf(self, report_ref, res_ids=None, data=None):
        pdf_content, content_type = super()._render_qweb_pdf(report_ref, res_ids=res_ids, data=data)
        report = self._get_report(report_ref)
        if not report or content_type != 'pdf':
            return pdf_content, content_type
        pdf_content = report._qing_merge_pdf_appendices(pdf_content, res_ids)
        return report._qing_protect_pdf(pdf_content), content_type

    qing_legacy_mommy_base = fields.Boolean(compute='_compute_qing_legacy_mommy_base')
    qing_pdf_appendix_fields = fields.Char(
        string='PDF Appendix Fields',
        help='Comma-separated binary fields of the printed record. Their PDF files are appended after the report.',
    )
    qing_pdf_password_edit = fields.Char(
        string='PDF Edit Password', groups='base.group_system', copy=False,
        help='Owner password that controls editing. Uses the default from Advanced Settings when empty.',
    )
    qing_pdf_password_enabled = fields.Boolean(
        string='PDF Password Protection',
        help='Encrypt the rendered PDF with qpdf. qpdf must be installed on the server.',
    )
    qing_pdf_password_read = fields.Char(
        string='PDF Read Password', groups='base.group_system', copy=False,
        help='Password required to open the PDF. Uses the default from Advanced Settings when empty.',
    )
    qing_print_preview = fields.Boolean(
        string='Preview Online',
        help='Open this PDF report in a new browser tab instead of downloading it.',
    )
    qing_print_preview_effective = fields.Boolean(compute='_compute_qing_print_preview_effective')
