// Made by tools/site.mjs from the tutorial app and the example app. Do not edit by hand.
export const TEMPLATES = [
 {
  "id": "blank",
  "name": "Start here: a small app",
  "code": "import { Ask, List, Screen, Text, defineApp } from \"buddy-xr\";\n\nexport default defineApp({\n  name: \"My app\",\n  state: { count: 0 },\n  view(state) {\n    return Screen({ title: \"My app\" },\n      Text(`You have chosen ${state.count} times.`),\n      List([\"Choose me\", \"Or me\"], { onSelect: (item, index, ctx) => ctx.set({ count: state.count + 1 }) }));\n  },\n});\n",
  "events": ""
 },
 {
  "id": "step1",
  "name": "Tutorial 1: One screen",
  "code": "import { Screen, Text, defineApp } from \"buddy-xr\";\n\nexport default defineApp({\n  name: \"Delivery round\",\n  view: () => Screen({ title: \"Delivery round\" },\n    Text(\"3 parcels left today.\")),\n});\n",
  "events": ""
 },
 {
  "id": "step2",
  "name": "Tutorial 2: A list to choose from",
  "code": "import { List, Screen, Text, defineApp } from \"buddy-xr\";\n\nconst PARCELS = [\"12 Mill Lane\", \"4 Station Road\", \"The Old Bakery\"];\n\nexport default defineApp({\n  name: \"Delivery round\",\n  state: { delivered: [] },\n  view(state) {\n    const left = PARCELS.filter((p) => !state.delivered.includes(p));\n    return Screen({ title: \"Delivery round\" },\n      Text(`${left.length} parcels left today.`),\n      List(left, {\n        onSelect: (item, index, ctx) => ctx.set({ delivered: [...state.delivered, item.label] }),\n      }));\n  },\n});\n",
  "events": ""
 },
 {
  "id": "step3",
  "name": "Tutorial 3: A question",
  "code": "import { Ask, List, Screen, Text, defineApp } from \"buddy-xr\";\n\nconst PARCELS = [\"12 Mill Lane\", \"4 Station Road\", \"The Old Bakery\"];\n\nexport default defineApp({\n  name: \"Delivery round\",\n  state: { delivered: [], open: null },\n  view(state) {\n    if (state.open) return Screen({ title: state.open },\n      Ask(\"Delivered?\", {\n        yes: (ctx) => ctx.set({ delivered: [...state.delivered, state.open], open: null }),\n        no: (ctx) => ctx.set({ open: null }),\n      }));\n\n    const left = PARCELS.filter((p) => !state.delivered.includes(p));\n    return Screen({ title: \"Delivery round\" },\n      Text(`${left.length} parcels left today.`),\n      List(left, { onSelect: (item, index, ctx) => ctx.set({ open: item.label }) }));\n  },\n  onBack: (ctx) => ctx.set({ open: null }),\n});\n",
  "events": ""
 },
 {
  "id": "step4",
  "name": "Tutorial 4: Something private",
  "code": "import { Ask, List, Progress, Screen, Text, defineApp } from \"buddy-xr\";\n\nconst PARCELS = [\"12 Mill Lane\", \"4 Station Road\", \"The Old Bakery\"];\n\nconst GATE_CODES = { \"12 Mill Lane\": \"2580\", \"The Old Bakery\": \"7741\" };\n\nexport default defineApp({\n  name: \"Delivery round\",\n  state: { delivered: [], open: null },\n  view(state) {\n    if (state.open) return Screen({ title: state.open },\n      GATE_CODES[state.open] && Text(`Gate code ${GATE_CODES[state.open]}`, { privacy: \"personal\" }),\n      Ask(\"Delivered?\", {\n        yes: (ctx) => ctx.set({ delivered: [...state.delivered, state.open], open: null }),\n        no: (ctx) => ctx.set({ open: null }),\n      }));\n\n    const left = PARCELS.filter((p) => !state.delivered.includes(p));\n    return Screen({ title: \"Delivery round\" },\n      Progress(\"Done\", state.delivered.length / PARCELS.length),\n      List(left, { onSelect: (item, index, ctx) => ctx.set({ open: item.label }) }));\n  },\n  onBack: (ctx) => ctx.set({ open: null }),\n});\n",
  "events": ""
 },
 {
  "id": "step5",
  "name": "Tutorial 5: News from outside, and voice",
  "code": "import { Alert, Ask, List, Progress, Screen, Text, defineApp } from \"buddy-xr\";\n\nconst PARCELS = [\"12 Mill Lane\", \"4 Station Road\", \"The Old Bakery\"];\nconst GATE_CODES = { \"12 Mill Lane\": \"2580\", \"The Old Bakery\": \"7741\" };\n\nexport default defineApp({\n  name: \"Delivery round\",\n  needs: [\"mic\"],\n  state: { parcels: PARCELS, delivered: [], open: null, news: \"\" },\n  view(state) {\n    if (state.open) return Screen({ title: state.open },\n      GATE_CODES[state.open] && Text(`Gate code ${GATE_CODES[state.open]}`, { privacy: \"personal\" }),\n      Ask(\"Delivered?\", {\n        yes: (ctx) => ctx.set({ delivered: [...state.delivered, state.open], open: null }),\n        no: (ctx) => ctx.set({ open: null }),\n      }));\n\n    const left = state.parcels.filter((p) => !state.delivered.includes(p));\n    return Screen({ title: \"Delivery round\" },\n      state.news && Alert(state.news),\n      Progress(\"Done\", state.delivered.length / state.parcels.length),\n      List(left, { onSelect: (item, index, ctx) => ctx.set({ open: item.label, news: \"\" }) }));\n  },\n\n  // Something from outside: the depot adds a parcel to the round.\n  onEvent(event, ctx) {\n    if (event.type !== \"parcel\") return;\n    ctx.set((state) => ({ parcels: [...state.parcels, event.address], news: `New parcel: ${event.address}` }));\n  },\n\n  // Words that were not a command. \"skip\" puts the open parcel at the end of the round.\n  onVoice(words, ctx) {\n    const state = ctx.state;\n    if (/^skip\\b/i.test(words) && state.open) {\n      ctx.set({ parcels: [...state.parcels.filter((p) => p !== state.open), state.open], open: null });\n    } else ctx.say(\"Say next, select, yes, no or skip.\");\n  },\n\n  onBack: (ctx) => ctx.set({ open: null }),\n});\n",
  "events": "{ \"type\": \"parcel\", \"address\": \"9 Quay Street\" }"
 },
 {
  "id": "visit",
  "name": "Example: a technician's site visit",
  "code": "// Site visit: a small example app for the Buddy XR guide. A technician works through a job with both hands busy.\n// It shows the blocks (a list to tick off, progress, an alert from outside, a question) and the privacy rule:\n// the customer's door code is personal, so it is shown in the lens and not read out.\n//\n\nimport { Alert, Ask, List, Progress, Screen, Text, defineApp } from \"buddy-xr\";\n\nexport const JOB = { place: \"Unit 4B\", work: \"Boiler service\", code: \"4471\",\n  steps: [\"Isolate the power\", \"Check the pressure\", \"Replace the filter\", \"Photograph the gauge\"] };\n\nexport function createVisit(job = JOB) {\n  const start = { done: [], notes: [], news: \"\", closed: false };\n  return defineApp({\n    name: \"Site visit\",\n    state: start,\n\n    view(s) {\n      if (s.closed) return Screen({ id: \"closed\", title: \"Job closed\" },\n        Text(`${job.place} · ${job.work}. ${s.done.length} steps done${s.notes.length ? `, ${s.notes.length} ${s.notes.length === 1 ? \"note\" : \"notes\"}` : \"\"}.`, { say: true }),\n        List([{ label: \"Next job\" }], { onSelect: (_i, _n, ctx) => ctx.set(start) }));\n\n      const left = job.steps.filter((step) => !s.done.includes(step));\n      return Screen({ id: \"job\", title: `${job.place} · ${job.work}` },\n        s.news && Alert(s.news),\n        s.news && Ask(\"Got it?\", { yesLabel: \"Got it\", noLabel: \"Later\", yes: (ctx) => ctx.set({ news: \"\" }), no: (ctx) => ctx.set({ news: \"\" }) }),\n        Text(`Door code ${job.code}`, { privacy: \"personal\" }),\n        Progress(\"Done\", s.done.length / job.steps.length),\n        left.length ? List(left, { onSelect: (item, _i, ctx) => ctx.set({ done: [...s.done, item.label] }) }) : null,\n        ...s.notes.map((n) => Text(`Note: ${n}`)),\n        !left.length && !s.news && Ask(\"Close the job?\", { yesLabel: \"Close\", noLabel: \"Not yet\", yes: (ctx) => ctx.set({ closed: true }), no: (ctx) => ctx.set({ done: s.done.slice(0, -1) }) }));\n    },\n\n    // \"note the valve is worn\" keeps a note; anything else is not for this app\n    onVoice(words, ctx) {\n      const note = words.match(/^note[:,]?\\s+(.{2,120})$/i);\n      if (note) ctx.set((s) => ({ notes: [...s.notes, note[1].replace(/[.!]$/, \"\")] }));\n      else ctx.say(\"Say next, select, or note and what to write down.\");\n    },\n\n    onEvent(event, ctx) { if (event && event.text) ctx.set({ news: String(event.text).slice(0, 120) }); },\n    onBack(ctx) { ctx.set((s) => ({ done: s.done.slice(0, -1) })); },\n  });\n}\n\nexport default createVisit();\n",
  "events": "{ \"text\": \"The part has arrived at the van.\" }"
 }
];
