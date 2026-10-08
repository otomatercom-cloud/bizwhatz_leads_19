from odoo import fields, models


class BizwhatzStatusMap(models.Model):
    _name = 'otm.bizwhatz.status.map'
    _description = 'Bizwhatz Status to Lead Quality'
    _order = 'bizwhatz_status'

    bizwhatz_status = fields.Char(string='Bizwhatz Status', required=True)
    lead_quality = fields.Selection(selection=lambda s: s.env['leads.logic']._fields['lead_quality'].selection,
                                    string='Odoo Lead Quality')
    push_back = fields.Boolean(string='Use when sending to Bizwhatz', default=True,
                               help='When an Odoo lead changes to this quality, send this Bizwhatz status. '
                                    'If several rows share a quality, the first one ticked is used.')

    def _quality_for(self, status):
        status = (status or '').strip().lower().replace('_', ' ')
        if not status:
            return False
        for row in self.sudo().search([('lead_quality', '!=', False)]):
            if row.bizwhatz_status.strip().lower().replace('_', ' ') == status:
                return row.lead_quality
        return False

    def _status_for(self, quality):
        row = self.sudo().search([('lead_quality', '=', quality), ('push_back', '=', True)], limit=1)
        return row.bizwhatz_status if row else False
