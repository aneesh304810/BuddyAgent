// An app from a plain description, for people who know their work and do not write code. Most work on glasses is
// a procedure: do this, check that, choose one, say a note. buildApp turns such a list into an app that shows one
// step at a time, which suits a small lens and a voice in the ear alike.
//
//   buildApp({
//     name: "Site safety walk",
//     steps: [
//       { kind: "info",   text: "Gate 2. Hard hats beyond this point." },
//       { kind: "do",     text: "Check the scaffold tags are in date" },          // Done / Could not do it
//       { kind: "ask",    text: "Are the edge barriers in place?", ifNo: "Stop work near the edge and tell the supervisor." },
//       { kind: "choose", text: "Weather", options: ["Dry", "Wet", "Windy"] },
//       { kind: "note",   text: "Anything else to report?" },                      // the wearer says it
//     ],
//     finish: "Walk complete. Thank you.",
//   })
//
// Any step may carry privacy: "personal" or "health"; the framework's privacy rule then applies to it.
// What the wearer answered is in state.answers, and options.onFinish(answers) is called at the end.
// A message from outside ({ text }) is shown as an alert on whatever step the wearer is on.

import { Alert, Ask, List, Progress, Screen, Text, defineApp } from "./widgets.js";
import { LEVELS } from "./privacy.js";

export const KINDS = { info: "Tell them something", do: "Something to do", ask: "A yes or no question", choose: "Choose one", note: "Say a note" };
const LONG = 110;                                         // past this a step fills a small lens several times over

/** What is wrong with a description, in plain words. An empty list means it can be built. */
export function problems(spec) {
  const out = [];
  if (!spec || typeof spec !== "object") return ["There is nothing to build yet."];
  if (!String(spec.name || "").trim()) out.push("Give the app a name.");
  const steps = Array.isArray(spec.steps) ? spec.steps : [];
  if (!steps.length) out.push("Add at least one step.");
  steps.forEach((s, i) => {
    const n = `Step ${i + 1}`;
    if (!s || !KINDS[s.kind]) { out.push(`${n}: choose what kind of step it is.`); return; }
    if (!String(s.text || "").trim()) out.push(`${n}: write what it says.`);
    if (s.kind === "choose" && (s.options || []).filter((o) => String(o).trim()).length < 2) out.push(`${n}: give at least two things to choose from.`);
    if (s.privacy && !LEVELS.includes(s.privacy)) out.push(`${n}: privacy must be public, personal or health.`);
  });
  return out;
}

/** Things that would work but could be better. */
export function advice(spec) {
  const out = [];
  const steps = Array.isArray(spec && spec.steps) ? spec.steps : [];
  if (String((spec && spec.name) || "").length > 20) out.push("The name is long: on a small lens it is cut to make room for the step number.");
  steps.forEach((s, i) => {
    if (s && String(s.text || "").length > LONG) out.push(`Step ${i + 1} is long. Short steps are easier to take in at a glance; consider splitting it.`);
    if (s && s.kind === "choose" && (s.options || []).length > 6) out.push(`Step ${i + 1} has many choices. More than six is slow to go through by voice.`);
  });
  if (steps.length > 25) out.push("There are many steps. Consider making two apps.");
  return out;
}

const clean = (v, max) => String(v ?? "").replace(/\s+/g, " ").trim().slice(0, max);

export function buildApp(spec, options = {}) {
  const wrong = problems(spec);
  if (wrong.length) throw new Error(wrong[0]);
  const name = clean(spec.name, 40);
  const steps = spec.steps.map((s) => ({ kind: s.kind, text: clean(s.text, 300), privacy: s.privacy || "public",
    options: s.kind === "choose" ? s.options.map((o) => clean(o, 60)).filter(Boolean).slice(0, 12) : [], ifNo: s.kind === "ask" ? clean(s.ifNo, 200) : "" }));
  const start = { at: 0, answers: [], news: "", warn: "", done: false };

  const answer = (ctx, value) => {
    const s = ctx.state, step = steps[s.at];
    const answers = [...s.answers.slice(0, s.at), { step: s.at + 1, kind: step.kind, text: step.text, value }];
    const done = s.at + 1 >= steps.length;
    ctx.set({ answers, at: done ? s.at : s.at + 1, done, news: "", warn: step.kind === "ask" && value === "no" ? step.ifNo : "" });
    if (done && options.onFinish) options.onFinish(answers);
  };
  const followUp = (answers) => answers.filter((a) => a.value === "problem" || (a.kind === "ask" && a.value === "no" && steps[a.step - 1].ifNo));

  return defineApp({
    name,
    state: start,

    view(s) {
      if (s.done) {
        const open = followUp(s.answers), notes = s.answers.filter((a) => a.kind === "note" && a.value);
        return Screen({ id: "finished", title: `${name} · finished` },
          s.warn && Alert(s.warn, { level: "warn" }),
          Text(clean(spec.finish, 200) || "All steps finished.", { say: true }),
          Text(`${open.length} to follow up, ${notes.length} ${notes.length === 1 ? "note" : "notes"}.`),
          open.length ? List(open.map((a) => ({ label: a.text, privacy: steps[a.step - 1].privacy }))) : null,
          List([{ label: "Start again" }], { onSelect: (_i, _n, ctx) => ctx.set(start) }));
      }
      const step = steps[s.at], p = { privacy: step.privacy };
      const body = step.kind === "ask" ? [Ask(step.text, { ...p, yes: (ctx) => answer(ctx, "yes"), no: (ctx) => answer(ctx, "no") })]
        : step.kind === "info" ? [Text(step.text, p), List([{ label: "Next" }], { onSelect: (_i, _n, ctx) => answer(ctx, "seen") })]
        : step.kind === "do" ? [Text(step.text, p), List([{ label: "Done" }, { label: "Could not do it" }], { onSelect: (_i, n, ctx) => answer(ctx, n === 0 ? "done" : "problem") })]
        : step.kind === "choose" ? [Text(step.text, p), List(step.options, { ...p, onSelect: (item, _n, ctx) => answer(ctx, item.label) })]
        : [Text(step.text, p), Text("Say your note, or choose No note."), List([{ label: "No note" }], { onSelect: (_i, _n, ctx) => answer(ctx, "") })];
      return Screen({ id: "step-" + s.at, title: `${name} · ${s.at + 1} of ${steps.length}` },
        s.news && Alert(s.news), s.warn && Alert(s.warn, { level: "warn" }),
        Progress("Done", s.at / steps.length), ...body);
    },

    onVoice(words, ctx) {
      const s = ctx.state;
      if (!s.done && steps[s.at].kind === "note") answer(ctx, clean(words, 200));
      else ctx.say("Say next, select, yes, no or back.");
    },
    onEvent(event, ctx) { if (event && event.text) ctx.set({ news: clean(event.text, 160) }); },
    onBack(ctx) { ctx.set((s) => s.done ? { done: false, warn: "" } : { at: Math.max(0, s.at - 1), warn: "" }); },
  });
}

/** The same app as a file a developer can take further. */
export const toCode = (spec) => `import { buildApp } from "buddy-xr";\n\nexport default buildApp(${JSON.stringify(spec, null, 2)});\n`;
