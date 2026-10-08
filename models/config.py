import secrets

import requests

from odoo import api, fields, models, _
from odoo.exceptions import UserError

DEFAULT_BASE = 'https://api.bizwhatz.com'


class BizwhatzConfig(models.Model):
    _name = 'otm.bizwhatz.config'
    _description = 'Bizwhatz Connection'
    _rec_name = 'company_id'

    company_id = fields.Many2one('res.company', required=True, default=lambda s: s.env.company)
    active = fields.Boolean(default=True)
    # --- connection (Odoo -> Bizwhatz)
    base_url = fields.Char(string='Bizwhatz API URL', default=DEFAULT_BASE, required=True)
    api_key = fields.Char(string='Organization API Key', groups='custom_leads_19.group_lead_manager,custom_leads_19.group_super_admin')
    country_code = fields.Char(string='Default Country Code', default='91',
                               help='Added when a lead number has 10 digits, because Bizwhatz stores 91XXXXXXXXXX.')
    # --- webhook (Bizwhatz -> Odoo)
    webhook_token = fields.Char(string='Webhook Token', copy=False, readonly=True,
                                default=lambda s: secrets.token_urlsafe(24))
    webhook_secret = fields.Char(string='External Webhook Secret',
                                 help='Same value as in Bizwhatz SmartLead Settings. When set, every webhook must carry a valid X-Hub-Signature-256.')
    webhook_url = fields.Char(string='Webhook URL', compute='_compute_webhook_url')
    # --- inbound options
    auto_create = fields.Boolean(string='Create leads from Bizwhatz', default=True)
    source_id = fields.Many2one('leads.sources', string='Lead Source', help='Source given to leads created from Bizwhatz. Created automatically if empty.')
    default_quality = fields.Selection(selection=lambda s: s.env['leads.logic']._fields['lead_quality'].selection,
                                       string='Quality for new leads', default='new')
    use_executive_owner = fields.Boolean(string='Use Bizwhatz executive as Lead Owner',
                                         help='Match assignedExecutive.phoneNumber to an employee mobile / work phone. '
                                              'If no match, normal Odoo auto-assignment applies.')
    # --- outbound options
    push_status = fields.Boolean(string='Send status changes to Bizwhatz', default=True)
    push_new_leads = fields.Boolean(string='Send new Odoo leads to Bizwhatz', default=False)
    push_source_ids = fields.Many2many('leads.sources', string='Only these sources',
                                       help='Leave empty to send every new lead (when enabled).')

    _company_uniq = models.Constraint('unique(company_id)', 'One Bizwhatz connection per company.')

    @api.depends('webhook_token')
    def _compute_webhook_url(self):
        base = self.env['ir.config_parameter'].sudo().get_param('web.base.url', '')
        for rec in self:
            rec.webhook_url = '%s/bizwhatz/webhook?token=%s' % (base, rec.webhook_token or '')

    # ------------------------------------------------------------------ helpers
    @api.model
    def _get(self, company=None):
        company = company or self.env.company
        return self.sudo().search([('company_id', '=', company.id)], limit=1)

    def _digits_phone(self, number):
        d = ''.join(c for c in (number or '') if c.isdigit())
        if len(d) == 10 and self.country_code:
            d = self.country_code + d
        return d

    def _get_source(self):
        self.ensure_one()
        if self.source_id:
            return self.source_id
        Source = self.env['leads.sources'].sudo()
        src = Source.search([('name', '=', 'Bizwhatz')], limit=1) or Source.create({'name': 'Bizwhatz'})
        self.sudo().source_id = src
        return src

    def _request(self, method, path, payload=None, params=None, timeout=10):
        self.ensure_one()
        key = self.sudo().api_key
        if not key:
            raise UserError(_('Set the Bizwhatz Organization API Key first.'))
        url = (self.base_url or DEFAULT_BASE).rstrip('/') + path
        resp = requests.request(method, url, json=payload, params=params, timeout=timeout, headers={
            'x-api-key': key, 'Content-Type': 'application/json', 'Accept': 'application/json'})
        return resp

    # ------------------------------------------------------------------ buttons
    def action_test_connection(self):
        self.ensure_one()
        try:
            r = self._request('GET', '/api/leads/external/statuses', timeout=10)
        except requests.RequestException as e:
            raise UserError(_('Cannot reach Bizwhatz:\n%s') % e)
        if r.status_code == 401:
            raise UserError(_('Bizwhatz rejected the API key (401).'))
        if r.status_code != 200:
            raise UserError(_('Bizwhatz answered HTTP %s:\n%s') % (r.status_code, r.text[:500]))
        return {'type': 'ir.actions.client', 'tag': 'display_notification', 'params': {
            'title': _('Connected'), 'message': _('Bizwhatz API key is valid.\n%s') % r.text[:400],
            'type': 'success', 'sticky': False}}

    def action_load_statuses(self):
        """Fetch Bizwhatz pipeline statuses and add any missing ones to the mapping table."""
        self.ensure_one()
        try:
            r = self._request('GET', '/api/leads/external/statuses')
            data = r.json()
        except (requests.RequestException, ValueError) as e:
            raise UserError(_('Could not load statuses: %s') % e)
        items = data
        if isinstance(data, dict):
            items = data.get('data') or data.get('statuses') or data.get('items') or []
        Map = self.env['otm.bizwhatz.status.map']
        added = 0
        for it in items if isinstance(items, list) else []:
            name = it.get('name') if isinstance(it, dict) else str(it)
            if name and not Map.search([('bizwhatz_status', '=ilike', name)], limit=1):
                Map.create({'bizwhatz_status': name, 'lead_quality': False})
                added += 1
        return {'type': 'ir.actions.client', 'tag': 'display_notification', 'params': {
            'title': _('Statuses loaded'), 'message': _('%s new status(es) added. Choose a Lead Quality for each one.') % added,
            'type': 'success'}}

    def action_pull_leads(self):
        """Back-fill: read existing Bizwhatz leads (paged) and upsert them in Odoo."""
        self.ensure_one()
        from .leads import upsert_from_bizwhatz
        total = 0
        for page in range(1, 21):
            try:
                r = self._request('GET', '/api/leads/external', params={'page': page, 'limit': 100}, timeout=30)
                data = r.json()
            except (requests.RequestException, ValueError) as e:
                raise UserError(_('Pull failed on page %s: %s') % (page, e))
            if r.status_code != 200:
                raise UserError(_('Bizwhatz answered HTTP %s:\n%s') % (r.status_code, r.text[:500]))
            items = data
            if isinstance(data, dict):
                inner = data.get('data', data)
                items = inner if isinstance(inner, list) else (
                    inner.get('leads') or inner.get('items') or inner.get('results') or [] if isinstance(inner, dict) else [])
                if not items:
                    items = data.get('leads') or data.get('items') or data.get('results') or []
            if not items:
                break
            for it in items:
                upsert_from_bizwhatz(self.env, self, 'lead.pull', it)
                total += 1
            if len(items) < 100:
                break
        return {'type': 'ir.actions.client', 'tag': 'display_notification', 'params': {
            'title': _('Pull finished'), 'message': _('%s lead(s) processed. See Bizwhatz Log.') % total, 'type': 'success'}}

    def action_new_token(self):
        for rec in self:
            rec.webhook_token = secrets.token_urlsafe(24)
