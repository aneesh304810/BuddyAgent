// The building blocks an app is written in. Each returns a plain object; nothing is drawn here. The same tree is
// laid out as lines on a display (layout.js) and as sentences for the ear (speech.js).
//
// Every block takes `privacy`: "public" (the default), "personal" or "health". A Screen's privacy applies to
// everything in it unless a block sets its own. See privacy.js for what each level allows.

const node = (type, props = {}, children = []) => ({ type, props, children: children.flat().filter(Boolean) });

/** One screen. `id` names it (focus and paging start again when it changes); `title` is its first line. */
export const Screen = (props, ...children) => node("screen", props, children);

/** A sentence or two. `say: true` also speaks it on glasses that have both a display and a speaker. */
export const Text = (text, props = {}) => node("text", { ...props, text: String(text) });

/** A label and its value on one line: Stat("Total", "$21.00"). */
export const Stat = (label, value, props = {}) => node("stat", { ...props, label: String(label), value: String(value) });

/** Rows the wearer can move through. Items are strings or { label, detail, value, privacy }.
 *  onSelect(item, index, ctx) runs when one is chosen; without it the list is read-only. */
export const List = (items, props = {}) => node("list", { ...props, items: items.map((i) => typeof i === "string" ? { label: i } : i) });

/** How far along something is, 0 to 1. */
export const Progress = (label, value, props = {}) => node("progress", { ...props, label: String(label), value: Math.max(0, Math.min(1, Number(value) || 0)) });

/** A yes-or-no question. The wearer answers by voice ("yes"/"no") or by moving to an option and choosing it. */
export const Ask = (question, props = {}) => node("ask", { yesLabel: "Yes", noLabel: "No", ...props, question: String(question) });

/** Something the wearer must not miss. Always spoken where there is a speaker. level: "info" | "warn". */
export const Alert = (text, props = {}) => node("alert", { level: "info", ...props, text: String(text) });

/** A picture, for displays that show them. `alt` is what the wearer gets everywhere else: always give one. */
export const Image = (src, props = {}) => node("image", { ...props, src: String(src) });

/** An app: its starting state, the screen for a state, and what to do with events.
 *    view(state, ctx)      -> Screen(...)
 *    onVoice(text, ctx)    words that were not a navigation command
 *    onEvent(event, ctx)   something from outside (an office, a server): run.notify(event)
 *    onBack(ctx)           the wearer went back
 *    needs                 what the app cannot work without: ["camera"], ["mic"]
 */
export function defineApp(app) {
  if (!app || typeof app.view !== "function") throw new Error("An app needs a view(state, ctx) function.");
  if (!app.name) throw new Error("An app needs a name.");
  return { state: {}, needs: [], ...app };
}
