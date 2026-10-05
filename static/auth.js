/**
 * Shared auth helpers for assistant.html and calendar-reminders.html.
 * Token stored in localStorage as just_auth_token.
 */
(function (global) {
  var TOKEN_KEY = "just_auth_token";

  function getToken() {
    try {
      return localStorage.getItem(TOKEN_KEY) || "";
    } catch (e) {
      return "";
    }
  }

  function setToken(t) {
    try {
      if (t) localStorage.setItem(TOKEN_KEY, t);
      else localStorage.removeItem(TOKEN_KEY);
    } catch (e) {}
  }

  function clearToken() {
    setToken("");
  }

  function authHeaders() {
    var t = getToken();
    var h = { "Content-Type": "application/json" };
    if (t) h["Authorization"] = "Bearer " + t;
    return h;
  }

  function baseUrl() {
    return typeof global.location !== "undefined" ? global.location.origin : "";
  }

  async function register(apiBase, email, password) {
    var r = await fetch((apiBase || baseUrl()) + "/api/auth/register", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email: email, password: password }),
    });
    var d = await r.json().catch(function () {
      return {};
    });
    if (!r.ok) throw new Error(d.error || "Registration failed");
    if (d.token) setToken(d.token);
    return d.user;
  }

  async function login(apiBase, email, password) {
    var r = await fetch((apiBase || baseUrl()) + "/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email: email, password: password }),
    });
    var d = await r.json().catch(function () {
      return {};
    });
    if (!r.ok) throw new Error(d.error || "Login failed");
    if (d.token) setToken(d.token);
    return d.user;
  }

  function logout() {
    clearToken();
  }

  global.JUSTAuth = {
    TOKEN_KEY: TOKEN_KEY,
    getToken: getToken,
    setToken: setToken,
    clearToken: clearToken,
    authHeaders: authHeaders,
    register: register,
    login: login,
    logout: logout,
  };
})(typeof window !== "undefined" ? window : globalThis);
