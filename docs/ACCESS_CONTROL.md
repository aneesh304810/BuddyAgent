# Access control policy

| | |
|---|---|
| Organization | Aneesh Nair (individual developer), operating the personal assistant "Buddy" |
| Applies to | Buddy's server (DigitalOcean droplet), its data and settings, the Buddy Android app, and the consoles that control them: Plaid, Tailscale, Google account, GitHub, DigitalOcean, Anthropic |
| Owner | Aneesh Nair |
| Version | 1.0, 2 November 2026 |
| Review | At least once a year, and whenever the setup changes |

## 1. Purpose

Bank data from Plaid, and everything else Buddy holds, must be reachable only by the people and
services that need it, with the least access that does the job. This policy says who may have
access, how access is given, checked and removed, and where the evidence is kept.

## 2. Principles

- **Least privilege.** Each person, service and key gets only what its role needs.
- **One identity.** People sign in with one central identity (section 4), protected by two-step login.
- **No shared accounts.** Every login belongs to one named person or one service.
- **Deny by default.** Anything not on the approved access list is removed: automatically for
  server accounts and SSH keys (section 6), and flagged for review otherwise.
- **Evidence.** Reviews and removals are recorded with a date (section 8).

## 3. Roles (role-based access control)

Access is granted by role, never person by person. Each role's limits are enforced by the system
named in the last column, not left to good behaviour.

| Role | Who or what | May | May not | Enforced by |
|---|---|---|---|---|
| **Owner / administrator** | Aneesh Nair | Everything: server root through Tailscale SSH, settings, keys, the Plaid, Tailscale, Google, GitHub and DigitalOcean consoles | — | Tailscale policy `autogroup:admin` (`deploy/tailscale-policy.hujson`); two-step login on every console |
| **App user** | The owner's phone or browser holding the app token | Talk to Buddy; see their own data; approve actions | Server shell, settings, keys | The token check on every request (constant time); fails closed without a token; Tailscale network only |
| **Buddy core service** | Unix account `buddy` (no login shell) | Read its settings in memory; read and write its own database and backups; call Plaid, Claude and the other providers | Log in; become root; write outside its data folders; read the settings file on disk | systemd hardening (`security.sh harden`); sealed settings only the service can decrypt; file permissions |
| **Agents** (Gmail and built agents) | Separate processes started by Buddy | Only the settings they declare (for example `BUDDY_EMAIL_*`) | Bank, database, app-token or Claude keys, even if they ask with a wildcard | `CORE_ONLY` in `backend/buddy/agents.py`; approval for anything that changes things |
| **Code sandbox** | Unix account `buddy-sandbox`, in Docker | Run code in its own workspace | Buddy's settings, database or network services | Its own account and container; its own token (`docs/SANDBOX.md`) |
| **Other tailnet members** | Anyone invited to the tailnet later | Their own devices | Any connection to the server | Tailscale policy: only admins reach `tag:buddy-server` |

Plaid secrets (`BUDDY_PLAID_*`) are held only by the core service role. Plaid dashboard access is
held only by the owner role.

## 4. Centralized identity and access management

- **One identity provider.** The owner's Google account, with two-step verification or a passkey,
  is the sign-in for Tailscale and the Plaid dashboard. GitHub and DigitalOcean use their own logins,
  each with two-step verification on.
- **Server access goes through Tailscale only.** Public SSH is closed by the firewall. Shell access
  is Tailscale SSH, which checks the Google identity and asks you to sign in again every 12 hours
  (`check` mode). Network access by role is managed centrally in the Tailscale policy file.
- **Device keys expire.** Tailscale device keys expire, so a device must sign in again with the
  central identity. If that identity is disabled, access ends.
- **Device approval.** New devices need admin approval in the Tailscale admin console
  (Settings → Device management → Device approval: on).
- **One place for keys.** Service keys live only in Buddy's sealed settings and are set with the
  hidden prompt `sudo bash /opt/buddy/deploy/set.sh NAME`. Every change is dated, and keys older
  than 90 days get a rotation reminder.

## 5. Granting access

1. Decide the role from section 3. Never give more than that role allows.
2. Give it:
   - a person's device: they sign in to Tailscale with their identity, and an admin approves the device;
   - a key: save it with `set.sh NAME`;
   - an agent: declare only the settings it needs in `agents.yaml`.
3. Run an access review (`sudo bash /opt/buddy/deploy/access.sh review`) so the new access goes on
   the approved list. Until then, the weekly check flags it, and server accounts or SSH keys are
   removed automatically.

## 6. Removing access: people leaving or changing role (automated)

