# Zapier: thousands of everyday apps for Buddy

Zapier connects about 8,000 apps. Through **Zapier MCP**, Buddy can use the actions
you allow there: "add this to my Notion", "create a Todoist task for Friday",
"log $40 lunch in my expenses sheet", "post in the family Slack".

Buddy treats Zapier like any other agent:

| Rule | What it means |
|---|---|
| **Asks before changes** | Anything that creates, sends, updates or deletes waits for your yes (checked in code). |
| **Lookups don't ask** | Actions named find / search / lookup / get / list / retrieve run straight away. |
| **Limits** | 20 calls a day and 45 a month by default (the free plan's 100 tasks ≈ 50 calls, since each call costs Zapier about 2 tasks). |
| **Audit and kill switch** | Every call is logged with arguments masked; *"Buddy, pause Zapier"* switches it off. |
| **Secrets stay on the server** | The Zapier link lives in Buddy's root-only settings (encrypted once sealed), never in code or chat. |

**Keep sensitive apps off Zapier.** Bank, Gmail, calendars, phone notifications and
health stay on Buddy's own connectors (`docs/CONNECTORS.md`) so they never pass
through a third company. Use Zapier for everyday, low-sensitivity apps.

## Set it up from your phone (about 10 minutes)

1. In Chrome, open **mcp.zapier.com** and sign in (or create a free Zapier account).
2. Create a new MCP server and give it a name like **Buddy**.
3. **Add tools**: pick an app (Notion, Todoist, Google Sheets, Slack...), pick the action
   (for example *Notion: Create Page*, *Google Sheets: Create Spreadsheet Row*,
   *Todoist: Create Task*), and connect your account for that app. Only the actions you
   add here are available to Buddy; add a few you'll really use.
4. Open the server's **Connect** tab and copy the **server URL**. If Zapier shows a
   separate **API key** for it, copy that too. Both are secrets: anyone with them can
   use those actions.
5. In Termux: `ssh root@buddy`, then `sudo bash /opt/buddy/deploy/security.sh edit` and add:
   ```
   ZAPIER_MCP_URL=https://mcp.zapier.com/api/mcp/...      (the URL you copied)
   ZAPIER_MCP_TOKEN=...                                   (only if Zapier gave an API key)
   ```
   Save (**Ctrl+O, Enter, Ctrl+X**).
6. If your server was set up before Zapier support arrived, add the `zapier` block from
   `/opt/buddy/backend/agents.example.yaml` to `/opt/buddy/backend/agents.yaml`.
7. `systemctl restart buddy`

**Check:** say *"Which agents do you have?"*. Buddy lists **zapier** with the tools you
added. Then try *"Add a Todoist task: call the dentist, Friday."* Buddy reads the
details back and waits for your **yes**.

## Changing limits

Edit the `zapier` block in `/opt/buddy/backend/agents.yaml`:

```yaml
    daily_call_limit: 20
    monthly_call_limit: 45      # Professional plan (~750 tasks): about 350
```

then `systemctl restart buddy`. Usage: `curl -H "Authorization: Bearer TOKEN"
https://buddy.<tailnet>.ts.net/admin/agents` shows `calls_last_30_days`.

To stop using Zapier: delete the MCP server at mcp.zapier.com (that also revokes the
link), or remove `ZAPIER_MCP_URL` from the settings file.
