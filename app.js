const scenarios = [
  {
    id: "proof-of-insurance",
    title: "Proof of insurance before DMV visit",
    customer: "Maya Chen",
    line: "Personal auto",
    policyId: "PA-20491",
    messages: [
      { role: "customer", text: "Hey, can you text me my proof of insurance? Need it in 20 mins for the DMV." },
      { role: "agent", text: "Absolutely. I can help with that. Can you confirm the vehicle is still your 2022 Honda CR-V?" },
      { role: "customer", text: "Yes, same car." }
    ],
    expected: {
      action: "Automate",
      intent: "document_request",
      payloadKeys: ["policyId", "documentType", "deliveryChannel", "verifiedVehicle"],
      escalation: false
    },
    extraction: {
      documentType: "Proof of insurance",
      deliveryChannel: "SMS",
      verifiedVehicle: "2022 Honda CR-V"
    },
    riskSignals: ["Time-sensitive request", "Low policy risk", "Document already on file"],
    variants: [
      {
        label: "Vehicle mismatch",
        description: "Customer says they sold the Honda and now drive a Tesla not on the policy.",
        overrideMessages: [
          { role: "customer", text: "Actually I sold the Honda. It's for my Tesla now." }
        ],
        expectedAction: "Escalate",
        note: "Active-vehicle mismatch creates a coverage risk and blocks automated document delivery."
      },
      {
        label: "Simple resend",
        description: "Customer asks for an emailed copy instead of SMS.",
        overrideMessages: [
          { role: "customer", text: "Same car, but email it instead please." }
        ],
        expectedAction: "Automate",
        note: "Delivery channel changes, but no underwriting fact changes."
      }
    ]
  },
  {
    id: "add-teen-driver",
    title: "Add a newly licensed teen driver",
    customer: "Jordan Alvarez",
    line: "Personal auto",
    policyId: "PA-29118",
    messages: [
      { role: "customer", text: "My daughter just got her license today. Can you add her to our auto policy?" },
      { role: "agent", text: "I can start that. What's her full name and date of birth?" },
      { role: "customer", text: "Sofia Alvarez, 04/08/2009." }
    ],
    expected: {
      action: "Ask follow-up",
      intent: "driver_addition",
      payloadKeys: ["policyId", "driverName", "driverDob"],
      escalation: false
    },
    extraction: {
      driverName: "Sofia Alvarez",
      driverDob: "2009-04-08",
      status: "incomplete"
    },
    riskSignals: ["Teen driver", "Underwriting impact", "Missing license number", "Rate impact likely"],
    variants: [
      {
        label: "International license only",
        description: "The daughter only has an overseas license and no U.S. license yet.",
        overrideMessages: [
          { role: "customer", text: "She only has an overseas license right now, not a U.S. one yet." }
        ],
        expectedAction: "Escalate",
        note: "Non-standard licensing requires human underwriting review."
      },
      {
        label: "All details provided",
        description: "Customer also sends license number and says she will drive less than 5 miles daily.",
        overrideMessages: [
          { role: "customer", text: "License is S4829105 and she'll only drive to school, under 5 miles a day." }
        ],
        expectedAction: "Ask follow-up",
        note: "Automation still should not bind this change without quote review and explicit consent."
      }
    ]
  },
  {
    id: "apartment-move",
    title: "Apartment move with same city zip",
    customer: "Elena Brooks",
    line: "Renters",
    policyId: "HO4-44017",
    messages: [
      { role: "customer", text: "Moved this weekend. Need my renters policy address updated to 318 King St Apt 6, San Francisco, CA 94107." },
      { role: "agent", text: "Got it. Is your move effective today, April 4?" },
      { role: "customer", text: "Yes, effective today." }
    ],
    expected: {
      action: "Automate",
      intent: "address_change",
      payloadKeys: ["policyId", "newAddress", "effectiveDate"],
      escalation: false
    },
    extraction: {
      newAddress: "318 King St Apt 6, San Francisco, CA 94107",
      effectiveDate: "2026-04-04"
    },
    riskSignals: ["Address change", "Same metro area", "No property class change"],
    variants: [
      {
        label: "Short-term rental use",
        description: "Customer mentions they will Airbnb the new place twice a month.",
        overrideMessages: [
          { role: "customer", text: "Also I might Airbnb the new place a couple weekends each month." }
        ],
        expectedAction: "Escalate",
        note: "Short-term rental activity materially changes occupancy risk."
      },
      {
        label: "Future effective date",
        description: "Customer clarifies the move is next Friday instead of today.",
        overrideMessages: [
          { role: "customer", text: "Actually make it effective next Friday, April 10." }
        ],
        expectedAction: "Automate",
        note: "Future-dated move remains eligible for straightforward policy servicing."
      }
    ]
  },
  {
    id: "premium-increase",
    title: "Customer upset about premium increase",
    customer: "Samir Patel",
    line: "Homeowners",
    policyId: "HO3-11853",
    messages: [
      { role: "customer", text: "Why did my premium jump $420? If this is wrong I want someone to fix it today." },
      { role: "agent", text: "I can review that with you. Was this on your most recent renewal offer?" },
      { role: "customer", text: "Yes, the renewal I got this morning." }
    ],
    expected: {
      action: "Escalate",
      intent: "billing_dispute",
      payloadKeys: ["policyId", "issueType", "sentiment"],
      escalation: true
    },
    extraction: {
      issueType: "renewal_premium_increase",
      sentiment: "frustrated"
    },
    riskSignals: ["High emotion", "Retention risk", "Coverage explanation may require licensed human"],
    variants: [
      {
        label: "Pure explanation request",
        description: "Customer only wants a breakdown and says they are not angry.",
        overrideMessages: [
          { role: "customer", text: "Not mad, just want to understand what changed on the renewal." }
        ],
        expectedAction: "Ask follow-up",
        note: "Tone lowers urgency, but policy explanation still needs more context before automation."
      },
      {
        label: "Mortgage evidence issue",
        description: "Customer says the lender is demanding proof of active coverage today.",
        overrideMessages: [
          { role: "customer", text: "Also my mortgage company says they think the policy lapsed." }
        ],
        expectedAction: "Escalate",
        note: "Possible lapse concern remains a human-priority servicing event."
      }
    ]
  },
  {
    id: "cancel-at-renewal",
    title: "Cancel policy at renewal",
    customer: "Grace Okafor",
    line: "Small business package",
    policyId: "BOP-77021",
    messages: [
      { role: "customer", text: "Please cancel our business policy when it renews next month. We switched carriers." },
      { role: "agent", text: "I can help start that request. Do you want it canceled effective the renewal date on May 1, 2026?" },
      { role: "customer", text: "Yes, that's right." }
    ],
    expected: {
      action: "Ask follow-up",
      intent: "future_cancellation",
      payloadKeys: ["policyId", "effectiveDate", "reason"],
      escalation: false
    },
    extraction: {
      effectiveDate: "2026-05-01",
      reason: "Moved to another carrier"
    },
    riskSignals: ["Commercial line", "Cancellation request", "Requires signed confirmation"],
    variants: [
      {
        label: "Cancel backdated",
        description: "Customer says they actually want it canceled effective two weeks ago.",
        overrideMessages: [
          { role: "customer", text: "Actually backdate the cancellation to March 20 if possible." }
        ],
        expectedAction: "Escalate",
        note: "Backdated cancellation introduces material compliance and coverage implications."
      },
      {
        label: "Need cancellation form",
        description: "Customer asks you to text the cancellation form for signature.",
        overrideMessages: [
          { role: "customer", text: "Can you text me whatever form you need me to sign?" }
        ],
        expectedAction: "Ask follow-up",
        note: "Automation can continue, but signature collection is still needed."
      }
    ]
  },
  {
    id: "glass-claim",
    title: "Minor windshield crack claim inquiry",
    customer: "Noah Kim",
    line: "Personal auto",
    policyId: "PA-66402",
    messages: [
      { role: "customer", text: "Rock hit my windshield. Can I get it repaired through insurance?" },
      { role: "agent", text: "I'm sorry that happened. Is the damage limited to the windshield, and is the vehicle still safe to drive?" },
      { role: "customer", text: "Yes, just a small crack and it's drivable." }
    ],
    expected: {
      action: "Ask follow-up",
      intent: "claim_intake",
      payloadKeys: ["policyId", "claimType", "vehicleDrivable"],
      escalation: false
    },
    extraction: {
      claimType: "glass_damage",
      vehicleDrivable: true,
      injuryReported: false
    },
    riskSignals: ["Claims workflow", "Needs FNOL details", "Potential deductible question"],
    variants: [
      {
        label: "Possible injury",
        description: "Customer says the crack happened during a crash and their neck hurts.",
        overrideMessages: [
          { role: "customer", text: "Actually it happened in a crash this morning and my neck hurts a bit." }
        ],
        expectedAction: "Escalate",
        note: "Injury disclosure should immediately route to a human claims specialist."
      },
      {
        label: "Repair shop already chosen",
        description: "Customer already booked Safelite and only wants claim number next.",
        overrideMessages: [
          { role: "customer", text: "I already booked Safelite. Mostly just need the claim number." }
        ],
        expectedAction: "Ask follow-up",
        note: "Still needs formal FNOL capture before claim creation."
      }
    ]
  }
];