- **Leaving or changing role:**
  `sudo bash /opt/buddy/deploy/access.sh offboard NAME`
  This does every step at once and logs it:
  1. locks the server account (password locked, account expired, shell removed);
  2. removes it from sudo and its sudoers file;
  3. removes its SSH keys and ends its open sessions;
  4. removes its Tailscale devices (automatic when `BUDDY_TAILSCALE_API_KEY` is set);
  5. issues a new app token, so old app links stop working.

  It then lists the keys to rotate (Plaid secret, Claude key, Gmail app passwords) and the consoles
  to remove the person from (Plaid, Tailscale, GitHub, DigitalOcean).
- **Daily automatic removal.** A timer (`buddy-access.timer`, installed by `update.sh`) locks any
  server account and removes any SSH key that isn't on the approved list. To pause it:
  `BUDDY_ACCESS_AUTO_REVOKE=0`.
- **Weekly check.** The weekly security check flags tailnet users and devices that aren't on the
  approved list, and devices not seen for 60 days. The findings reach the owner in Buddy's
  morning digest.
- **Lost phone.** Run `security.sh rotate-token`, then remove the device:
  `sudo bash /opt/buddy/deploy/access.sh revoke device NAME`.

## 7. Periodic access reviews and audits

- **Quarterly** (at least every 90 days; the weekly check says when one is due):
  `sudo bash /opt/buddy/deploy/access.sh review`

  It lists every server account and admin, SSH key, sudoers file, tailnet user and device (with
  last-seen dates) and key ages (names only). It asks you to confirm the things only you can check,
  and offers to revoke anything unexpected. It then records the result, your name and the new
  approved list.

  The checks only you can make:
  - Plaid team members and two-step login;
  - where the Plaid keys are;
  - Google account security;
  - Tailscale admins, device approval and key expiry;
  - GitHub collaborators and token expiry;
  - the DigitalOcean team;
  - which phones hold the app link.
- **Weekly audit** (automatic): patching, key ages, leaked keys in logs, network exposure, the
  firewall, SSH passwords and differences from the approved access list. See `docs/SECURITY.md`.
- **Yearly:** review this policy and the roles in section 3.

## 8. Evidence and records

| Record | Where | Kept |
|---|---|---|
| Access reviews (inventory, answers, reviewer, date) | `/var/lib/buddy/access-reviews/review-*.json` (root only) | 2 years |
| Approved access list | `/etc/buddy/access-approved.json` (root only) | Current |
| Revocations and offboarding | `/var/log/buddy-access.log`; removed SSH keys kept in `/var/lib/buddy/access-reviews/` | 2 years |
| Weekly security check | `/var/lib/buddy/security.json`, `/var/log/buddy-maintain.log` | 1 year |
| Key change dates (names only) | `/etc/buddy/key-dates` | Current |

## 9. Plaid attestations: what backs each one

| Plaid attestation | Implemented by |
|---|---|
| Periodic access reviews and audits | Section 7: the quarterly `access.sh review`, with records kept; the weekly automatic audit |
| Automated de-provisioning or modification of access for terminated or transferred employees | Section 6: `access.sh offboard`; the daily automatic removal of anything not approved; Tailscale key expiry |
| Centralized identity and access management | Section 4: one Google identity with two-step login for Tailscale and Plaid; all server access through Tailscale SSH; keys in one sealed store |
| A defined and documented access control policy | This document |
| Role-based access control | Section 3: roles enforced by the Tailscale policy, Unix accounts, systemd, the app token and `CORE_ONLY` |

Attest only once the setup steps in section 10 are done and the first review is recorded.

## 10. One-time setup

1. Update the server: `sudo bash /opt/buddy/deploy/update.sh` (installs the daily timer).
2. In the Tailscale admin console:
   - Access controls: paste `deploy/tailscale-policy.hujson` and save;
   - on the server, run `sudo tailscale up --ssh --advertise-tags=tag:buddy-server`;
   - Settings → Device approval: on;
   - check that key expiry is on for your devices.

   Tagged devices don't expire, so the server keeps working.
3. Optional, for automatic device removal: create a Tailscale API key (Settings → Keys, 90 days),
   then save it with `sudo bash /opt/buddy/deploy/set.sh BUDDY_TAILSCALE_API_KEY`.
4. Turn on two-step login in Plaid, Google, GitHub and DigitalOcean, if it isn't already.
5. Run the first review: `sudo bash /opt/buddy/deploy/access.sh review`.

## Revision history

| Date | Change |
|---|---|
| 2 November 2026 | First version, for Plaid production access |
