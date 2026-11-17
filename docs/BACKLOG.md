# Technical backlog

Work that has been discussed and deliberately left for later. Each item says what it is, why it matters, what was
decided, and what would have to be built. Nothing here is built.

## Privacy Gateway

### Photos and scanned PDFs are read by Claude on upload

- **Added:** 17 November 2026. **Status:** not started; the owner chose to leave it for now.
- **What happens today.** A file with real text in it has its text taken out on the server, and a sample goes to
  Claude to be summarised; the Privacy Gateway cuts health lines out of that sample. A photo or a scanned PDF is
  different: the picture itself is sent to Claude to be read once (`library.py`, `_understand`). The gateway's rules
  read text, not pictures, so it is not stopped. Only after that first read can the document be marked sensitive
  and become a vault item.
- **Why it matters.** The gateway's rule is that health information reaches no AI model. A photographed lab report
  or prescription does reach Claude at upload. This is gap 2 in `docs/PRIVACY-GATEWAY.md`.
- **Proposed.**
  1. A switch on the Privacy Gateway tab, "Let Claude read photos and scanned PDFs". On keeps today's behaviour.
     Off stores pictures and scans encrypted and never sends them; Buddy keeps the file and whatever text the
     server can take out by itself, with no summary. Suggested default: on, because turning it off also stops Buddy
     reading photographed receipts, IDs and letters.
  2. A "this is a health document" mark at upload, also set automatically when the file name or the owner's note
     says so (lab, blood test, prescription, medical, Oura). A marked document goes straight to the vault and is
     never sent to any model, whatever the switch says.
- **Known weakness of the proposal.** The health mark depends on the owner ticking it or on the file name. A lab
  report photographed as "IMG_4821.jpg" with no note would still be read while the switch is on.
- **Not to be made switchable.** The health rule itself. The page says it cannot be switched off, and a test fails
  if a setting for it is added.

### Decided, no work planned

- **Speech.** Spoken words reach the speech-to-text model before any rule can read them. The owner accepted this
  on 17 November 2026. The transcript that comes back is checked like any other text.
