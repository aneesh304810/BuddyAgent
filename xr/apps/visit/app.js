// Site visit: a small example app for the Buddy XR guide. A technician works through a job with both hands busy.
// It shows the blocks (a list to tick off, progress, an alert from outside, a question) and the privacy rule:
// the customer's door code is personal, so it is shown in the lens and not read out.
//
// It is an example, not a product: the job is fixed and nothing is saved.

import { Alert, Ask, List, Progress, Screen, Text, defineApp } from "../../src/index.js";

export const JOB = { place: "Unit 4B", work: "Boiler service", code: "4471",
  steps: ["Isolate the power", "Check the pressure", "Replace the filter", "Photograph the gauge"] };

export function createVisit(job = JOB) {
  const start = { done: [], notes: [], news: "", closed: false };
  return defineApp({
    name: "Site visit",
    state: start,

    view(s) {
      if (s.closed) return Screen({ id: "closed", title: "Job closed" },
        Text(`${job.place} · ${job.work}. ${s.done.length} steps done${s.notes.length ? `, ${s.notes.length} ${s.notes.length === 1 ? "note" : "notes"}` : ""}.`, { say: true }),
        List([{ label: "Next job" }], { onSelect: (_i, _n, ctx) => ctx.set(start) }));

      const left = job.steps.filter((step) => !s.done.includes(step));
      return Screen({ id: "job", title: `${job.place} · ${job.work}` },
        s.news && Alert(s.news),
        s.news && Ask("Got it?", { yesLabel: "Got it", noLabel: "Later", yes: (ctx) => ctx.set({ news: "" }), no: (ctx) => ctx.set({ news: "" }) }),
        Text(`Door code ${job.code}`, { privacy: "personal" }),
        Progress("Done", s.done.length / job.steps.length),
        left.length ? List(left, { onSelect: (item, _i, ctx) => ctx.set({ done: [...s.done, item.label] }) }) : null,
        ...s.notes.map((n) => Text(`Note: ${n}`)),
        !left.length && !s.news && Ask("Close the job?", { yesLabel: "Close", noLabel: "Not yet", yes: (ctx) => ctx.set({ closed: true }), no: (ctx) => ctx.set({ done: s.done.slice(0, -1) }) }));
    },

    // "note the valve is worn" keeps a note; anything else is not for this app
    onVoice(words, ctx) {
      const note = words.match(/^note[:,]?\s+(.{2,120})$/i);
      if (note) ctx.set((s) => ({ notes: [...s.notes, note[1].replace(/[.!]$/, "")] }));
      else ctx.say("Say next, select, or note and what to write down.");
    },

    onEvent(event, ctx) { if (event && event.text) ctx.set({ news: String(event.text).slice(0, 120) }); },
    onBack(ctx) { ctx.set((s) => ({ done: s.done.slice(0, -1) })); },
  });
}

export const demo = () => ({ app: createVisit() });
