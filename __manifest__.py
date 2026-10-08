{
    'name': 'Bizwhatz SmartLead Connector (Leads)',
    'version': '19.0.1.0.0',
    'summary': 'Two-way sync between Bizwhatz SmartLead CRM and Odoo Leads. Optional add-on.',
    'description': """
Separate add-on for custom_leads_19. Uninstall it and Leads keeps working unchanged.

* Bizwhatz -> Odoo : real-time webhook (lead.created / updated / assigned / status_changed / deleted),
  HMAC-SHA256 signature check, phone de-duplication, status -> Lead Quality mapping.
* Odoo -> Bizwhatz : lead status changes (and optionally new leads) are queued and pushed
  through the Bizwhatz External API with automatic retry.
* Log of every event in and out, with retry for failures.
""",
    'author': 'Otomater',
    'website': 'https://www.otomater.com',
    'category': 'Sales/CRM',
    'license': 'OPL-1',
    'depends': ['custom_leads_19'],
    'data': [
        'security/ir.model.access.csv',
        'data/status_map_data.xml',
        'data/cron.xml',
        'views/config_views.xml',
        'views/log_views.xml',
        'views/leads_views.xml',
        'views/menus.xml',
    ],
    'installable': True,
    'application': False,
}
