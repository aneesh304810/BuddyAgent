// From an app's screen to what the wearer hears.
//
//   glasses with no display   the whole screen is read: the title, each sentence, how many things there are to
//                             choose from and the one the wearer is on. Moving reads the next one.
//   glasses with a display    only what must not be missed is spoken: alerts, questions, and text an app marked
//                             `say: true`. The rest is in the lens.

import { allowed, WITHHELD } from "./privacy.js";

// written marks that are not said: "2× Latte · $9.00" is heard as "2 Latte, $9.00"
const heard = (s) => String(s).replace(/(\d)\s*×\s*/g, "$1 ").replace(/\s*·\s*/g, ", ");
const end = (s) => { s = heard(s); return /[.!?]$/.test(s) ? s : s + "."; };

/** The sentences for a screen. Returns { script: [..], withheld, focused }: `focused` is the sentence for the
 *  choice the wearer is on, so moving between choices can say just that. */
export function script(screen, list, profile, { focus = 0, options = {} } = {}) {
  if (!profile.speaker) return { script: [], withheld: 0, focused: "" };
  const full = !profile.display;
  const out = [];
  let withheld = 0, held = false, focused = "";
  const say = (r, text) => {
    if (allowed(r.privacy, "speech", profile, options)) return out.push(end(text));
    withheld++;
    if (!held) { out.push(WITHHELD.speech); held = true; }     // said once a screen, not once a row
  };
  if (full && screen.props.title) out.push(end(String(screen.props.title)));
  const choosable = list.filter((r) => r.choose);
  const items = choosable.filter((r) => r.kind === "item");
  let counted = false;
  for (const r of list) {
    if (r.kind === "option") continue;
    if (r.kind === "item") {
      if (!full) continue;
      if (!r.choose) { say(r, r.say); continue; }
      if (!counted) { out.push(`${items.length} ${items.length === 1 ? "choice" : "choices"}.`); counted = true; }
      if (r === choosable[focus]) { const at = out.length, was = withheld; say(r, `${r.position[0]} of ${r.position[1]}: ${r.say}`); if (withheld === was) focused = out[at]; }
      continue;
    }
    if (r.kind === "image" && !r.say) continue;
    if (r.kind === "ask") { say(r, r.say); out.push("Say yes or no."); continue; }
    if (full || r.speak) say(r, r.say);
  }
  return { script: out, withheld, focused };
}