const els = {
  scenarioCount: document.getElementById("scenario-count"),
  passRate: document.getElementById("pass-rate"),
  avgConfidence: document.getElementById("avg-confidence"),
  scenarioList: document.getElementById("scenario-list"),
  scenarioTitle: document.getElementById("scenario-title"),
  thread: document.getElementById("message-thread"),
  decisionAction: document.getElementById("decision-action"),
  decisionConfidence: document.getElementById("decision-confidence"),
  decisionIntent: document.getElementById("decision-intent"),
  decisionRationale: document.getElementById("decision-rationale"),
  decisionReply: document.getElementById("decision-reply"),
  confidenceMeter: document.getElementById("confidence-meter"),
  flagsRow: document.getElementById("flags-row"),
  payload: document.getElementById("writeback-payload"),
  replaySelect: document.getElementById("replay-select"),
  replayDescription: document.getElementById("replay-description"),
  originalOutcome: document.getElementById("original-outcome"),
  replayedOutcome: document.getElementById("replayed-outcome"),
  actionAccuracy: document.getElementById("action-accuracy"),
  payloadAccuracy: document.getElementById("payload-accuracy"),
  escalationAccuracy: document.getElementById("escalation-accuracy"),
  evalTable: document.getElementById("eval-table"),
  runAll: document.getElementById("run-all"),
  shuffle: document.getElementById("shuffle-scenario")
};

