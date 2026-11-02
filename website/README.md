# Company website

The public website for Buddy Labs and its first product, Buddy. A static site with no build step. The pages are Home, Buddy, Security, Privacy, Terms, Contact and 404.
To have Claude change or rebuild it, use [CLAUDE_BUILD_INSTRUCTIONS.md](CLAUDE_BUILD_INSTRUCTIONS.md).

## Change the company name or email

```
bash website/rename.sh "Your Company Name" hello@yourdomain.com
```

## Put it online (free): Cloudflare Pages

1. Buy the domain at the at-cost price (about $10 a year for .com): dash.cloudflare.com →
   **Domain Registration → Register Domains**.
2. In **Workers & Pages → Create → Pages**, connect GitHub and pick the `BuddyAgent` repo (a private
   repo works).
   - Production branch: the one you deploy from.
   - Build command: *(none)*.
   - Build output directory: `website`.
3. In that project, go to **Custom domains → Set up a domain** and enter your domain. HTTPS is
   automatic.
4. Optional, for an email address on the domain: **Email Routing** forwards `hello@yourdomain.com`
   to your Gmail for free. Then run `rename.sh` with that address.

Other hosts that also work: Netlify (drag and drop the `website` folder) and GitHub Pages (needs a
public repo, or a paid plan for a private one).

## Using the site elsewhere (Plaid and other services)

The privacy and terms pages also serve services that ask for them, such as Plaid:

- **Settings → Company profile**: company name, website `https://yourdomain.com`, and the legal
  entity if you form one.
- **Link → Link customization**: set the display name to "Buddy" and add your logo.
- **Settings → Compliance / Security questionnaire**, wherever it asks:
  - privacy policy: `https://yourdomain.com/privacy.html`;
  - terms: `https://yourdomain.com/terms.html`.
- Keep the site's privacy page in step with the "Buddy Privacy and Data Retention Policy" document.
  Update both when Buddy starts collecting a new kind of data.
