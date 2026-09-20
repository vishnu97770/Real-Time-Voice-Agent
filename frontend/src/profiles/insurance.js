import { listSentence, makeGreeting, rupees } from "./shared.js";

const CLAIM_TYPES = {
  hospital: "hospitalisation",
  hospitalisation: "hospitalisation",
  hospitalization: "hospitalisation",
  surgery: "hospitalisation",
  pharmacy: "pharmacy reimbursement",
  medicine: "pharmacy reimbursement",
  diagnostic: "diagnostics",
  test: "diagnostics",
};

function claimType(text) {
  const word = Object.keys(CLAIM_TYPES).find((key) => text.toLowerCase().includes(key));

  return word ? CLAIM_TYPES[word] : null;
}

export default {
  id: "insurance",
  name: "Insurance Desk",
  vertical: "Insurance",
  workspaceLabel: "Insurance Service Workspace",
  counterparty: "policyholder",

  headline: "How can I help with your policy?",
  description:
    "Speak naturally with the AI voice agent about renewals, claim updates, your coverage or filing a new claim.",

  greeting: makeGreeting(
    "I'm the virtual assistant for Shieldwell Insurance, speaking with Arjun Nair.",
    "I can help with your renewal, an existing claim, your coverage, or filing a new claim. What would you like to do?"
  ),

  prompts: [
    "When is my renewal due?",
    "What's the status of my claim?",
    "What does my policy cover?",
    "File a claim for hospitalisation",
  ],

  capabilities: [
    "check your renewal",
    "update you on a claim",
    "explain your coverage",
    "file a new claim",
  ],

  nextSteps: [
    "Upload the discharge summary for claim CLM-5521",
    "Pay the renewal premium before the due date",
    "Track any newly filed claim in the portal",
  ],

  data: {
    policyholder: "Arjun Nair",
    policy: {
      id: "POL-88213",
      type: "family health",
      sumInsured: 500000,
      premium: 14200,
      renewalDate: "14 October 2026",
      daysToRenewal: 25,
      covers: ["hospitalisation", "day-care procedures", "pre and post hospitalisation costs", "ambulance charges"],
    },
    claims: [
      {
        id: "CLM-5521",
        type: "hospitalisation",
        status: "under assessment",
        amount: 62000,
        pendingDocs: ["discharge summary"],
      },
    ],
  },

  intents: [
    {
      id: "file_claim",
      topic: "New claim",
      match: /\b(file|raise|register|submit|lodge|make)\b.*\bclaim\b|\bnew claim\b/i,
      propose: (data, text) => {
        const type = claimType(text);

        if (!type) {
          return {
            reply:
              "Happy to. What is the claim for: hospitalisation, pharmacy reimbursement, or diagnostics?",
          };
        }

        return { tool: "file_claim", args: { policy_id: data.policy.id, type } };
      },
    },
    {
      id: "claim_status",
      topic: "Claim status",
      tool: "get_claim_status",
      match: /\b(claim|claims)\b/i,
      run: (data) => {
        const claim = data.claims[0];
        const docs = claim.pendingDocs.length
          ? `We're still waiting for the ${listSentence(claim.pendingDocs)}.`
          : "No documents are pending.";

        return {
          args: { claim_id: claim.id },
          result: claim,
          reply: `Claim ${claim.id}, your ${claim.type} claim for ${rupees(claim.amount)}, is ${claim.status}. ${docs}`,
          ref: `Claim: ${claim.id}`,
        };
      },
    },
    {
      id: "renewal",
      topic: "Policy renewal",
      tool: "get_renewal",
      match: /\b(renew|renewal|premium|due|expire|expiry)\b/i,
      run: (data) => {
        const { policy } = data;

        return {
          args: { policy_id: policy.id },
          result: { renewal_date: policy.renewalDate, premium: policy.premium },
          reply: `Your ${policy.type} policy ${policy.id} renews on ${policy.renewalDate}, which is ${policy.daysToRenewal} days away. The renewal premium is ${rupees(policy.premium)}.`,
          ref: `Policy: ${policy.id}`,
        };
      },
    },
    {
      id: "coverage",
      topic: "Coverage",
      tool: "get_policy",
      match: /\b(cover|covers|coverage|covered|policy|sum insured|benefit)\b/i,
      run: (data) => {
        const { policy } = data;

        return {
          args: { policy_id: policy.id },
          result: policy,
          reply: `Your ${policy.type} policy ${policy.id} has a sum insured of ${rupees(policy.sumInsured)}. It covers ${listSentence(policy.covers)}.`,
          ref: `Policy: ${policy.id}`,
        };
      },
    },
  ],

  actions: {
    file_claim: {
      label: "File claim",
      describe: (args) =>
        `file a new ${args.type} claim under policy ${args.policy_id}`,
      execute: (args, data) => {
        const claim = {
          id: `CLM-${5530 + data.claims.length - 1}`,
          type: args.type,
          status: "registered",
          amount: 0,
          pendingDocs: ["claim form", "supporting bills"],
        };

        data.claims.unshift(claim);

        return {
          result: claim,
          summary: `Claim ${claim.id} filed (${claim.type})`,
          reply: `Done. I've filed claim ${claim.id} for ${claim.type}. You'll need to send the ${listSentence(claim.pendingDocs)}, and the claims team will contact you.`,
          ref: `Claim: ${claim.id}`,
        };
      },
    },
  },
};
