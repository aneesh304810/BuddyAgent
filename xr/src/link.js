// The link: joins a pair of glasses (any host) to a Buddy server, so Buddy's brain can show the wearer something,
// ask a yes-or-no question or offer a choice, and is told what actually reached them.
//
// The runtime stays on this side. Fitting to the lens or the ear, and the privacy rule, are applied here before
// anything reaches the glasses. The server never learns which brand it is talking to, only the profile.
//
// Messages (JSON; the contract in docs/BWCP.md, version 0.2):
//   to the server    { op: "hello", protocol: "bwcp", version, device: { maker, model, id }, profile }
//                    { op: "result", id, ok: true, result }  |  { op: "result", id, ok: false, error }
//                    { op: "state", battery?, charging?, link?, worn? }
//                    { op: "said", text }            the wearer's words that were not a command
//                    { op: "problem", code, about }
//                    { op: "bye", why }
//   from the server  { op: "welcome", version, session }  |  { op: "refused", why }
//                    { op: "call", id, tool, args }  one of the four agent tools (agent.js)
//                    { op: "cancel", id }            stop waiting for an answer nobody gave
// A side ignores an op it does not know.
//
//   const link = connectLink(host, new WebSocket(url), { device: { maker: "brilliant", model: "halo", id: name } });
//
// `socket` is anything shaped like a WebSocket (send, close, onopen, onmessage, onclose, readyState), so the link
// can be tested without a network. NOT YET RUN WITH REAL GLASSES.

import { agentSession } from "./agent.js";

export const LINK = { protocol: "bwcp", version: "0.2" };
const CONTRACT = ["display", "speaker", "speakerPrivate", "mic", "camera", "input", "route"];   // what the server may know
const text = (v, max) => String(v ?? "").replace(/\s+/g, " ").trim().slice(0, max);

/** The part of a profile that crosses the link: what the glasses can do, and how content reaches them. */
export function described(profile) {
  const out = { name: text(profile.name, 80) };
  for (const field of CONTRACT) if (profile[field] !== undefined) out[field] = profile[field];
  return out;
}

export function connectLink(host, socket, options = {}) {
  const name = options.name || "Buddy";
  const device = options.device || {};
  const tell = (what, detail) => { if (options.onState) options.onState(what, detail); };
  const waiting = new Set();                             // questions the server is still waiting on
  let open = false, welcomed = false, closed = false;

  const send = (message) => { if (open && !closed) socket.send(JSON.stringify(message)); };
  const session = agentSession(host, { name, speakPersonal: options.speakPersonal, allowCloud: options.allowCloud,
    onError: (e) => send({ op: "problem", code: "app-failed", about: text(e && e.message, 200) }),
    onSay: (words) => send({ op: "said", text: text(words, 500) }) });

  function hello() {
    if (open || closed) return;
    open = true;
    send({ op: "hello", ...LINK, device: { maker: text(device.maker, 40), model: text(device.model, 60), id: text(device.id, 80) },
      profile: described(host.profile) });
  }

  async function call(m) {
    const asks = m.tool === "glasses_ask" || m.tool === "glasses_choose";
    if (asks) waiting.add(m.id);
    try { send({ op: "result", id: m.id, ok: true, result: await session.call(m.tool, m.args || {}) }); }
    catch (e) { send({ op: "result", id: m.id, ok: false, error: text(e && e.message, 200) }); }
    finally { waiting.delete(m.id); }
  }

  function heard(raw) {
    let m;
    try { m = typeof raw === "string" ? JSON.parse(raw) : raw; } catch { return; }
    if (!m || typeof m !== "object" || closed) return;
    if (m.op === "welcome") { welcomed = true; tell("connected", m); }
    else if (m.op === "refused") { tell("refused", text(m.why, 200)); shut(); }
    else if (m.op === "call" && m.id != null && typeof m.tool === "string") call(m);
    else if (m.op === "cancel" && waiting.has(m.id)) session.call("glasses_show", { title: name }).catch(() => {});   // clears the question
  }

  function shut() {
    if (closed) return;
    closed = true;
    session.stop();
    try { socket.close(); } catch { /* already gone */ }
  }

  socket.onopen = hello;
  socket.onmessage = (event) => heard(event && event.data !== undefined ? event.data : event);
  socket.onclose = () => { const was = !closed; closed = true; session.stop(); if (was) tell("closed"); };
  if (socket.readyState === 1) hello();

  return {
    session,
    get connected() { return welcomed && !closed; },
    /** Battery, charge, connection, whether they are worn: whatever the glasses report. */
    state(patch) { send({ op: "state", ...patch }); },
    problem(code, about) { send({ op: "problem", code: text(code, 40), about: text(about, 200) }); },
    close(why = "off") { send({ op: "bye", why: text(why, 40) }); shut(); tell("closed"); },
  };
}
