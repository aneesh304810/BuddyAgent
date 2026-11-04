// Buddy Labs email: forward to the owner's inbox, then send one polite auto-reply from the address
// that was written to (hello@, privacy@, security@). Paste into a Cloudflare Email Worker; see README.md.
import { EmailMessage } from "cloudflare:email";

const FORWARD_TO = "aneeshnair304810@gmail.com"; // must be a verified destination address in Email Routing
const ONCE_PER_DAYS = 3;                         // one auto-reply per sender per address in this many days

const REPLIES = {
  hello: "Thanks for contacting Buddy Labs. We've received your message and will get back to you within 3 business days.",
  privacy: "Thanks for your privacy request. We've received it and will respond within 3 business days. "
    + "As our Privacy Policy explains, we complete access, correction, export and deletion requests within 30 days. "
    + "We may ask you to confirm your identity before acting on a request.",
  security: "Thanks for reporting a security issue to Buddy Labs. We've received your report and will respond within 3 business days. "
    + "Please don't share the details publicly until we've had a chance to look into it and fix it.",
  default: "Thanks for contacting Buddy Labs. We've received your message and will get back to you within 3 business days.",
};

export function shouldReply(from, headers) {
  const sender = (from || "").toLowerCase();
  if (!sender || sender.endsWith("@buddylabs.io")) return false;                       // no loops
  if (/(^|[.+_-])(no-?reply|do-?not-?reply|mailer-daemon|postmaster|bounces?|notifications?)([.+_-]|@)/.test(sender)) return false;
  const h = (name) => (headers.get(name) || "").toLowerCase();
  if (h("auto-submitted") && h("auto-submitted") !== "no") return false;                // auto-replies, bounces
  if (["bulk", "list", "junk", "auto_reply"].includes(h("precedence"))) return false;    // newsletters
  if (headers.get("list-id") || headers.get("list-unsubscribe")) return false;          // mailing lists
  if (h("x-auto-response-suppress")) return false;
  return true;
}

function b64(text) {
  const bytes = new TextEncoder().encode(text);
  let bin = "";
  for (const b of bytes) bin += String.fromCharCode(b);
  return btoa(bin);
}

export function buildReply({ from, to, subject, messageId, now = new Date(), id = crypto.randomUUID() }) {
  const local = to.split("@")[0].toLowerCase();
  const text = REPLIES[local] || REPLIES.default;
  const subj = /^re:/i.test(subject || "") ? subject : `Re: ${subject || "your message"}`;
  const body = `Hello,\r\n\r\n${text}\r\n\r\nThis is an automatic reply. A person will follow up.\r\n\r\nBuddy Labs\r\nhttps://buddylabs.io\r\n`;
  const lines = [
    `From: Buddy Labs <${to}>`,
    `To: ${from}`,
    `Subject: =?UTF-8?B?${b64(subj)}?=`,
    `Date: ${now.toUTCString()}`,
    `Message-ID: <${id}@buddylabs.io>`,
    ...(messageId ? [`In-Reply-To: ${messageId}`, `References: ${messageId}`] : []),
    "Auto-Submitted: auto-replied",
    "X-Auto-Response-Suppress: All",
    "MIME-Version: 1.0",
    "Content-Type: text/plain; charset=utf-8",
    "Content-Transfer-Encoding: base64",
    "",
    b64(body).replace(/.{76}/g, "$&\r\n"),
  ];
  return lines.join("\r\n");
}

export default {
  async email(message, env, ctx) {
    await message.forward(env.FORWARD_TO || FORWARD_TO);                 // the owner always gets the email
    if (!shouldReply(message.from, message.headers)) return;
    const to = message.to.toLowerCase();
    if (env.AUTOREPLY) {                                                 // optional KV: once per sender per address
      const key = `ar:${to}:${message.from.toLowerCase()}`;
      if (await env.AUTOREPLY.get(key)) return;
      await env.AUTOREPLY.put(key, "1", { expirationTtl: ONCE_PER_DAYS * 86400 });
    }
    const raw = buildReply({ from: message.from, to, subject: message.headers.get("subject"),
                             messageId: message.headers.get("message-id") });
    try {
      await message.reply(new EmailMessage(to, message.from, raw));
    } catch (e) {
      console.log("auto-reply not sent:", e && e.message);              // never lose the forwarded email over it
    }
  },
};
