# Buddy Labs Information Security Program

Source: https://claude.ai/artifact/1vTCZcPNPFscwPmQksXXDs, revision 8

Nov 4, 2026 · @Aneesh

## 1. Purpose and scope

This is Buddy Labs' written information security program. It protects customer information in Buddy and follows the FTC Safeguards Rule (16 CFR Part 314, under the Gramm-Leach-Bliley Act). **It is a draft for review by counsel**; counsel should confirm which parts of the Rule apply.

- **Customer information** means any non-public personal information about a person who uses Buddy: bank and card data received through Plaid, connected email and calendar data, device data, documents, voice data, and anything users tell Buddy.
- **Systems in scope:** the Buddy server and its database, backups and secrets; the Buddy Android app; the company website and waitlist; and the consoles that control them (Plaid, cloud hosting, private network, source code, AI providers).
- **Current state:** Buddy is in private testing. Its only user is the founder, so today the program protects one person's data. It is written to the standard required once Buddy has customers.
- **Small-business note:** the Rule exempts institutions holding information on fewer than 5,000 consumers from the written risk assessment, the incident response plan, annual board reporting and some testing duties (16 CFR 314.6). Buddy Labs follows them anyway, so nothing changes when it grows.

## 2. Qualified Individual and governance

Aneesh Nair, founder, is the **Qualified Individual** who oversees, carries out and enforces this program (16 CFR 314.4(a)).

- **Responsibilities:** owns the risk assessment, approves access, reviews the weekly security check, runs the quarterly access review, manages service providers, and leads incident response.
- **Annual report:** at least once a year the Qualified Individual writes a short report on the program's status, material risks, testing results, security events and recommended changes. Until Buddy Labs has a board, the report is kept with the company records and reviewed by the owner (16 CFR 314.4(i)).
- **Related policies:**
  - Access Control Policy (roles, granting and removing access, quarterly reviews);
  - Privacy and Data Retention Policy;
  - Biometric Information Privacy Policy;
  - Security overview (docs/SECURITY.md in the code repository).
- **If the team grows:** a second person reviews security changes, and the Qualified Individual role can move to a dedicated security lead or a qualified service provider, with Buddy Labs keeping responsibility.

## 3. Risk assessment

The highest risks are a stolen administrator login, a leaked API key, and a flaw in Buddy's code or an AI provider exposing data. Each has controls in place; the residual risk is low to medium. The assessment is repeated at least yearly and after any major change (16 CFR 314.4(b)).

**Customer information held**

| Data | Where it is | Sensitivity |
| --- | --- | --- |
| Bank and card data (Plaid), Plaid access tokens | Encrypted database on the Buddy server | High |
| Connected email and calendar data | Encrypted database (email 30 days, calendar 14 days) | High |
| Voiceprint (biometric) | Encrypted database, as numbers only | High |
| Documents and photos users share | Encrypted files on the server | High |
| Device data (notifications, location) | Encrypted database (7 days, latest location only) | Medium |
| Preferences, goals, notes | Encrypted database | Medium |
| Waitlist emails | Website hosting provider's key-value store | Low |

**Threats, controls and residual risk**

| Threat | Controls in place | Residual risk |
| --- | --- | --- |
| Stolen administrator login | MFA on every console; server reachable only over the private network; re-authentication for shell access | Low |
| Leaked API key or secret | Keys sealed at rest, kept out of logs, scanned for in logs weekly, rotation reminder at 90 days, least-privilege keys | Low |
| Server compromise through a vulnerable package | Daily security updates, weekly package vulnerability scan with automatic fixes, hardened service account | Medium |
| Flaw in Buddy's own code | Tests, code scanning and secret scanning on every change; approvals for actions checked in code | Medium |
| AI provider exposure | Personal data only to providers whose terms forbid training; card and ID numbers masked first | Medium |
| Lost or stolen phone | App token in the Android Keystore; token rotation; device removal from the private network | Low |
| Untrusted content (emails, web pages) steering the AI | Content treated as information, never instructions; actions need the user's yes | Medium |
| Backup theft | Backups encrypted with a key that isn't on the server; 14-day retention | Low |
| Insider misuse | One administrator today; access logged and reviewed quarterly; written access policy | Low |

## 4. Safeguards

Each safeguard the Rule requires (16 CFR 314.4(c)) is in place today, enforced by the system named.

