# Email auto-reply (Cloudflare Email Worker)

Every email to hello@, privacy@ or security@buddylabs.io is forwarded to the owner's Gmail, and the
sender gets one automatic reply from the address they wrote to: "we'll get back to you within 3 business
days" (privacy and security have their own wording). It never replies to no-reply senders, mailing
lists, newsletters, bounces or other auto-replies, or to buddylabs.io itself, and (with the optional KV)
at most once per sender per address every 3 days.

## Set it up (about 5 minutes, in the Cloudflare dashboard)

1. **buddylabs.io → Email → Email Routing → Email Workers** (or "Destination Workers") → **Create**.
   Name it `buddy-email`, start from the blank template, open the editor, replace everything with
   `worker.js` from this folder, then **Deploy**.
2. Optional, recommended (once-per-3-days limit): **Storage & Databases → KV → Create** a namespace
   `buddy-email-autoreply`. Then in the `buddy-email` Worker: **Settings → Bindings → Add → KV namespace**,
   variable name `AUTOREPLY`, that namespace. Deploy again.
3. **Email Routing → Routing rules**: for each of `hello`, `privacy` and `security`, set
   **Action: Send to a Worker → buddy-email** (instead of "Send to an email").
4. Test from a non-Gmail account: the email arrives in Gmail, and the sender gets the auto-reply.

The forward goes to the address in `FORWARD_TO` (top of worker.js), which must stay a verified
destination address. If an auto-reply can't be sent (for example, the sender's domain fails DMARC), the
email is still forwarded.
