import logging
import re

from odoo import api, fields, models, _
from odoo.tools import html_escape

_logger = logging.getLogger(__name__)


def _g(d, *names):
    for n in names:
        v = d.get(n)
        if v not in (None, '', 'null'):
            return v
    return ''


def upsert_from_bizwhatz(env, cfg, event, data):
    """Create or update an Odoo lead from a Bizwhatz lead object. Returns (lead, note)."""
    env = env(su=True, context=dict(env.context, otm_bizwhatz_skip=True, mail_create_nosubscribe=True,
                                    allowed_company_ids=[cfg.company_id.id]))
    Log, Lead, Map = env['otm.bizwhatz.log'], env['leads.logic'], env['otm.bizwhatz.status.map']
    bid = str(_g(data, 'id', 'leadId') or '')
    raw_phone = str(_g(data, 'phoneNumber', 'phone') or '')
    digits = re.sub(r'\D', '', raw_phone)
    last10 = digits[-10:]

    lead = Lead.browse()
    if bid:
        lead = Lead.search([('bizwhatz_id', '=', bid)], limit=1)
    if not lead and last10:
        lead = Lead.search([('phone_number', 'like', '%' + last10)], limit=1)

    if event == 'lead.deleted':
        if lead:
            lead.message_post(body=_('🗑️ Bizwhatz: this lead was deleted in Bizwhatz. The Odoo lead was kept.'))
        Log._log_in(event, data, 'done' if lead else 'ignored', lead)
        return lead, 'deleted noted'

    if not lead and not last10:
        Log._log_in(event, data, 'ignored', error='No phone number in payload (Odoo needs one).')
        return Lead.browse(), 'no phone'

    status = _g(data, 'status')
    quality = Map._quality_for(status)
    exe = data.get('assignedExecutive') or {}
    product = data.get('product') or data.get('products') or ''
    if isinstance(product, list):
        product = ', '.join(str(p) for p in product)
    details = [(_('Company'), _g(data, 'companyName')), (_('Designation'), _g(data, 'designation')),
               (_('Branch'), _g(data, 'branch')), (_('Product'), product), (_('Stage'), _g(data, 'stage')),
               (_('Bizwhatz status'), status), (_('Executive'), _g(exe, 'name') if isinstance(exe, dict) else ''),
               (_('Remarks'), _g(data, 'remarks'))]
    body = '<br/>'.join('<b>%s:</b> %s' % (html_escape(k), html_escape(str(v))) for k, v in details if v)

    owner = Lead.env['hr.employee'].browse()
    if cfg.use_executive_owner and isinstance(exe, dict) and exe.get('phoneNumber'):
        e10 = re.sub(r'\D', '', str(exe['phoneNumber']))[-10:]
        if e10:
            owner = env['hr.employee'].search(['|', ('mobile_phone', 'like', '%' + e10), ('work_phone', 'like', '%' + e10)], limit=1)

    if lead:
        vals = {}
        if bid and lead.bizwhatz_id != bid:
            vals['bizwhatz_id'] = bid
        if quality and quality != lead.lead_quality:
            vals['lead_quality'] = quality
        if _g(data, 'email') and not lead.email_address:
            vals['email_address'] = _g(data, 'email')
        if product and not lead.course_interested:
            vals['course_interested'] = product
        if owner and owner != lead.lead_owner and event in ('lead.assigned', 'lead.created'):
            vals['lead_owner'] = owner.id
        vals['bizwhatz_status'] = status or lead.bizwhatz_status
        if vals:
            lead.write(vals)
        if event != 'lead.created' or body:
            lead.message_post(body=Markup_event(event, body))
        Log._log_in(event, data, 'done', lead)
        return lead, 'updated'

    if not cfg.auto_create:
        Log._log_in(event, data, 'ignored', error='Auto-create is off.')
        return Lead.browse(), 'auto-create off'

    vals = {
        'name': _g(data, 'customerName', 'name') or 'Bizwhatz Lead',
        'phone_number': digits[-10:] if len(digits) > 10 else digits,
        'email_address': _g(data, 'email') or False,
        'leads_source': cfg._get_source().id,
        'lead_quality': quality or cfg.default_quality or 'new',
        'course_interested': product or False,
        'remarks': (_g(data, 'remarks') or '')[:250] or False,
        'bizwhatz_id': bid or False,
        'bizwhatz_status': status or False,
        'company_id': cfg.company_id.id,
    }
    if owner:
        vals['lead_owner'] = owner.id
    lead = Lead.create(vals)
    lead.message_post(body=Markup_event(event, body))
    Log._log_in(event, data, 'done', lead)
    return lead, 'created'


