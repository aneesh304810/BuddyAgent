// A host on top of MentraOS, an open-source runtime for smart glasses (MIT). A Buddy XR app or agent session
// given this host reaches whatever glasses MentraOS supports, without a host for each maker.
//
// NOT YET RUN ON GLASSES. Written against the published SDK (@mentra/sdk 2.1.29) and tested here against a
// stand-in for its session.
//
// How MentraOS works, and what follows from it:
//   - An app is a server. MentraOS's cloud calls it when the wearer opens the app and hands it a `session`.
//     Everything shown, spoken and heard passes through that cloud, so the profile's route is "cloud": personal
//     content is held back unless the deployer accepts that route (options.allowCloud), and health content is
//     never sent (see privacy.js).
//   - The session reports what the wearer's glasses can do. The profile is built from that report, so display
//     lines, speaker, microphone and camera are the device's own figures, not estimates. Characters a line is
//     the exception: it is not reported, the default is a guess, and the font may not be fixed-width.
//   - The wearer's words arrive already as text (MentraOS transcribes them), so voice input works.
//   - Glasses with one button have no "choose" of their own: a short press is next and a long press chooses.
//     Back is by voice. Swipes are next and previous.
//
// Use, inside a MentraOS AppServer:
//   protected async onSession(session) { runApp(app, mentraHost(session)); }
// A complete server is in examples/mentra-server.js.

const GESTURES = { forward_swipe: "swipe-forward", backward_swipe: "swipe-back" };
const DEFAULT_COLS = 36;

/** A Buddy XR profile from what a MentraOS session says the glasses can do. */
export function profileFromCapabilities(caps, { cols = DEFAULT_COLS } = {}) {
  const c = caps || {};
  const d = c.hasDisplay && c.display ? c.display : null;
  const buttons = c.hasButton && c.button && c.button.buttons || [];
  const input = [];
  if (buttons.some((b) => b.type === "press")) input.push("tap", "double");     // short press: next. long press: choose
  if (buttons.some((b) => b.type === "swipe1d" || b.type === "swipe2d")) input.push("swipe");
  return { id: "mentra", name: c.modelName ? `${c.modelName} (through MentraOS)` : "Glasses through MentraOS",
    about: "Reached through MentraOS's cloud. Capabilities are what the session reports for the wearer's glasses.",
    display: d ? { kind: d.isColor ? "color" : "text", lines: Math.max(2, d.maxTextLines || 5), cols, images: false, proportional: true } : null,
    speaker: Boolean(c.hasSpeaker), speakerPrivate: Boolean(c.speaker && c.speaker.isPrivate),
    mic: Boolean(c.hasMicrophone), camera: Boolean(c.hasCamera), input, route: "cloud", tested: false,
    source: "capabilities reported by the MentraOS session; characters a line is a default" };
}

export function mentraHost(session, { profile, cols, gestures = {}, onProblem = () => {} } = {}) {
  const p = profile || profileFromCapabilities(session.capabilities, { cols });
  const map = { ...GESTURES, ...gestures };
  let listener = () => {}, last = "";
  const guard = (fn) => { try { const r = fn(); if (r && typeof r.catch === "function") r.catch(onProblem); } catch (e) { onProblem(e); } };

  const stops = [
    p.mic && session.events.onTranscription((t) => { if (t && t.isFinal && String(t.text || "").trim()) listener({ type: "voice", text: String(t.text).trim() }); }),
    session.events.onButtonPress((b) => { if (b) listener({ type: b.pressType === "long" ? "double" : "tap" }); }),
    session.events.onTouchEvent((t) => { const type = t && map[t.gesture_name]; if (type) listener({ type }); }),
  ].filter((s) => typeof s === "function");

  return { profile: p,
    show(frame) {
      const text = [frame.title, ...frame.lines.map((l) => l.text)].join("\n");
      if (text === last) return;
      last = text;
      guard(() => session.layouts.showTextWall(text));
    },
    speak(text) { if (p.speaker) guard(() => session.audio.speak(text)); },
    on(l) { listener = l; },
    capture() { return p.camera ? session.camera.requestPhoto() : Promise.reject(new Error("These glasses have no camera.")); },
    /** Stop listening to the session (call when MentraOS ends it). */
    close() { stops.forEach((stop) => guard(stop)); listener = () => {}; },
  };
}
