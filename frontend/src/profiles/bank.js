import { listSentence, makeGreeting, rupees } from "./shared.js";

const CARD_WORDS = { debit: "debit", credit: "credit" };

function pickCard(data, text) {
  const kind = Object.keys(CARD_WORDS).find((word) => text.toLowerCase().includes(word));

  return data.cards.find((card) => card.type === kind);
}

const describeCard = (card) => `${card.type} card ending ${card.last4}`;

export default {
  id: "bank",
  name: "Bank Customer Care",
  vertical: "Bank",
  workspaceLabel: "Bank Customer-Care Workspace",
  counterparty: "customer",

  headline: "How can I help with your account?",
  description:
    "Speak naturally with the AI voice agent about your balance, recent transactions, unusual activity or your cards.",

  greeting: makeGreeting(
    "I'm the virtual assistant for Northbridge Bank, speaking with Priya Sharma.",
    "I can help with your balance, recent transactions, unusual activity, or freezing a card. What can I do for you?"
  ),

  prompts: [
    "What's my balance?",
    "Show my recent transactions",
    "I got an unusual activity alert",
    "Freeze my credit card",
    "Which cards do I have?",
  ],

  capabilities: [
    "check your balance",
    "review recent transactions",
    "look into unusual activity",
    "freeze a card",
  ],

  nextSteps: [
    "Review the flagged transaction in the banking app",
    "Order a replacement card if one was frozen",
    "Follow up with the fraud team if charges are disputed",
  ],

  data: {
    customer: "Priya Sharma",
    account: { type: "savings", last4: "4821", balance: 84250.75 },
    cards: [
      { type: "debit", last4: "7712", status: "active" },
      { type: "credit", last4: "3390", status: "active" },
    ],
    transactions: [
      { date: "18 Sep", merchant: "Fresh Basket Groceries", amount: 2140, card: "debit" },
      { date: "17 Sep", merchant: "Metro Rail Recharge", amount: 500, card: "debit" },
      { date: "17 Sep", merchant: "TechMart Online, Singapore", amount: 18999, card: "credit", flagged: true },
      { date: "15 Sep", merchant: "City Pharmacy", amount: 860, card: "debit" },
      { date: "14 Sep", merchant: "StreamPlus Subscription", amount: 649, card: "credit" },
    ],
  },

  intents: [
    {
      id: "freeze_card",
      topic: "Card freeze",
      match: /\b(freeze|block|lock|stop)\b.*\bcard\b|\bcard\b.*\b(freeze|block|lock)\b|\bfreeze\b/i,
      propose: (data, text) => {
        const card = pickCard(data, text);

        if (!card) {
          return { reply: "Which card should I freeze, your debit card or your credit card?" };
        }

        if (card.status === "frozen") {
          return { reply: `Your ${describeCard(card)} is already frozen.` };
        }

        return { tool: "freeze_card", args: { card: card.type } };
      },
    },
    {
      id: "flagged",
      topic: "Unusual activity",
      tool: "get_flagged_activity",
      match: /\b(unusual|suspicious|fraud|alert|unauthori[sz]ed|didn't make|not me)\b/i,
      run: (data) => {
        const flagged = data.transactions.filter((item) => item.flagged);

        if (!flagged.length) {
          return {
            args: {},
            result: [],
            reply: "I don't see any flagged activity on your account right now.",
          };
        }

        const item = flagged[0];

        return {
          args: {},
          result: flagged,
          reply: `Yes, there is one flagged transaction: ${rupees(item.amount)} at ${item.merchant} on ${item.date}, on your ${item.card} card. If that wasn't you, I can freeze the card right now.`,
          ref: "Flagged transaction alert",
        };
      },
    },
    {
      id: "transactions",
      topic: "Recent transactions",
      tool: "get_recent_transactions",
      match: /\b(transaction|transactions|statement|spent|spending|recent|last)\b/i,
      run: (data) => {
        const top = data.transactions.slice(0, 3);
        const parts = top.map((item) => `${rupees(item.amount)} at ${item.merchant} on ${item.date}`);

        return {
          args: { limit: 3 },
          result: top,
          reply: `Your three most recent transactions are ${listSentence(parts)}.`,
          ref: `Account ending ${data.account.last4}`,
        };
      },
    },
    {
      id: "balance",
      topic: "Account balance",
      tool: "get_balance",
      match: /\b(balance|how much)\b/i,
      run: (data) => ({
        args: { account: data.account.last4 },
        result: data.account,
        reply: `The balance in your ${data.account.type} account ending ${data.account.last4} is ${rupees(data.account.balance)}.`,
        ref: `Account ending ${data.account.last4}`,
      }),
    },
    {
      id: "cards",
      topic: "Card status",
      tool: "get_cards",
      match: /\b(cards?)\b/i,
      run: (data) => ({
        args: {},
        result: data.cards,
        reply: `You have ${listSentence(data.cards.map((card) => `a ${describeCard(card)}, currently ${card.status}`))}.`,
        ref: "Card list",
      }),
    },
  ],

  actions: {
    freeze_card: {
      label: "Freeze card",
      describe: (args, data) => {
        const card = data.cards.find((item) => item.type === args.card);

        return `freeze your ${describeCard(card)}. It will be declined everywhere until you unfreeze it`;
      },
      execute: (args, data) => {
        const card = data.cards.find((item) => item.type === args.card);

        card.status = "frozen";

        return {
          result: { card: describeCard(card), status: card.status },
          summary: `${describeCard(card)} frozen`.replace(/^./, (c) => c.toUpperCase()),
          reply: `Done. Your ${describeCard(card)} is now frozen. You can unfreeze it any time from the app.`,
          ref: `Card ending ${card.last4}`,
        };
      },
    },
  },
};
