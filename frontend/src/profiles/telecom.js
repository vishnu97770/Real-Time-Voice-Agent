import { listSentence, makeGreeting, rupees } from "./shared.js";

const PLANS = {
  "Postpaid 599": { price: 599, data: "60 GB", extras: "unlimited calls" },
  "Postpaid 799": { price: 799, data: "120 GB", extras: "unlimited calls and a streaming bundle" },
  "Postpaid 999": { price: 999, data: "250 GB", extras: "unlimited calls, a streaming bundle and international roaming" },
};

function requestedPlan(text) {
  const match = text.match(/\b(799|999)\b/);

  return match ? `Postpaid ${match[1]}` : null;
}

export default {
  id: "telecom",
  name: "Telecom Support",
  vertical: "Telecom",
  workspaceLabel: "Telecom Support Workspace",
  counterparty: "subscriber",

  headline: "How can I help with your plan?",
  description:
    "Speak naturally with the AI voice agent about your usage, bill, plan renewal or an upgrade.",

  greeting: makeGreeting(
    "I'm the virtual assistant for Vertex Mobile, speaking with Meera Iyer.",
    "I can help with your data usage, your bill, plan renewal, or an upgrade. What can I do for you?"
  ),

  prompts: [
    "How much data have I used?",
    "What's my current bill?",
    "When does my plan renew?",
    "What plans are available?",
    "Upgrade my plan",
  ],

  capabilities: [
    "check your data usage",
    "explain your bill",
    "tell you when your plan renews",
    "upgrade your plan",
  ],

  nextSteps: [
    "Pay the outstanding bill before the due date",
    "Check the new plan benefits in the app",
    "Review usage again before the next renewal",
  ],

  data: {
    subscriber: "Meera Iyer",
    number: "ending 0210",
    plan: "Postpaid 599",
    usage: { usedGb: 42, limitGb: 60, resetsOn: "28 September 2026" },
    bill: { amount: 599, dueDate: "25 September 2026", status: "unpaid" },
    plans: PLANS,
  },

  intents: [
    {
      id: "upgrade",
      topic: "Plan upgrade",
      match: /\b(upgrade|switch|change (?:my )?plan|move to)\b/i,
      propose: (data, text) => {
        const plan = requestedPlan(text) ?? "Postpaid 799";

        if (plan === data.plan) {
          return { reply: `You're already on ${plan}.` };
        }

        return { tool: "upgrade_plan", args: { plan } };
      },
    },
    {
      id: "plans",
      topic: "Available plans",
      tool: "list_plans",
      match: /\b(plans|options|available|offers)\b/i,
      run: (data) => {
        const parts = Object.entries(data.plans).map(
          ([name, plan]) => `${name} at ${rupees(plan.price)} with ${plan.data} and ${plan.extras}`
        );

        return {
          args: {},
          result: data.plans,
          reply: `The plans available are ${listSentence(parts)}. You're on ${data.plan}.`,
          ref: "Plan catalogue",
        };
      },
    },
    {
      id: "usage",
      topic: "Data usage",
      tool: "get_usage",
      match: /\b(data|usage|used|left|remaining|gb)\b/i,
      run: (data) => {
        const { usedGb, limitGb, resetsOn } = data.usage;

        return {
          args: {},
          result: data.usage,
          reply: `You've used ${usedGb} of your ${limitGb} GB, so ${limitGb - usedGb} GB is left. It resets on ${resetsOn}.`,
          ref: `Number ${data.number}`,
        };
      },
    },
    {
      id: "bill",
      topic: "Current bill",
      tool: "get_bill",
      match: /\b(bill|payment|pay|amount|charges)\b/i,
      run: (data) => ({
        args: {},
        result: data.bill,
        reply: `Your current bill is ${rupees(data.bill.amount)}, due on ${data.bill.dueDate}. It is ${data.bill.status}.`,
        ref: `Number ${data.number}`,
      }),
    },
    {
      id: "renewal",
      topic: "Plan renewal",
      tool: "get_plan",
      match: /\b(renew|renewal|validity|expire|expiry|plan)\b/i,
      run: (data) => ({
        args: {},
        result: { plan: data.plan, renews: data.usage.resetsOn },
        reply: `You're on ${data.plan} at ${rupees(data.plans[data.plan].price)} a month. It renews on ${data.usage.resetsOn}.`,
        ref: `Number ${data.number}`,
      }),
    },
  ],

  actions: {
    upgrade_plan: {
      label: "Upgrade plan",
      describe: (args, data) => {
        const plan = data.plans[args.plan];

        return `upgrade you from ${data.plan} to ${args.plan}, which is ${rupees(plan.price)} a month with ${plan.data}. The new price applies from your next bill`;
      },
      execute: (args, data) => {
        const previous = data.plan;

        data.plan = args.plan;

        return {
          result: { previous_plan: previous, new_plan: args.plan },
          summary: `Plan upgraded from ${previous} to ${args.plan}`,
          reply: `Done. You're now on ${args.plan}. The new price applies from your next bill.`,
          ref: `Number ${data.number}`,
        };
      },
    },
  },
};
