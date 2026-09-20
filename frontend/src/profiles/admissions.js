import { listSentence, makeGreeting, rupees } from "./shared.js";

const SLOTS = {
  morning: "Thursday at 10 AM",
  afternoon: "Thursday at 4 PM",
  evening: "Friday at 6 PM",
};

function requestedSlot(text) {
  const key = Object.keys(SLOTS).find((word) => text.toLowerCase().includes(word));

  return SLOTS[key ?? "afternoon"];
}

export default {
  id: "admissions",
  name: "Admissions Office",
  vertical: "Admissions",
  workspaceLabel: "Admissions Desk Workspace",
  counterparty: "applicant",

  headline: "How can I help with your application?",
  description:
    "Speak naturally with the AI voice agent about your application status, program details or booking a counselor call.",

  greeting: makeGreeting(
    "I'm the virtual assistant for the Lakeview University admissions office, speaking with Kavya Reddy.",
    "I can help with your application status, your document checklist, program details, or booking a counselor call. What would you like to know?"
  ),

  prompts: [
    "What's my application status?",
    "Which documents are pending?",
    "Tell me about the program",
    "What are the deadlines?",
    "Book a counselor call",
  ],

  capabilities: [
    "check your application status",
    "review your document checklist",
    "explain the program",
    "share key deadlines",
    "book a counselor call",
  ],

  nextSteps: [
    "Upload the remaining documents",
    "Prepare for the interview",
    "Attend the scheduled counselor call",
  ],

  data: {
    applicant: "Kavya Reddy",
    application: {
      id: "ADM-2026-0412",
      program: "MSc Data Science",
      status: "documents verified, awaiting interview",
      checklist: [
        { item: "transcripts", done: true },
        { item: "statement of purpose", done: true },
        { item: "recommendation letter", done: false },
        { item: "English proficiency score", done: true },
      ],
      interview: "not yet scheduled",
    },
    program: {
      duration: "two years",
      fee: 385000,
      intake: "January 2027",
      format: "on campus with an optional industry placement",
    },
    deadlines: [
      { label: "Document submission", date: "30 September 2026" },
      { label: "Interviews", date: "the first two weeks of October 2026" },
      { label: "Fee payment after offer", date: "15 November 2026" },
    ],
    counselorCalls: [],
  },

  intents: [
    {
      id: "book_counselor",
      topic: "Counselor call",
      match: /\b(counsel|counselor|counsellor|book|schedule|call me|talk to|speak to)\b/i,
      propose: (data, text) => ({
        tool: "schedule_counselor_call",
        args: { slot: requestedSlot(text), application_id: data.application.id },
      }),
    },
    {
      id: "documents",
      topic: "Document checklist",
      tool: "get_checklist",
      match: /\b(document|documents|checklist|missing|pending|outstanding|submitted)\b/i,
      run: (data) => {
        const missing = data.application.checklist.filter((entry) => !entry.done).map((entry) => entry.item);

        return {
          args: { application_id: data.application.id },
          result: data.application.checklist,
          reply: missing.length
            ? `Your checklist is nearly complete. Still pending: ${listSentence(missing)}.`
            : "All your documents have been received and verified.",
          ref: `Application: ${data.application.id}`,
        };
      },
    },
    {
      id: "program",
      topic: "Program details",
      tool: "get_program_details",
      match: /\b(program|programme|course|curriculum|duration|fee|fees|intake|cost)\b/i,
      run: (data) => {
        const { program, application } = data;

        return {
          args: { program: application.program },
          result: program,
          reply: `${application.program} is a ${program.duration} program, ${program.format}. The next intake is ${program.intake} and the fee is ${rupees(program.fee)}.`,
          ref: `Program: ${application.program}`,
        };
      },
    },
    {
      id: "deadlines",
      topic: "Deadlines",
      tool: "get_deadlines",
      match: /\b(deadline|deadlines|last date|when|due)\b/i,
      run: (data) => ({
        args: {},
        result: data.deadlines,
        reply: `Key dates: ${data.deadlines.map((entry) => `${entry.label} by ${entry.date}`).join("; ")}.`,
        ref: "Admissions calendar",
      }),
    },
    {
      id: "status",
      topic: "Application status",
      tool: "get_application_status",
      match: /\b(status|application|progress|update|interview)\b/i,
      run: (data) => {
        const { application } = data;

        return {
          args: { application_id: application.id },
          result: application,
          reply: `Your application ${application.id} for ${application.program} is ${application.status}. The interview is ${application.interview}.`,
          ref: `Application: ${application.id}`,
        };
      },
    },
  ],

  actions: {
    schedule_counselor_call: {
      label: "Schedule counselor call",
      describe: (args) => `book a counselor call for you on ${args.slot}`,
      execute: (args, data) => {
        const booking = {
          booking_id: `CNS-${710 + data.counselorCalls.length}`,
          slot: args.slot,
          application_id: args.application_id,
        };

        data.counselorCalls.push(booking);

        return {
          result: booking,
          summary: `Counselor call ${booking.booking_id} booked for ${args.slot}`,
          reply: `Done. Your counselor call is booked for ${args.slot}. The confirmation reference is ${booking.booking_id}.`,
          ref: `Booking: ${booking.booking_id}`,
        };
      },
    },
  },
};