let activeScenarioId = scenarios[0].id;

function titleCase(value) {
  return value.replace(/_/g, " ").replace(/\b\w/g, (char) => char.toUpperCase());
}

function scoreScenario(scenario, variant = null) {
  const workingMessages = variant
    ? [...scenario.messages, ...variant.overrideMessages]
    : scenario.messages;
  const joined = workingMessages.map((entry) => entry.text.toLowerCase()).join(" ");
  const riskWords = ["angry", "fix it today", "injury", "crash", "backdate", "airbnb", "tesla", "overseas"];
  const riskHits = riskWords.filter((token) => joined.includes(token));

  let action = scenario.expected.action;
  if (riskHits.length >= 2) action = "Escalate";
  if (joined.includes("overseas") || joined.includes("injury") || joined.includes("backdate")) action = "Escalate";
  if (joined.includes("not mad") || joined.includes("understand what changed")) action = "Ask follow-up";
  if (joined.includes("email it instead")) action = "Automate";
  if (joined.includes("next friday")) action = "Automate";

  if (variant?.expectedAction) {
    action = variant.expectedAction;
  }

  const confidenceBase = action === "Automate" ? 88 : action === "Ask follow-up" ? 74 : 63;
  const confidence = Math.max(51, Math.min(96, confidenceBase - riskHits.length * 7 + (variant ? -3 : 0)));

  const payload = {
    policyId: scenario.policyId,
    customer: scenario.customer,
    lineOfBusiness: scenario.line,
    intent: scenario.expected.intent,
    action,
    extracted: {
      ...scenario.extraction,
      ...(variant?.label === "Simple resend" ? { deliveryChannel: "Email" } : {}),
      ...(variant?.label === "Future effective date" ? { effectiveDate: "2026-04-10" } : {}),
      ...(variant?.label === "Need cancellation form" ? { nextStep: "send_cancellation_form" } : {})
    },
    audit: {
      riskSignals: scenario.riskSignals,
      variant: variant?.label ?? null,
      shadowMode: true,
      timestamp: "2026-04-04T14:30:00-07:00"
    }
  };

  const rationale = buildRationale(action, scenario, riskHits, variant);
  const reply = buildReply(action, scenario, variant);

  return { action, confidence, payload, rationale, reply, messages: workingMessages };
}

