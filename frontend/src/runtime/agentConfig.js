// What the operator wants the voice agent to do: its use case, role, audience,
// tasks, tone, language and rules. This is structured data, not display text, so
// it can be handed to the call (and later to the server brain) as the agent's
// operating context. It is domain-agnostic: no industry is special.

export const OTHER = "Other";
export const CUSTOM = "Custom";

export const INDUSTRIES = [
  "Customer Support",
  "Healthcare",
  "Education",
  "Banking & Finance",
  "Sales",
  "Mining",
  "HR / Recruitment",
  "Receptionist",
  "Technical Support",
  OTHER,
];

export const TARGET_USERS = [
  "Customers",
  "Employees",
  "Students",
  "Operators",
  "Patients",
  "Managers",
  "General Users",
];

export const TASK_SUGGESTIONS = [
  "Answer user questions",
  "Search documents",
  "Provide information",
  "Analyze data",
  "Schedule appointments",
  "Handle customer requests",
  "Provide technical guidance",
  "Generate summaries",
  "Perform workflow actions",
];

export const BEHAVIORS = [
  "Professional",
  "Friendly",
  "Concise",
  "Detailed",
  "Technical",
  "Conversational",
  CUSTOM,
];

export const LANGUAGES = ["English", "Hindi", "English + Hindi", OTHER];

// Suggestions only: the role stays free text, so any domain works. Unknown
// industries (and "Other") simply get no suggestions.
export const ROLE_SUGGESTIONS = {
  "Customer Support": ["Customer Support Executive", "Order Support Assistant", "Complaints Handler"],
  Healthcare: ["Patient Support Assistant", "Appointment Coordinator", "Care Navigator"],
  Education: ["Student Support Assistant", "Admissions Counsellor", "Course Advisor"],
  "Banking & Finance": ["Banking Support Assistant", "Loan Enquiry Assistant", "Collections Reminder Agent"],
  Sales: ["Sales Development Representative", "Product Advisor", "Lead Qualification Assistant"],
  Mining: ["Mining Operations Support", "Safety Briefing Assistant", "Production Reporting Assistant"],
  "HR / Recruitment": ["HR Helpdesk Assistant", "Recruitment Screener", "Onboarding Assistant"],
  Receptionist: ["Virtual Receptionist", "Visitor Coordinator"],
  "Technical Support": ["Technical Support Engineer", "IT Helpdesk Assistant"],
};

export const LIMITS = {
  agentName: 60,
  role: 80,
  purpose: 500,
  domainContext: 1500,
  additionalInstructions: 1500,
  short: 60, // industryOther, languageOther, behaviorCustom and each chip
  chips: 12, // per list
};

const STORAGE_KEY = "voice-agent-config";

export function emptyConfig() {
  return {
    industry: "",
    industryOther: "",
    agentName: "",
    role: "",
    purpose: "",
    targetUsers: [],
    primaryTasks: [],
    domainContext: "",
    conversationBehavior: [],
    behaviorCustom: "",
    language: "English",
    languageOther: "",
    voice: "", // "" means the browser's default voice
    additionalInstructions: "",
  };
}

const text = (value, max) => (typeof value === "string" ? value.trim().slice(0, max) : "");

// Trimmed, capped and de-duplicated (ignoring case), in the order given.
function list(value, max = LIMITS.short) {
  const seen = new Set();
  const items = [];

  for (const entry of Array.isArray(value) ? value : []) {
    const item = text(entry, max);

    if (item && !seen.has(item.toLowerCase()) && items.length < LIMITS.chips) {
      seen.add(item.toLowerCase());
      items.push(item);
    }
  }

  return items;
}

// Always returns a complete, clean config, whatever it is given (a form, or
// something read back from storage that an older version wrote).
export function normalizeConfig(raw) {
  const input = raw && typeof raw === "object" ? raw : {};
  const blank = emptyConfig();
  const industry = INDUSTRIES.includes(input.industry) ? input.industry : blank.industry;
  const language = LANGUAGES.includes(input.language) ? input.language : blank.language;
  const behaviors = list(input.conversationBehavior).filter((item) => BEHAVIORS.includes(item));

  return {
    industry,
    industryOther: industry === OTHER ? text(input.industryOther, LIMITS.short) : "",
    agentName: text(input.agentName, LIMITS.agentName),
    role: text(input.role, LIMITS.role),
    purpose: text(input.purpose, LIMITS.purpose),
    targetUsers: list(input.targetUsers),
    primaryTasks: list(input.primaryTasks),
    domainContext: text(input.domainContext, LIMITS.domainContext),
    conversationBehavior: behaviors,
    behaviorCustom: behaviors.includes(CUSTOM) ? text(input.behaviorCustom, LIMITS.short) : "",
    language,
    languageOther: language === OTHER ? text(input.languageOther, LIMITS.short) : "",
    voice: text(input.voice, 200),
    additionalInstructions: text(input.additionalInstructions, LIMITS.additionalInstructions),
  };
}

// {field: message} for everything that stops this being a specialised agent.
// Empty means valid. Additional instructions, tasks and users stay optional.
export function validateConfig(config) {
  const value = normalizeConfig(config);
  const errors = {};

  if (!value.industry) errors.industry = "Choose the use case or industry.";
  else if (value.industry === OTHER && !value.industryOther) errors.industryOther = "Describe your use case or industry.";

  if (!value.agentName) errors.agentName = "Give the agent a name.";
  if (!value.role) errors.role = "Say what role the agent should perform.";
  if (!value.purpose) errors.purpose = "Describe what the agent should help users accomplish.";

  if (value.language === OTHER && !value.languageOther) errors.languageOther = "Enter the language.";
  if (value.conversationBehavior.includes(CUSTOM) && !value.behaviorCustom) {
    errors.behaviorCustom = "Describe the custom behavior, or deselect Custom.";
  }

  return errors;
}

export const isConfigured = (config) => Object.keys(validateConfig(config)).length === 0;

// The flat shape a call (and later the server) reads: "Other" and "Custom"
// replaced by what the operator actually typed. Null unless the config is valid.
export function resolveConfig(config) {
  if (!isConfigured(config)) return null;

  const value = normalizeConfig(config);

  return {
    industry: value.industry === OTHER ? value.industryOther : value.industry,
    agentName: value.agentName,
    role: value.role,
    purpose: value.purpose,
    targetUsers: value.targetUsers,
    primaryTasks: value.primaryTasks,
    domainContext: value.domainContext,
    conversationBehavior: value.conversationBehavior.map((item) => (item === CUSTOM ? value.behaviorCustom : item)),
    language: value.language === OTHER ? value.languageOther : value.language,
    voice: value.voice,
    additionalInstructions: value.additionalInstructions,
  };
}

// True when two configs are the same once cleaned, so "unsaved changes" ignores
// whitespace and key order.
export function sameConfig(a, b) {
  return JSON.stringify(normalizeConfig(a)) === JSON.stringify(normalizeConfig(b));
}

// Storage can be missing, full or blocked (private windows): never let that
// break the console. A stored config that no longer validates is ignored.
export function loadConfig(storage = globalThis.localStorage) {
  try {
    const stored = normalizeConfig(JSON.parse(storage.getItem(STORAGE_KEY)));

    return isConfigured(stored) ? stored : null;
  } catch {
    return null;
  }
}

export function saveConfig(config, storage = globalThis.localStorage) {
  try {
    storage.setItem(STORAGE_KEY, JSON.stringify(normalizeConfig(config)));
    return true;
  } catch {
    return false;
  }
}
