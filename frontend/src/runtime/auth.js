import { ApiError, getJson, post } from "./api.js";

// The signed-in operator, or null.
export async function fetchSession() {
  try {
    return (await getJson("/api/auth/me")).user;
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) return null;
    throw error;
  }
}

export async function login(email, password) {
  try {
    return (await (await post("/api/auth/login", { email, password })).json()).user;
  } catch (error) {
    if (error instanceof ApiError && error.status === 429) {
      throw new Error(`Too many attempts. Try again in ${error.retryAfter ?? "a few"} seconds.`, { cause: error });
    }
    if (error instanceof ApiError && error.status === 401) {
      throw new Error("Invalid email or password.", { cause: error });
    }
    throw new Error("Could not reach the server.", { cause: error });
  }
}

export async function logout() {
  try {
    await post("/api/auth/logout");
  } catch {
    // Signing out locally is what matters; the cookie expires on its own anyway.
  }
}
