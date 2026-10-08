# bizwhatz_leads_19  – Bizwhatz SmartLead ⇄ Odoo Leads (optional add-on)

Depends only on `custom_leads_19`. Uninstall it any time; Leads is untouched
(the two extra lead fields and the log/queue are removed with it).

## Setup (5 minutes)
1. Install the module. Open **Leads → Configuration → Bizwhatz → Connection** (one record per company).
2. Paste the **Organization API Key** (Bizwhatz → SmartLead → Settings → External API & Webhooks). Click **Test connection**.
3. Copy the **Webhook URL** into Bizwhatz (same Settings panel). If you set an *External Webhook Secret* there, type the same value here.
4. **Status Mapping**: click *Load Bizwhatz statuses* on the Connection form, then give each Bizwhatz status a Lead Quality.
   Tick *Use when sending to Bizwhatz* on one row per quality.
5. Optional: *Pull existing leads* to back-fill leads already in Bizwhatz.

## Flow
* Bizwhatz → Odoo: `lead.created / updated / assigned / status_changed / deleted` → lead is matched by Bizwhatz id, then by last 10 digits of the phone; created if new (source "Bizwhatz");
  status → Lead Quality by the mapping; details go to the chatter. `lead.deleted` only adds a note (Odoo lead is kept).
* Odoo → Bizwhatz: when Lead Quality changes on a lead linked to Bizwhatz, an `update-status` call is queued (cron every 2 min, 5 tries, retry button in the Log).
  Optional: send brand-new Odoo leads (`POST /api/leads/external`).
* Echo-safe: updates that came from Bizwhatz are never sent back.
