import underwriting from "./underwriting.js";
import bank from "./bank.js";
import insurance from "./insurance.js";
import telecom from "./telecom.js";
import admissions from "./admissions.js";

// Adding a vertical means adding a profile file and listing it here.
// Nothing in ../runtime changes.
export const PROFILES = [underwriting, bank, insurance, telecom, admissions];

export const DEFAULT_PROFILE_ID = underwriting.id;

export function getProfile(id) {
  return PROFILES.find((profile) => profile.id === id) ?? PROFILES[0];
}
