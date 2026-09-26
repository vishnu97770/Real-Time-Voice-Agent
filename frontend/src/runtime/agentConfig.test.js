import assert from "node:assert/strict";
import { test } from "node:test";

import {
  CUSTOM,
  LIMITS,
  INDUSTRIES,
  OTHER,
  ROLE_SUGGESTIONS,
  emptyConfig,
  fromAgentPayload,
  isConfigured,
  loadConfig,
  normalizeConfig,
  resolveConfig,
  sameConfig,
  saveConfig,
  toAgentPayload,
  validateConfig,
} from "./agentConfig.js";

const mining = () => ({
  ...emptyConfig(),
  industry: "Mining",
  agentName: "MineAssist",
  role: "Mining Operations Support Agent",
  purpose: "Help mine operators understand production data.",
  targetUsers: ["Operators", "Managers"],
  primaryTasks: ["Explain reports"],
  conversationBehavior: ["Professional", "Concise"],
  language: "English + Hindi",
});

const memoryStorage = () => {
  const items = new Map();

  return {
    getItem: (key) => items.get(key) ?? null,
    setItem: (key, value) => items.set(key, value),
  };
};

test("an empty configuration is not a valid agent", () => {
  const errors = validateConfig(emptyConfig());

  assert.deepEqual(Object.keys(errors).sort(), ["agentName", "industry", "purpose", "role"]);
  assert.equal(isConfigured(emptyConfig()), false);
  assert.equal(resolveConfig(emptyConfig()), null);
});

test("a filled-in configuration is valid, and instructions stay optional", () => {
  assert.deepEqual(validateConfig(mining()), {});
  assert.equal(mining().additionalInstructions, "");
});

test("whitespace does not count as an answer", () => {
  const errors = validateConfig({ ...mining(), agentName: "   ", purpose: "\n" });

  assert.deepEqual(Object.keys(errors).sort(), ["agentName", "purpose"]);
});

test("Other needs a description, and so do Custom behavior and Other language", () => {
  const errors = validateConfig({
    ...mining(),
    industry: OTHER,
    language: OTHER,
    conversationBehavior: [CUSTOM],
  });

  assert.deepEqual(Object.keys(errors).sort(), ["behaviorCustom", "industryOther", "languageOther"]);
});

test("Other and Custom resolve to what was typed", () => {
  const resolved = resolveConfig({
    ...mining(),
    industry: OTHER,
    industryOther: "Aviation",
    language: OTHER,
    languageOther: "Tamil",
    conversationBehavior: ["Concise", CUSTOM],
    behaviorCustom: "Calm under pressure",
  });

  assert.equal(resolved.industry, "Aviation");
  assert.equal(resolved.language, "Tamil");
  assert.deepEqual(resolved.conversationBehavior, ["Concise", "Calm under pressure"]);
});

test("resolving gives the flat context a call reads", () => {
  assert.deepEqual(resolveConfig(mining()), {
    industry: "Mining",
    agentName: "MineAssist",
    role: "Mining Operations Support Agent",
    purpose: "Help mine operators understand production data.",
    targetUsers: ["Operators", "Managers"],
    primaryTasks: ["Explain reports"],
    domainContext: "",
    conversationBehavior: ["Professional", "Concise"],
    language: "English + Hindi",
    voice: "",
    additionalInstructions: "",
  });
});

test("normalizing trims, de-duplicates, caps and drops unknown values", () => {
  const value = normalizeConfig({
    ...mining(),
    agentName: `  ${"x".repeat(200)}  `,
    targetUsers: [" Operators ", "operators", "", 7, "Managers"],
    primaryTasks: Array.from({ length: 30 }, (_, index) => `task ${index}`),
    conversationBehavior: ["Concise", "Sarcastic"],
    industry: "Piracy",
    language: "Klingon",
  });

  assert.equal(value.agentName.length, LIMITS.agentName);
  assert.deepEqual(value.targetUsers, ["Operators", "Managers"]);
  assert.equal(value.primaryTasks.length, LIMITS.chips);
  assert.deepEqual(value.conversationBehavior, ["Concise"]);
  assert.equal(value.industry, "");
  assert.equal(value.language, "English");
});

test("junk input still gives a complete configuration", () => {
  for (const junk of [null, undefined, "text", 5, [], { targetUsers: "Operators" }]) {
    assert.deepEqual(Object.keys(normalizeConfig(junk)), Object.keys(emptyConfig()));
  }
});

test("a description for a choice that is no longer selected is dropped", () => {
  const value = normalizeConfig({ ...mining(), industryOther: "Aviation", languageOther: "Tamil", behaviorCustom: "x" });

  assert.equal(value.industryOther, "");
  assert.equal(value.languageOther, "");
  assert.equal(value.behaviorCustom, "");
});

test("a saved configuration is loaded back", () => {
  const storage = memoryStorage();

  assert.equal(saveConfig(mining(), storage), true);
  assert.deepEqual(loadConfig(storage), normalizeConfig(mining()));
});

