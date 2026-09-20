import { getJson } from "./api.js";

// Who the agent can be pointed at for a profile: [{ ref, display_name }].
// An empty list (or no server) means only the profile's built-in demo data.
export async function fetchCustomers(profileId) {
  try {
    return await getJson(`/api/customers?profile_id=${encodeURIComponent(profileId)}`);
  } catch {
    return [];
  }
}
