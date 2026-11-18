// What a pair of glasses can do. An app never asks "which brand is this?": it describes what to tell the wearer,
// and the framework uses the profile to decide how.
//
//   display   null, or { kind: "text" | "color", lines, cols, images, proportional }
//             proportional: the font is not fixed-width, so nothing is lined up with spaces
//   speaker   can it play speech to the wearer
//   mic       can it hear the wearer (voice commands need a host that turns speech into text)
//   camera    can it take a photo
//   input     physical input the host reports: "tap", "double", "long", "swipe"
//   route     how content reaches the glasses: "direct" (this phone to the glasses, nothing in between),
//             "maker-app" (through the maker's own app) or "cloud" (through a third party's servers).
//             The privacy rules use it.
//
// CLASSES are the three shapes of glasses on sale. FAMILIES are real product families: each is a class plus what
// the open-source device layer (xg.glass, Apache-2.0) declares for its adapter. None has been run on hardware
// from this framework yet (tested: false), and display sizes other than the one marked are defaults, not measured.

export const CLASSES = {
  audio: { id: "audio", name: "Audio glasses", about: "Speaker, microphone and camera. No display.",
    display: null, speaker: true, mic: true, camera: true, input: ["tap"], route: "maker-app" },
  text: { id: "text", name: "Text-display glasses", about: "A few lines of one-colour text in the lens.",
    display: { kind: "text", lines: 5, cols: 30, images: false }, speaker: false, mic: true, camera: false,
    input: ["tap", "double", "long"], route: "direct" },
  color: { id: "color", name: "Colour-display glasses", about: "A colour display with images, plus speaker and camera.",
    display: { kind: "color", lines: 8, cols: 38, images: true }, speaker: true, mic: true, camera: true,
    input: ["tap", "double", "long", "swipe"], route: "maker-app" },
};

const family = (id, name, base, changes, note) => ({ ...CLASSES[base], ...changes, id, name, class: base, tested: false,
  about: note, source: "capabilities as declared by the xg.glass adapter; not yet run on hardware from Buddy XR" });

export const FAMILIES = {
  meta: family("meta", "Meta AI glasses (no display)", "audio", { input: [] },
    "Camera, microphone and speaker through the maker's phone toolkit. No display, no tap events."),
  even: family("even", "Even Realities G1", "text", { display: { kind: "text", lines: 5, cols: 30, images: false, linesMeasured: true } },
    "Text display (five lines a page), microphone, tap and long press. No camera, no speaker."),
  "even-g2": family("even-g2", "Even Realities G2 (through the maker's plugin system)", "text",
    { display: { kind: "text", lines: 7, cols: 40, images: false, proportional: true }, input: ["tap", "double", "long", "swipe"], route: "maker-app" },
    "A web app inside the maker's phone app. Text display, microphone, tap, double tap, long press and scroll. No speaker. The font is not fixed-width."),
  frame: family("frame", "Brilliant Labs Frame", "text", { camera: true, input: ["tap"] },
    "Text display, camera, microphone and tap. No speaker."),
  // Halo's facts come from the maker's own developer kit and emulator, not from the device layer the others use.
  halo: { ...family("halo", "Brilliant Labs Halo", "color",
    { display: { kind: "color", lines: 10, cols: 26, images: false, round: true }, input: ["tap", "double", "long"], route: "direct" },
    "A round colour display of 256 by 256 points, a button, tap, two microphones, bone-conduction speakers and a small camera. Reached from the phone over Bluetooth."),
    source: "the maker's developer kit (brilliant_sdk, BSD-3-Clause) and its Halo emulator; not yet run on hardware from Buddy XR" },
  rokid: family("rokid", "Rokid Glasses", "text", { speaker: true, camera: true, input: [], route: "maker-app" },
    "Text display, camera, microphone and speech. No tap events."),
  rayneo: family("rayneo", "RayNeo (on-glasses runtime)", "color", { input: [], route: "direct" },
    "Text and image display, camera, microphone and audio. The app runs on the glasses."),
  inmo: family("inmo", "INMO Air3 (on-glasses runtime)", "color", { input: ["tap", "long"], route: "direct" },
    "Text and image display, camera, microphone, audio, tap and long press. The app runs on the glasses."),
  "android-xr": family("android-xr", "Android XR display glasses (preview)", "color", { input: [] },
    "Assumed from the preview adapter: text display, camera, microphone and audio."),
  omi: family("omi", "Omi Glass", "audio", { speaker: false, camera: false, input: [] },
    "Microphone only: it can hear the wearer but cannot show or say anything."),
};

export const PROFILES = { ...CLASSES, ...FAMILIES };

export function profile(id) {
  const found = PROFILES[id];
  if (!found) throw new Error(`Unknown glasses profile "${id}". Known: ${Object.keys(PROFILES).join(", ")}`);
  return found;
}

/** Can the wearer be told anything at all, and can they answer? */
export const canOutput = (p) => Boolean(p.display || p.speaker);
export const canChoose = (p) => p.mic || p.input.includes("tap") || p.input.includes("double");