test("nothing, garbage, or an invalid configuration in storage loads as none", () => {
  const storage = memoryStorage();

  assert.equal(loadConfig(storage), null);

  storage.setItem("voice-agent-config", "{not json");
  assert.equal(loadConfig(storage), null);

  storage.setItem("voice-agent-config", JSON.stringify({ agentName: "Half done" }));
  assert.equal(loadConfig(storage), null);
});

test("blocked storage never throws", () => {
  const blocked = {
    getItem: () => {
      throw new Error("denied");
    },
    setItem: () => {
      throw new Error("denied");
    },
  };

  assert.equal(loadConfig(blocked), null);
  assert.equal(saveConfig(mining(), blocked), false);
  assert.equal(loadConfig(undefined), null); // no localStorage at all, as in Node
});

test("two configs are the same once cleaned, whatever whitespace or key order", () => {
  const a = mining();
  const b = { ...a, agentName: "  MineAssist  ", role: `${a.role} ` };

  assert.equal(sameConfig(a, b), true);
  assert.equal(sameConfig(a, { ...a, purpose: "Something else." }), false);
  assert.equal(sameConfig(null, emptyConfig()), true);
});

test("role suggestions only exist for known industries, so any domain still works", () => {
  for (const industry of Object.keys(ROLE_SUGGESTIONS)) {
    assert.ok(INDUSTRIES.includes(industry), `${industry} is not an industry`);
    assert.ok(ROLE_SUGGESTIONS[industry].length > 0);
  }

  assert.equal(ROLE_SUGGESTIONS[OTHER], undefined);
});

// --- the backend Agent payload (Step 18B) ---------------------------------------------------

test("an unconfigured agent has no payload to send", () => {
  assert.equal(toAgentPayload(emptyConfig()), null);
});

test("a valid configuration becomes the shape the Agent API reads", () => {
  assert.deepEqual(toAgentPayload(mining()), {
    name: "MineAssist",
    role: "Mining Operations Support Agent",
    industry: "Mining",
    purpose: "Help mine operators understand production data.",
    target_users: ["Operators", "Managers"],
    primary_tasks: ["Explain reports"],
    behavior_config: { tone: ["Professional", "Concise"] },
    instructions: { domain_context: "", additional_instructions: "" },
    language: "English + Hindi",
    voice: null,
  });
});

test("Other industry, Other language and a Custom behavior are resolved before sending, same as resolveConfig", () => {
  const payload = toAgentPayload({
    ...mining(),
    industry: OTHER,
    industryOther: "Aviation",
    language: OTHER,
    languageOther: "Tamil",
    conversationBehavior: ["Concise", CUSTOM],
    behaviorCustom: "Calm under pressure",
  });

  assert.equal(payload.industry, "Aviation");
  assert.equal(payload.language, "Tamil");
  assert.deepEqual(payload.behavior_config.tone, ["Concise", "Calm under pressure"]);
});

test("an agent loaded from the backend fills the form the same way the form would have produced it", () => {
  assert.deepEqual(fromAgentPayload(toAgentPayload(mining())), normalizeConfig(mining()));
});

test("loading a backend agent never drops a value that is no longer a fixed choice: it becomes Other/Custom", () => {
  const agent = {
    name: "MineAssist",
    role: "Mining Operations Support Agent",
    industry: "Piracy on the high seas", // not one of the fixed INDUSTRIES
    purpose: "Help mine operators understand production data.",
    target_users: ["Operators"],
    primary_tasks: [],
    behavior_config: { tone: ["Concise", "Menacing"] }, // "Menacing" is not one of the fixed BEHAVIORS
    instructions: { domain_context: "", additional_instructions: "" },
    language: "Klingon", // not one of the fixed LANGUAGES
    voice: null,
  };

  const form = fromAgentPayload(agent);

  assert.equal(form.industry, OTHER);
  assert.equal(form.industryOther, "Piracy on the high seas");
  assert.deepEqual(form.conversationBehavior, ["Concise", CUSTOM]);
  assert.equal(form.behaviorCustom, "Menacing");
  assert.equal(form.language, OTHER);
  assert.equal(form.languageOther, "Klingon");

  // And validates as a complete, savable configuration again.
  assert.deepEqual(validateConfig(form), {});
});

test("a backend agent with nothing in its instructions or behavior still loads as a valid, empty-optional form", () => {
  const form = fromAgentPayload({
    name: "Bare",
    role: "Role",
    industry: "Mining",
    purpose: "Purpose.",
    target_users: [],
    primary_tasks: [],
    behavior_config: {},
    instructions: {},
    language: "English",
    voice: null,
  });

  assert.deepEqual(validateConfig(form), {});
  assert.equal(form.domainContext, "");
  assert.equal(form.additionalInstructions, "");
  assert.deepEqual(form.conversationBehavior, []);
});
