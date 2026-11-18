// For AI agents. An agent does not write an app: it calls four tools, and the framework puts the result on
// whatever glasses the wearer has, under the same privacy rules as any app.
//
//   glasses_describe   what these glasses can do (so the agent can keep to what will fit)
//   glasses_show       a title, some lines, and optionally an alert
//   glasses_ask        a yes-or-no question; the result is the wearer's answer
//   glasses_choose     a short list; the result is the one the wearer picked
//
// AGENT_TOOLS is in the shape tool-calling models and the Model Context Protocol use (name, description,
// inputSchema), so it can be handed to either. agentSession(host) runs them:
//
//   const session = agentSession(host, { onSay: (words) => sendToAgent(words) });
//   const result = await session.call("glasses_ask", { question: "Navigate there?" });   // { answer: "yes" }
//
// Every result says what actually reached the wearer ({ shown, spoken, withheld }), so an agent is not left
// believing the wearer saw something that was held back.

import { Alert, Ask, List, Screen, Text, defineApp } from "./widgets.js";
import { LEVELS } from "./privacy.js";
import { runApp } from "./runtime.js";

const privacy = { type: "string", enum: LEVELS, description: "How private this is. personal: shown, not spoken aloud. health: shown only on glasses reached directly from the phone, never spoken. Default public." };
const text = (description, max) => ({ type: "string", maxLength: max, description });

export const AGENT_TOOLS = [
  { name: "glasses_describe", description: "What the wearer's glasses can do: display size or none, speaker, microphone, camera, and how they give input.",
    inputSchema: { type: "object", properties: {}, additionalProperties: false } },
  { name: "glasses_show", description: "Show something to the wearer. On glasses with no display it is read to them instead.",
    inputSchema: { type: "object", required: ["title"], additionalProperties: false, properties: {
      title: text("A few words naming what this is.", 60), lines: { type: "array", maxItems: 12, items: text("One short sentence or fact.", 200) },
      alert: text("Something the wearer must not miss. Always spoken where there is a speaker.", 200), privacy } } },
  { name: "glasses_ask", description: "Ask the wearer a yes-or-no question and wait for the answer.",
    inputSchema: { type: "object", required: ["question"], additionalProperties: false, properties: {
      question: text("The question, answerable with yes or no.", 200), title: text("A few words naming what this is about.", 60), privacy } } },
  { name: "glasses_choose", description: "Offer the wearer a short list and wait for their choice.",
    inputSchema: { type: "object", required: ["title", "options"], additionalProperties: false, properties: {
      title: text("A few words naming the choice.", 60), options: { type: "array", minItems: 2, maxItems: 8, items: text("One option.", 80) }, privacy } } },
];

const clean = (v, max) => String(v ?? "").replace(/\s+/g, " ").trim().slice(0, max);
function level(v) { if (v == null) return "public"; if (!LEVELS.includes(v)) throw new Error(`privacy must be one of: ${LEVELS.join(", ")}`); return v; }

export function agentSession(host, options = {}) {
  let waiting = null;                                  // the question or choice the agent is waiting on
  const settle = (value) => { const w = waiting; waiting = null; if (w) w(value); };
  const app = defineApp({
    name: options.name || "Agent",
    state: { n: 0, title: options.name || "Agent", lines: [], alert: "", question: "", options: [], privacy: "public" },
    view: (s) => Screen({ id: "agent-" + s.n, title: s.title, privacy: s.privacy },
      s.alert && Alert(s.alert),
      ...s.lines.map((l) => Text(l)),
      s.question && Ask(s.question, { yes: () => settle({ answer: "yes" }), no: () => settle({ answer: "no" }) }),
      s.options.length && List(s.options, { onSelect: (item, index) => settle({ choice: item.label, index }) })),
    onVoice: (words) => options.onSay && options.onSay(words),
    onBack: () => settle({ cancelled: true }),
  });
  const run = runApp(app, host, options);
  const p = run.profile;
  const reached = () => ({ shown: Boolean(p.display), spoken: Boolean(p.speaker), withheld: run.withheld.display + run.withheld.speech });
  const put = (patch) => run.set((s) => ({ lines: [], alert: "", question: "", options: [], ...patch, n: s.n + 1 }));

  async function call(name, args = {}) {
    if (name === "glasses_describe") return { name: p.name, display: p.display ? { lines: p.display.lines, characters: p.display.cols, pictures: p.display.images } : null,
      speaker: p.speaker, microphone: p.mic, camera: p.camera, input: p.input };
    if (!AGENT_TOOLS.some((t) => t.name === name)) throw new Error(`Unknown tool "${name}".`);
    if (waiting) settle({ cancelled: true });           // a new screen replaces a question nobody answered
    const base = { title: clean(args.title, 60) || app.name, privacy: level(args.privacy) };
    if (name === "glasses_show") {
      put({ ...base, lines: (args.lines || []).slice(0, 12).map((l) => clean(l, 200)).filter(Boolean), alert: clean(args.alert, 200) });
      return reached();
    }
    if (name === "glasses_ask") {
      if (!clean(args.question, 200)) throw new Error("glasses_ask needs a question.");
      const answer = new Promise((ok) => { waiting = ok; });
      put({ ...base, question: clean(args.question, 200) });
      return { ...(await answer), ...reached() };
    }
    const offered = (args.options || []).slice(0, 8).map((o) => clean(o, 80)).filter(Boolean);
    if (offered.length < 2) throw new Error("glasses_choose needs at least two options.");
    const choice = new Promise((ok) => { waiting = ok; });
    put({ ...base, options: offered });
    return { ...(await choice), ...reached() };
  }
  return { run, call, tools: AGENT_TOOLS, stop() { settle({ cancelled: true }); run.stop(); } };
}
