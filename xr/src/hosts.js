// Hosts: the part that knows the glasses.
//
//   memoryHost   keeps what was shown and said. For tests and for the check tool.
//   bridgeHost   for a native app that holds the real connection (for example an Android app built on the
//                xg.glass device layer, with this framework in a web view). The two sides exchange plain JSON:
//                  to the native side:   { op: "show", frame }   { op: "speak", text }   { op: "capture", id }
//                  from the native side: { op: "input", event }  { op: "captured", id, image | error }
//                The native half is not written yet, so this has not been run against real glasses.
// The simulator's host is in simulator/sim.js.

export function memoryHost(profile) {
  let listener = () => {};
  return { profile, frames: [], spoken: [],
    show(f) { this.frames.push(f); }, speak(t) { this.spoken.push(t); }, on(l) { listener = l; },
    input(event) { listener(event); },
    voice(text) { listener({ type: "voice", text }); },
    get last() { return this.frames[this.frames.length - 1] || null; },
    get text() { const f = this.last; return f ? [f.title, ...f.lines.map((l) => l.text)].join("\n") : ""; } };
}

export function bridgeHost(profile, post) {
  let listener = () => {}, next = 1;
  const waiting = new Map();
  return { profile,
    show(frame) { post({ op: "show", frame }); },
    speak(text) { post({ op: "speak", text }); },
    on(l) { listener = l; },
    capture() { const id = next++; post({ op: "capture", id }); return new Promise((ok, no) => waiting.set(id, { ok, no })); },
    /** Call with each message from the native side. */
    receive(message) {
      if (!message || typeof message !== "object") return;
      if (message.op === "input") listener(message.event);
      if (message.op === "captured" && waiting.has(message.id)) {
        const w = waiting.get(message.id); waiting.delete(message.id);
        if (message.error) w.no(new Error(String(message.error))); else w.ok(message.image);
      }
    } };
}