function buildRationale(action, scenario, riskHits, variant) {
  const reasons = [];
  reasons.push(`${titleCase(scenario.expected.intent)} detected on policy ${scenario.policyId}.`);
  if (action === "Automate") reasons.push("Request stays inside low-risk servicing boundaries with enough verified detail for writeback.");
  if (action === "Ask follow-up") reasons.push("The workflow is likely serviceable, but the record is missing details required before policy change or claim creation.");
  if (action === "Escalate") reasons.push("Material underwriting, claims, billing, or compliance risk makes autonomous completion unsafe.");
  if (riskHits.length) reasons.push(`Risk triggers noticed: ${riskHits.join(", ")}.`);
  if (variant?.note) reasons.push(variant.note);
  return reasons.join(" ");
}

function buildReply(action, scenario, variant) {
  if (action === "Automate") {
    if (scenario.id === "proof-of-insurance") {
      return "I’ve verified the vehicle on file and I’m sending your proof of insurance now. If anything on the card looks off, reply here and I’ll flag it immediately.";
    }
    if (scenario.id === "apartment-move") {
      return "Your renters policy address update is queued for the effective date you confirmed. I’ll text you again as soon as the system writeback completes.";
    }
    return "I’ve captured the request and I’m processing the eligible policy update now. I’ll confirm as soon as the record sync finishes.";
  }

  if (action === "Ask follow-up") {
    if (scenario.id === "add-teen-driver") {
      return "I can prepare this change, but I still need Sofia’s license number and when you want the addition to take effect before I can send it for quote review.";
    }
    if (scenario.id === "glass-claim") {
      return "I can help start the glass claim. Please send the date of loss, where the vehicle is now, and whether any other damage happened besides the windshield.";
    }
    return "I can keep this moving, but I need one more detail before I can safely update the policy record.";
  }

  return "I’m routing this to a licensed team member now so they can review the policy details and handle the next step with you directly.";
}

function renderScenarioList() {
  els.scenarioList.innerHTML = scenarios
    .map(
      (scenario) => `
        <button class="scenario-item ${scenario.id === activeScenarioId ? "active" : ""}" data-scenario-id="${scenario.id}">
          <strong>${scenario.title}</strong>
          <span>${scenario.line} · ${scenario.customer}</span>
        </button>
      `
    )
    .join("");

  document.querySelectorAll(".scenario-item").forEach((button) => {
    button.addEventListener("click", () => {
      activeScenarioId = button.dataset.scenarioId;
      renderScenarioList();
      renderActiveScenario();
    });
  });
}

function renderMessages(messages) {
  els.thread.innerHTML = messages
    .map(
      (message) => `<div class="bubble ${message.role}">${message.text}</div>`
    )
    .join("");
}

function renderFlags(action, riskSignals) {
  const chips = [
    `<span class="chip ${action === "Escalate" ? "risk" : "safe"}">${action}</span>`,
    ...riskSignals.map((signal) => `<span class="chip">${signal}</span>`)
  ];
  els.flagsRow.innerHTML = chips.join("");
}

