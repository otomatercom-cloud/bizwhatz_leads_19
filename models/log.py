import json
import logging

import requests

from odoo import api, fields, models

_logger = logging.getLogger(__name__)
MAX_ATTEMPTS = 5


class BizwhatzLog(models.Model):
    _name = 'otm.bizwhatz.log'
    _description = 'Bizwhatz Event Log / Outbound Queue'
    _order = 'id desc'

    direction = fields.Selection([('in', 'Bizwhatz -> Odoo'), ('out', 'Odoo -> Bizwhatz')], required=True, index=True)
    event = fields.Char(index=True)
    state = fields.Selection([('pending', 'Pending'), ('done', 'Done'), ('failed', 'Failed'), ('ignored', 'Ignored')],
                             default='done', index=True)
    lead_id = fields.Many2one('leads.logic', ondelete='set null', index=True)
    method = fields.Char()
    path = fields.Char()
    payload = fields.Text()
    response = fields.Text()
    error = fields.Text()
    attempts = fields.Integer()
    config_id = fields.Many2one('otm.bizwhatz.config', ondelete='cascade')

    @api.model
    def _log_in(self, event, payload, state='done', lead=None, error=''):
        return self.sudo().create({
            'direction': 'in', 'event': event, 'state': state, 'lead_id': lead.id if lead else False,
            'payload': json.dumps(payload, ensure_ascii=False, default=str)[:20000], 'error': error})

    @api.model
    def _queue_out(self, config, lead, event, method, path, payload):
        rec = self.sudo().create({
            'direction': 'out', 'event': event, 'state': 'pending', 'lead_id': lead.id, 'method': method,
            'path': path, 'payload': json.dumps(payload, ensure_ascii=False), 'config_id': config.id})
        cron = self.env.ref('bizwhatz_leads_19.cron_bizwhatz_out', raise_if_not_found=False)
        if cron:
            cron.sudo()._trigger()
        return rec

    def _send(self):
        for rec in self:
            cfg = rec.config_id
            rec.attempts += 1
            try:
                resp = cfg._request(rec.method or 'POST', rec.path, payload=json.loads(rec.payload or '{}'))
                rec.response = ('HTTP %s %s' % (resp.status_code, resp.text))[:5000]
                if resp.status_code < 300:
                    rec.state, rec.error = 'done', False
                    rec._after_success(resp)
                elif resp.status_code in (400, 404, 422):  # data problem, retrying will not help
                    rec.state, rec.error = 'failed', 'Rejected by Bizwhatz (HTTP %s)' % resp.status_code
                else:
                    raise requests.RequestException('HTTP %s' % resp.status_code)
            except Exception as e:  # noqa: BLE001 - keep the queue alive
                rec.error = str(e)[:2000]
                rec.state = 'failed' if rec.attempts >= MAX_ATTEMPTS else 'pending'
                _logger.warning('Bizwhatz push %s failed (%s): %s', rec.id, rec.attempts, e)

    def _after_success(self, resp):
        """Remember the Bizwhatz lead id returned when an Odoo lead was pushed."""
        self.ensure_one()
        if not self.lead_id or self.lead_id.bizwhatz_id:
            return
        try:
            data = resp.json()
        except ValueError:
            return
        node = data.get('lead') if isinstance(data, dict) else None
        node = node or (data.get('data') if isinstance(data, dict) else None) or {}
        bid = node.get('id') if isinstance(node, dict) else None
        if bid:
            self.lead_id.sudo().with_context(otm_bizwhatz_skip=True).write({'bizwhatz_id': str(bid)})

    @api.model
    def cron_process_out(self):
        for rec in self.sudo().search([('direction', '=', 'out'), ('state', '=', 'pending')], order='id', limit=50):
            rec._send()
            self.env.cr.commit()

    def action_retry(self):
        for rec in self.filtered(lambda r: r.direction == 'out'):
            rec.write({'state': 'pending', 'attempts': 0})
        self.sudo().cron_process_out()