| Requirement | What Buddy Labs does |
| --- | --- |
| Access controls (c)(1) | Role-based, least-privilege access under the written Access Control Policy. One central identity with MFA. The server has no public ports and is reachable only over a private network with device approval. Quarterly access reviews; a daily job removes server accounts and SSH keys not on the approved list. Add-on components can't receive bank, database or app keys. |
| Data and systems inventory (c)(2) | Data inventory in section 3; systems listed in the access review. Both are updated at each review. |
| Encryption (c)(3) | At rest: SQLCipher database (AES-256), uploaded files with AES-256-GCM, secrets sealed by the operating system, backups encrypted with a key kept off the server. In transit: HTTPS with TLS 1.2 or higher, inside a WireGuard private network. |
| Secure development (c)(4) | Automated tests, dependency vulnerability scanning (pip-audit), code scanning (bandit) and secret scanning on every change and weekly. Untrusted content is never treated as instructions. Generated code must pass safety checks and the owner's approval before it runs. |
| Multi-factor authentication (c)(5) | MFA on the Google identity used for the private network and Plaid, on cloud hosting and on source code hosting. Shell access requires re-authentication. |
| Disposal (c)(6) | Retention periods enforced automatically: bank data 24 months, email 30 days, calendar 14 days, device data 7 days, backups 14 days. Disconnecting a bank revokes access at Plaid and deletes its data within 30 days. Data is disposed of no later than 2 years after its last use unless it's still needed or the law requires keeping it. |
| Change management (c)(7) | Every change goes through source control and the test suite. The server is updated only by the owner running the update script; Buddy never deploys itself. Failed package updates roll back automatically. |
| Monitoring and logging (c)(8) | Weekly security check: vulnerabilities, key ages, secrets in logs, exposed services, firewall, SSH settings, access changes and backups. Findings go to the owner's daily health check. Logs mask card numbers, IDs and secrets, and are kept for 1 month. |

## 5. Testing and monitoring

Safeguards are tested continuously, plus a vulnerability assessment every 6 months and a penetration test every year (16 CFR 314.4(d)).

- **Continuous:** the weekly security check, daily security updates, and code and dependency scans on every change.
- **Every 6 months:** a vulnerability assessment of the server, app and website, plus after any major change.
- **Every year:** a penetration test by an independent tester, starting before public launch.
- **Findings** are logged, fixed by severity (critical within 7 days, high within 30), and summarized in the annual report.

## 6. Training and personnel

Everyone with access to customer information gets security awareness training before access and yearly after (16 CFR 314.4(e)).

- Training covers phishing, password and MFA hygiene, handling customer data, reporting incidents, and this program.
- The Qualified Individual keeps current on security threats and records the training taken each year.
- New staff get access only through the Access Control Policy; leavers lose it through the offboarding process the same day.

## 7. Service provider oversight

Buddy Labs picks providers that can protect customer information, requires it in their terms, and reviews them yearly (16 CFR 314.4(f)).

| Provider (type) | What it receives | Review |
| --- | --- | --- |
| Plaid (bank connections) | Bank data users choose to share | Plaid's security program and End User Privacy Policy |
| Anthropic (AI) | Requests and the data needed to answer them; not used for training | Commercial terms; data retention settings |
| Speech-to-text provider | Short voice clips | Terms; no storage by Buddy |
| Other AI and search providers | General text only, never personal or financial data | Enforced in code |
| DigitalOcean (hosting) | Encrypted data at rest | SOC 2 report |
| Tailscale (private network) | Encrypted traffic it can't read | SOC 2 report |
| Cloudflare (website and waitlist) | Waitlist emails and names | SOC 2 report |
| GitHub (source code) | Code only; no customer data or secrets | Secret scanning |

Before launch, sign each provider's data processing agreement (DPA) and keep copies with this program.

## 8. Incident response plan

A suspected security event is contained the same day, and the FTC is notified within 30 days when unencrypted information on 500 or more people may have been taken (16 CFR 314.4(h) and (j)).

**Who:** the Qualified Individual leads. Outside help as needed: counsel, a forensic firm, the cyber insurer.

**Steps**

1. **Detect and record:** time found, what was seen, systems involved. Sources: the weekly check, alerts, provider notices, user reports to the security contact.
2. **Contain:**
   - rotate the affected keys and the app token;
   - remove compromised devices and accounts (access.sh offboard or revoke);
   - revoke Plaid access tokens if bank data may be exposed;
   - take the server off the network if needed.
3. **Assess:** what data, whose, whether it was encrypted, and whether it was taken.
4. **Notify:** as in the table below. Counsel confirms the deadlines.
5. **Recover:** restore from an encrypted backup onto a clean server if needed, then confirm the weakness is fixed.
6. **Learn:** a written review within 14 days. Update this program and the risk assessment.

**Notification**

| Who | When |
| --- | --- |
| FTC (online form) | Within 30 days of discovery, if unencrypted customer information on 500+ people was acquired without authorization |
| Affected users | As required by state breach laws, usually without unreasonable delay (some states set 30–60 days) |
| State attorneys general | Where state law requires, usually above a set number of residents |
| Plaid | Promptly, as its developer terms require, when Plaid data is involved |
| Other providers and the insurer | Promptly, as their contracts require |

Records of every incident, including ones that needed no notice, are kept for at least 5 years.

## 9. Review and open items

The program is reviewed at least yearly, and after any security event, major system change or new kind of customer data (16 CFR 314.4(g)).

**To do before public launch**

- [ ] Counsel review of this program and confirmation of which Safeguards Rule parts apply
- [ ] First quarterly access review recorded (access.sh review)
- [ ] Network access policy and device approval turned on in the private network console
- [ ] Independent penetration test, and the first 6-month vulnerability assessment
- [ ] Data processing agreements signed with each provider in section 7
- [ ] Security awareness training completed and recorded
- [ ] Incident response tabletop exercise (one simulated incident, walked through end to end)
- [ ] Cyber liability insurance in place
- [ ] Annual report template created; first report due one year after launch

**Revision history**

| Date | Change | By |
| --- | --- | --- |
| 4 November 2026 | First version | Aneesh Nair |