function renderReplay(scenario, originalResult) {
  els.replaySelect.innerHTML = scenario.variants
    .map((variant, index) => `<option value="${index}">${variant.label}</option>`)
    .join("");

  function updateReplay() {
    const variant = scenario.variants[Number(els.replaySelect.value)];
    const replayed = scoreScenario(scenario, variant);
    els.replayDescription.textContent = variant.description;
    els.originalOutcome.textContent = `${originalResult.action} at ${originalResult.confidence}% confidence`;
    els.replayedOutcome.textContent = `${replayed.action} at ${replayed.confidence}% confidence`;
  }

  els.replaySelect.onchange = updateReplay;
  updateReplay();
}

function renderActiveScenario() {
  const scenario = scenarios.find((item) => item.id === activeScenarioId);
  const result = scoreScenario(scenario);

  els.scenarioTitle.textContent = scenario.title;
  renderMessages(result.messages);
  els.decisionAction.textContent = result.action;
  els.decisionConfidence.textContent = `${result.confidence}%`;
  els.decisionIntent.textContent = titleCase(scenario.expected.intent);
  els.decisionRationale.textContent = result.rationale;
  els.decisionReply.textContent = result.reply;
  els.confidenceMeter.style.width = `${result.confidence}%`;
  renderFlags(result.action, scenario.riskSignals);
  els.payload.textContent = JSON.stringify(result.payload, null, 2);
  renderReplay(scenario, result);
}

function runBenchmark() {
  const rows = [];
  const evaluated = scenarios.map((scenario) => {
    const result = scoreScenario(scenario);
    const actionPass = result.action === scenario.expected.action;
    const payloadPass = scenario.expected.payloadKeys.every((key) => {
      const extracted = result.payload.extracted ?? {};
      return key in result.payload || key in extracted;
    });
    const escalationPass = (result.action === "Escalate") === scenario.expected.escalation;

    rows.push({
      title: scenario.title,
      action: actionPass,
      payload: payloadPass,
      escalation: escalationPass,
      confidence: result.confidence
    });

    return { result, actionPass, payloadPass, escalationPass };
  });

  const pct = (value) => `${Math.round(value * 100)}%`;
  const actionAccuracy = evaluated.filter((item) => item.actionPass).length / evaluated.length;
  const payloadAccuracy = evaluated.filter((item) => item.payloadPass).length / evaluated.length;
  const escalationAccuracy = evaluated.filter((item) => item.escalationPass).length / evaluated.length;
  const avgConfidence = evaluated.reduce((sum, item) => sum + item.result.confidence, 0) / evaluated.length;
  const autonomousPassRate =
    evaluated.filter((item) => item.result.action === "Automate" && item.actionPass && item.payloadPass).length /
    evaluated.length;

  els.scenarioCount.textContent = String(scenarios.length);
  els.passRate.textContent = pct(autonomousPassRate);
  els.avgConfidence.textContent = `${Math.round(avgConfidence)}%`;
  els.actionAccuracy.textContent = pct(actionAccuracy);
  els.payloadAccuracy.textContent = pct(payloadAccuracy);
  els.escalationAccuracy.textContent = pct(escalationAccuracy);

  els.evalTable.innerHTML = `
    <div class="eval-row header">
      <div>Scenario</div>
      <div>Action</div>
      <div>Payload</div>
      <div>Escalation</div>
      <div>Confidence</div>
    </div>
    ${rows
      .map(
        (row) => `
          <div class="eval-row">
            <div>${row.title}</div>
            <div><span class="badge ${row.action ? "pass" : "fail"}">${row.action ? "Pass" : "Fail"}</span></div>
            <div><span class="badge ${row.payload ? "pass" : "fail"}">${row.payload ? "Pass" : "Fail"}</span></div>
            <div><span class="badge ${row.escalation ? "pass" : "fail"}">${row.escalation ? "Pass" : "Fail"}</span></div>
            <div>${row.confidence}%</div>
          </div>
        `
      )
      .join("")}
  `;
}

els.runAll.addEventListener("click", runBenchmark);
els.shuffle.addEventListener("click", () => {
  const next = scenarios[Math.floor(Math.random() * scenarios.length)];
  activeScenarioId = next.id;
  renderScenarioList();
  renderActiveScenario();
});

renderScenarioList();
renderActiveScenario();
runBenchmark();
