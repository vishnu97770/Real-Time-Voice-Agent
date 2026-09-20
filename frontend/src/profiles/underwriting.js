import { findRef, listSentence, makeGreeting, rupees } from "./shared.js";

function resolveApplication(data, text) {
  const id = findRef(text, "APP") ?? data.currentId;

  return { id, application: data.applications[id] };
}

const notFound = (id) => ({
  args: { application_id: id },
  result: { found: false },
  reply: `I couldn't find an application ${id} in the system, so I won't guess. Could you check the number?`,
});

export default {
  id: "underwriting",
  name: "Credit Underwriting",
  vertical: "Credit",
  workspaceLabel: "Credit Underwriting Workspace",
  counterparty: "underwriter",

  headline: "How can I help you today?",
  description:
    "Speak naturally with the AI voice agent about any application, financials, risks, or review.",

  greeting: makeGreeting(
    "I'm your credit underwriting copilot.",
    "I'm looking at application APP-1024. Ask me about it, the pending queue or its risk, or ask me to arrange a follow-up call with the applicant."
  ),

  prompts: [
    "Show pending applications",
    "Summarize this applicant",
    "What are the key risks?",
    "Which documents are missing?",
    "Call the applicant",
    "Show me today's activities",
  ],

  capabilities: [
    "see pending applications",
    "summarize an applicant",
    "explain risk scores",
    "check missing documents",
    "arrange follow-up calls",
  ],

  nextSteps: [
    "Verify outstanding documents",
    "Continue underwriting review",
    "Confirm the follow-up call took place",
  ],

  data: {
    currentId: "APP-1024",
    applications: {
      "APP-1024": {
        applicant: "Ravi Menon",
        product: "business loan",
        amount: 1800000,
        status: "under financial review",
        riskScore: 0.28,
        riskBand: "moderate",
        missingDocs: ["latest bank statement"],
        riskFactors: [
          "a debt-to-income ratio of 41 percent",
          "recent revenue variation",
          "the pending bank statement",
        ],
        pending: true,
      },
      "APP-1031": {
        applicant: "Sneha Kulkarni",
        product: "home loan",
        amount: 4200000,
        status: "awaiting documents",
        riskScore: 0.41,
        riskBand: "elevated",
        missingDocs: ["PAN verification", "income proof"],
        riskFactors: ["a short employment history", "two missed card payments last year"],
        pending: true,
      },
      "APP-1040": {
        applicant: "Imran Sheikh",
        product: "vehicle loan",
        amount: 950000,
        status: "ready for a decision",
        riskScore: 0.19,
        riskBand: "low",
        missingDocs: [],
        riskFactors: ["nothing significant"],
        pending: false,
      },
    },
    activities: [
      "09:10, income proof verified on APP-1040",
      "11:25, document request sent on APP-1031",
      "13:40, risk score recalculated on APP-1024",
    ],
    callJobs: [],
  },

  intents: [
    {
      id: "follow_up_call",
      topic: "Follow-up call",
      match: /\b(call|phone|ring|schedule|follow[- ]?up)\b/i,
      propose: (data, text) => {
        const { id, application } = resolveApplication(data, text);

        if (!application) return notFound(id);

        return { tool: "schedule_followup_call", args: { application_id: id } };
      },
    },
    {
      id: "pending",
      topic: "Pending applications",
      tool: "list_pending_applications",
      match: /\b(pending|queue|waiting|outstanding applications)\b/i,
      run: (data) => {
        const pending = Object.entries(data.applications).filter(([, app]) => app.pending);
        const parts = pending.map(
          ([id, app]) => `${id} for ${app.applicant}, ${app.status}`
        );

        return {
          args: {},
          result: pending.map(([id, app]) => ({ id, applicant: app.applicant, status: app.status })),
          reply: `There are ${pending.length} applications pending: ${listSentence(parts)}.`,
        };
      },
    },
    {
      id: "risk",
      topic: "Risk assessment",
      tool: "get_risk_factors",
      match: /\b(risk|risks|score|risky)\b/i,
      run: (data, text) => {
        const { id, application } = resolveApplication(data, text);

        if (!application) return notFound(id);

        return {
          args: { application_id: id },
          result: {
            risk_score: application.riskScore,
            band: application.riskBand,
            factors: application.riskFactors,
          },
          reply: `${id} has a risk score of ${application.riskScore}, which is ${application.riskBand} risk. The main factors are ${listSentence(application.riskFactors)}.`,
          ref: `Application: ${id}`,
        };
      },
    },
    {
      id: "documents",
      topic: "Document checklist",
      tool: "get_missing_documents",
      match: /\b(document|documents|missing|statement|checklist)\b/i,
      run: (data, text) => {
        const { id, application } = resolveApplication(data, text);

        if (!application) return notFound(id);

        const missing = application.missingDocs;

        return {
          args: { application_id: id },
          result: { missing },
          reply: missing.length
            ? `For ${id}, still outstanding: ${listSentence(missing)}.`
            : `${id} has all required documents on file.`,
          ref: "Document checklist",
        };
      },
    },
    {
      id: "activities",
      topic: "Today's activity",
      tool: "get_todays_activity",
      match: /\b(today|activity|activities|happened)\b/i,
      run: (data) => ({
        args: {},
        result: data.activities,
        reply: `Today so far: ${data.activities.join("; ")}.`,
      }),
    },
    {
      id: "summary",
      topic: "Application summary",
      tool: "get_application",
      match: /\b(summari[sz]e|summary|applicant|status|application|APP-?\d+|tell me about)\b/i,
      run: (data, text) => {
        const { id, application } = resolveApplication(data, text);

        if (!application) return notFound(id);

        const docs = application.missingDocs.length
          ? `Still outstanding: ${listSentence(application.missingDocs)}.`
          : "All required documents are in.";

        return {
          args: { application_id: id },
          result: application,
          reply: `${id} is a ${rupees(application.amount)} ${application.product} for ${application.applicant}. It is currently ${application.status}. ${docs} The risk score is ${application.riskScore}, ${application.riskBand} risk.`,
          ref: `Application: ${id}`,
        };
      },
    },
  ],

  actions: {
    schedule_followup_call: {
      label: "Schedule follow-up call",
      describe: (args, data) =>
        `create an outbound follow-up call job to ${data.applications[args.application_id].applicant} about ${args.application_id}`,
      execute: (args, data) => {
        const application = data.applications[args.application_id];
        const job = {
          job_id: `JOB-${3001 + data.callJobs.length}`,
          applicant: application.applicant,
          application_id: args.application_id,
          reason: application.missingDocs.length
            ? `Collect ${listSentence(application.missingDocs)}`
            : "Status follow-up",
          profile: "underwriting",
        };

        data.callJobs.push(job);

        return {
          result: job,
          summary: `Outbound call job ${job.job_id} created for ${application.applicant}`,
          reply: `Done. I've created outbound call job ${job.job_id} to ${application.applicant} regarding ${args.application_id}. The call orchestration service will place the call.`,
          ref: `Call job: ${job.job_id}`,
        };
      },
    },
  },
};