def Markup_event(event, body):
    from markupsafe import Markup
    return Markup('<b>🟢 Bizwhatz (%s)</b><br/>%s') % (event, Markup(body))


class LeadsLogic(models.Model):
    _inherit = 'leads.logic'

    bizwhatz_id = fields.Char(string='Bizwhatz Lead ID', index=True, copy=False, readonly=True)
    bizwhatz_status = fields.Char(string='Bizwhatz Status', copy=False, readonly=True)

    # ---- Odoo -> Bizwhatz -------------------------------------------------
    def _bizwhatz_push_status(self, cfg):
        Map, Log = self.env['otm.bizwhatz.status.map'], self.env['otm.bizwhatz.log']
        for lead in self:
            status = Map._status_for(lead.lead_quality)
            if not status or status == lead.bizwhatz_status:
                continue
            payload = {'phoneNumber': cfg._digits_phone(lead.phone_number), 'status': status,
                       'remarks': (lead.remarks or 'Status changed in Odoo')[:250]}
            Log._queue_out(cfg, lead, 'status.update', 'POST', '/api/leads/external/update-status', payload)

    def write(self, vals):
        old = {}
        track = ('lead_quality' in vals and not self.env.context.get('otm_bizwhatz_skip')
                 and not self.env.context.get('otm_odoo17_import'))
        if track:
            old = {l.id: l.lead_quality for l in self}
        res = super().write(vals)
        if track:
            cfg = self.env['otm.bizwhatz.config']._get()
            if cfg and cfg.active and cfg.push_status and cfg.api_key:
                changed = self.filtered(lambda l: l.bizwhatz_id and old.get(l.id) != l.lead_quality)
                try:
                    changed._bizwhatz_push_status(cfg)
                except Exception:  # never block saving a lead
                    _logger.exception('Bizwhatz: could not queue status update')
        return res

    @api.model_create_multi
    def create(self, vals_list):
        leads = super().create(vals_list)
        if self.env.context.get('otm_bizwhatz_skip'):
            return leads
        cfg = self.env['otm.bizwhatz.config']._get()
        if cfg and cfg.active and cfg.push_new_leads and cfg.api_key:
            for lead in leads.filtered(lambda l: not l.bizwhatz_id):
                if cfg.push_source_ids and lead.leads_source not in cfg.push_source_ids:
                    continue
                try:
                    lead._bizwhatz_push_new(cfg)
                except Exception:
                    _logger.exception('Bizwhatz: could not queue new lead')
        return leads

    def _bizwhatz_push_new(self, cfg):
        self.ensure_one()
        status = self.env['otm.bizwhatz.status.map']._status_for(self.lead_quality)
        payload = {'customerName': self.name, 'phoneNumber': cfg._digits_phone(self.phone_number),
                   'source': 'ODOO', 'remarks': (self.remarks or '')[:250]}
        if self.email_address:
            payload['email'] = self.email_address
        if self.course_interested:
            payload['products'] = [self.course_interested]
        if status:
            payload['status'] = status
        self.env['otm.bizwhatz.log']._queue_out(cfg, self, 'lead.push', 'POST', '/api/leads/external', payload)

    def action_bizwhatz_push(self):
        """Manual: send this lead (new) or its current status (already linked) to Bizwhatz."""
        cfg = self.env['otm.bizwhatz.config']._get()
        for lead in self:
            if lead.bizwhatz_id:
                lead.bizwhatz_status = False
                lead._bizwhatz_push_status(cfg)
            else:
                lead._bizwhatz_push_new(cfg)
        return {'type': 'ir.actions.client', 'tag': 'display_notification', 'params': {
            'title': _('Queued'), 'message': _('Sent to the Bizwhatz queue (runs within 2 minutes).'), 'type': 'info'}}
