// Starting points for the no-code builder, one for each kind of work. They are examples to change, written from
// general knowledge of the work, not from any company's procedure: the person using them knows their job better.

export const INDUSTRIES = [
  { id: "warehouse", industry: "Warehouse", spec: { name: "Order picking",
    steps: [
      { kind: "info", text: "Order 4471. 3 lines. Trolley 12." },
      { kind: "do", text: "Aisle 7, shelf C. Pick 2 boxes of 10 mm bolts." },
      { kind: "do", text: "Aisle 9, shelf A. Pick 1 cordless drill." },
      { kind: "ask", text: "Is the packaging undamaged?", ifNo: "Set the item aside and pick another." },
      { kind: "choose", text: "Where is the order going?", options: ["Packing bench 1", "Packing bench 2", "Hold area"] },
      { kind: "note", text: "Anything short or missing?" },
    ], finish: "Order picked." } },

  { id: "construction", industry: "Construction", spec: { name: "Site safety walk",
    steps: [
      { kind: "info", text: "Start at gate 2. Hard hats and boots beyond this point." },
      { kind: "do", text: "Check the scaffold tags are in date." },
      { kind: "ask", text: "Are the edge barriers in place on every open floor?", ifNo: "Stop work near the edge and tell the supervisor." },
      { kind: "ask", text: "Are the walkways clear?", ifNo: "Have the walkway cleared before work goes on." },
      { kind: "choose", text: "Weather", options: ["Dry", "Wet", "Windy", "Icy"] },
      { kind: "note", text: "Anything else to report?" },
    ], finish: "Walk complete." } },

  { id: "manufacturing", industry: "Manufacturing", spec: { name: "Line changeover",
    steps: [
      { kind: "do", text: "Stop the line and lock it off." },
      { kind: "ask", text: "Has the machine come to a full stop?", ifNo: "Wait. Do not open the guard." },
      { kind: "do", text: "Remove the old tooling and put it on the rack." },
      { kind: "do", text: "Fit the new tooling and tighten to the marked setting." },
      { kind: "choose", text: "Which product is next?", options: ["Product A", "Product B", "Product C"] },
      { kind: "ask", text: "Did the first piece pass inspection?", ifNo: "Do not restart. Call the line lead." },
    ], finish: "Changeover complete." } },

  { id: "fieldservice", industry: "Field service", spec: { name: "Boiler service",
    steps: [
      { kind: "info", text: "Unit 4B. Door code 4471.", privacy: "personal" },
      { kind: "do", text: "Isolate the power and the gas." },
      { kind: "do", text: "Check the pressure and write it down." },
      { kind: "do", text: "Clean or replace the filter." },
      { kind: "ask", text: "Any sign of a leak?", ifNo: "" },
      { kind: "note", text: "Parts used, or anything the customer should know?" },
    ], finish: "Service complete." } },

  { id: "hotel", industry: "Hotel", spec: { name: "Room turnover",
    steps: [
      { kind: "do", text: "Strip the bed and make it with fresh linen." },
      { kind: "do", text: "Clean the bathroom and replace the towels." },
      { kind: "do", text: "Restock the minibar and the tea tray." },
      { kind: "ask", text: "Is anything broken or missing?", ifNo: "" },
      { kind: "choose", text: "Room status", options: ["Ready", "Needs maintenance", "Needs a second clean"] },
      { kind: "note", text: "Anything left behind by the guest?" },
    ], finish: "Room finished." } },

  { id: "retail", industry: "Retail", spec: { name: "Store opening",
    steps: [
      { kind: "do", text: "Switch off the alarm and switch on the lights." },
      { kind: "do", text: "Count the float into the till." },
      { kind: "ask", text: "Are the fire exits clear?", ifNo: "Clear them before opening." },
      { kind: "do", text: "Check the window display and the entrance are clean." },
      { kind: "choose", text: "Deliveries expected today", options: ["None", "Morning", "Afternoon"] },
    ], finish: "Ready to open." } },

  { id: "homecare", industry: "Home care", spec: { name: "Home visit",
    steps: [
      { kind: "info", text: "Mrs Okafor, 14 Rowan Close. Key safe 2580.", privacy: "personal" },
      { kind: "ask", text: "Has she eaten today?", privacy: "health", ifNo: "Offer a meal and write it in the visit record." },
      { kind: "do", text: "Give the morning tablets from the dated box.", privacy: "health" },
      { kind: "do", text: "Check the heating is on and the home is safe to move around." },
      { kind: "note", text: "Anything the next carer should know?", privacy: "health" },
    ], finish: "Visit complete." } },
];

export const EMPTY = { name: "My app", steps: [{ kind: "do", text: "The first thing to do" }], finish: "" };
