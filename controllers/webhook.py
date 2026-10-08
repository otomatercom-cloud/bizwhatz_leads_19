import hashlib
import hmac
import json
import logging

from odoo import http
from odoo.http import request

from ..models.leads import upsert_from_bizwhatz

_logger = logging.getLogger(__name__)
EVENTS = {'lead.created', 'lead.updated', 'lead.assigned', 'lead.status_changed', 'lead.deleted'}


def _json(data, status=200):
    return request.make_response(json.dumps(data), headers=[('Content-Type', 'application/json')], status=status)


class BizwhatzWebhook(http.Controller):

    @http.route('/bizwhatz/webhook', type='http', auth='public', methods=['POST'], csrf=False)
    def webhook(self, **kw):
        env = request.env(su=True)
        token = request.params.get('token', '')
        cfg = env['otm.bizwhatz.config'].search([('webhook_token', '=', token), ('active', '=', True)], limit=1) if token else None
        if not cfg:
            _logger.warning('Bizwhatz webhook: bad token from %s', request.httprequest.remote_addr)
            return _json({'success': False, 'error': 'forbidden'}, 403)

        raw = request.httprequest.get_data()
        if cfg.webhook_secret:
            given = request.httprequest.headers.get('X-Hub-Signature-256', '')
            expected = 'sha256=' + hmac.new(cfg.webhook_secret.encode(), raw, hashlib.sha256).hexdigest()
            if not hmac.compare_digest(given, expected):
                _logger.warning('Bizwhatz webhook: bad signature')
                return _json({'success': False, 'error': 'bad signature'}, 401)
        try:
            body = json.loads(raw.decode('utf-8') or '{}')
        except ValueError:
            return _json({'success': False, 'error': 'invalid json'}, 400)

        event = body.get('event') or ''
        data = body.get('data') or {}
        if event not in EVENTS or not isinstance(data, dict):
            env['otm.bizwhatz.log']._log_in(event or 'unknown', body, 'ignored', error='Unsupported event')
            return _json({'success': True, 'ignored': True})
        try:
            with env.cr.savepoint():
                lead, note = upsert_from_bizwhatz(env, cfg, event, data)
        except Exception as e:  # always answer 200 so Bizwhatz does not retry forever; the log keeps the payload
            _logger.exception('Bizwhatz webhook processing failed')
            env['otm.bizwhatz.log']._log_in(event, body, 'failed', error=str(e)[:2000])
            return _json({'success': True, 'processed': False})
        return _json({'success': True, 'result': note, 'odooLeadId': lead.id if lead else None})
