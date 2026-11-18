// The app built in the tutorial, one step at a time. Each step is a complete app. The tutorial page shows the
// code between the "--- step" marks and runs the same code, so what is printed is what is tested.

import { Alert, Ask, List, Progress, Screen, Text, defineApp } from "../../src/index.js";

// --- step 1
export const step1 = () => defineApp({
  name: "Delivery round",
  view: () => Screen({ title: "Delivery round" },
    Text("3 parcels left today.")),
});
// --- end

// --- step 2
const PARCELS = ["12 Mill Lane", "4 Station Road", "The Old Bakery"];

export const step2 = () => defineApp({
  name: "Delivery round",
  state: { delivered: [] },
  view(state) {
    const left = PARCELS.filter((p) => !state.delivered.includes(p));
    return Screen({ title: "Delivery round" },
      Text(`${left.length} parcels left today.`),
      List(left, {
        onSelect: (item, index, ctx) => ctx.set({ delivered: [...state.delivered, item.label] }),
      }));
  },
});
// --- end

// --- step 3
export const step3 = () => defineApp({
  name: "Delivery round",
  state: { delivered: [], open: null },
  view(state) {
    if (state.open) return Screen({ title: state.open },
      Ask("Delivered?", {
        yes: (ctx) => ctx.set({ delivered: [...state.delivered, state.open], open: null }),
        no: (ctx) => ctx.set({ open: null }),
      }));

    const left = PARCELS.filter((p) => !state.delivered.includes(p));
    return Screen({ title: "Delivery round" },
      Text(`${left.length} parcels left today.`),
      List(left, { onSelect: (item, index, ctx) => ctx.set({ open: item.label }) }));
  },
  onBack: (ctx) => ctx.set({ open: null }),
});
// --- end

// --- step 4
const GATE_CODES = { "12 Mill Lane": "2580", "The Old Bakery": "7741" };

export const step4 = () => defineApp({
  name: "Delivery round",
  state: { delivered: [], open: null },
  view(state) {
    if (state.open) return Screen({ title: state.open },
      GATE_CODES[state.open] && Text(`Gate code ${GATE_CODES[state.open]}`, { privacy: "personal" }),
      Ask("Delivered?", {
        yes: (ctx) => ctx.set({ delivered: [...state.delivered, state.open], open: null }),
        no: (ctx) => ctx.set({ open: null }),
      }));

    const left = PARCELS.filter((p) => !state.delivered.includes(p));
    return Screen({ title: "Delivery round" },
      Progress("Done", state.delivered.length / PARCELS.length),
      List(left, { onSelect: (item, index, ctx) => ctx.set({ open: item.label }) }));
  },
  onBack: (ctx) => ctx.set({ open: null }),
});
// --- end

// --- step 5
export const step5 = () => defineApp({
  name: "Delivery round",
  needs: ["mic"],
  state: { parcels: PARCELS, delivered: [], open: null, news: "" },
  view(state) {
    if (state.open) return Screen({ title: state.open },
      GATE_CODES[state.open] && Text(`Gate code ${GATE_CODES[state.open]}`, { privacy: "personal" }),
      Ask("Delivered?", {
        yes: (ctx) => ctx.set({ delivered: [...state.delivered, state.open], open: null }),
        no: (ctx) => ctx.set({ open: null }),
      }));

    const left = state.parcels.filter((p) => !state.delivered.includes(p));
    return Screen({ title: "Delivery round" },
      state.news && Alert(state.news),
      Progress("Done", state.delivered.length / state.parcels.length),
      List(left, { onSelect: (item, index, ctx) => ctx.set({ open: item.label, news: "" }) }));
  },

  // Something from outside: the depot adds a parcel to the round.
  onEvent(event, ctx) {
    if (event.type !== "parcel") return;
    ctx.set((state) => ({ parcels: [...state.parcels, event.address], news: `New parcel: ${event.address}` }));
  },

  // Words that were not a command. "skip" puts the open parcel at the end of the round.
  onVoice(words, ctx) {
    const state = ctx.state;
    if (/^skip\b/i.test(words) && state.open) {
      ctx.set({ parcels: [...state.parcels.filter((p) => p !== state.open), state.open], open: null });
    } else ctx.say("Say next, select, yes, no or skip.");
  },

  onBack: (ctx) => ctx.set({ open: null }),
});
// --- end

export const STEPS = [step1, step2, step3, step4, step5];
export default step5();
