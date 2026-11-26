# Test cases for Stage 9 (implementation)

Automated in the prototype today: `prototype.test.js` (116 checks). The same cases become Compose UI tests and page
tests when the app is changed.

| ID | Case | Expected |
| --- | --- | --- |
| HOME-1 | Cold start | Home › Conversation; composer and mic on screen without scrolling, folded and open |
| HOME-2 | Type and send | Message appears; Buddy's reply with any card; composer clears |
| HOME-3 | Mic tap, speak, Buddy answers, tap during the answer | listening → thinking → speaking → interrupted → listening; each state has words |
| HOME-4 | Server unreachable | Error state with Retry; no data lost |
| HOME-5 | Quick actions | Log a meal, Log BP, Scan food open their sheets in 1 tap; Spending and Bills open Money sections; Deals opens Explore › Deals |
| HLTH-1 | Type "my blood pressure was 150/95" in the conversation | Turned away to Health; no model request contains it (assert on the Privacy Gateway record) |
| HLTH-2 | Log BP from Home | Saved; shows on Health › Today; nothing in the conversation |
| HLTH-3 | Scan a barcode | Verdict with Good, Watch and "What this rests on"; Add to a meal; Compare |
| HLTH-4 | Health screens make no AI request | Network log: no call to any AI provider while in Health |
| MON-1 | "Pay my card bill" | Approval card with amount, destination, source, time, risk; nothing executes before Approve |
| MON-2 | Approve with a changed amount (tampered request) | Server refuses: hash mismatch; audit log records the refusal |
| MON-3 | Approve twice | Second approval refused: token already used |
| NAV-1 | Each destination | 2 to 5 sections, no inner tab rows |
| NAV-2 | Back from a sheet, a section, a destination | sheet closes → default section → Home → exit |
| NAV-3 | Old deep links (e.g. Discover › Intelligence) | Land on the mapped new place |
| VIS-1 | Every destination, folded and open, light and dark | No sideways scroll; screenshot matches the reference |
| A11Y-1 | TalkBack through Home and Health | Every control announced with its label and state |
| A11Y-2 | Font size 200% | No clipped text; tables scroll inside their box |
| PLAN-1 | Free plan | Premium markers on advanced research only; privacy, export, deletion available |
| DATA-1 | Export | Archive with every domain's data; recorded in the audit log |
| DATA-2 | Delete everything | Two-step confirmation; data plane wiped; keys destroyed |
| ISO-1 | Two customers' data planes | A token for one never reads the other (request returns 403) |
