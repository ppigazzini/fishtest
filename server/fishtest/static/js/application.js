// Global variables
const fishtestBroadcastKey = "fishtest_broadcast";
let broadcastDispatch = {
  logout_: logout_,
};

// Application main function
(async () => {
  await DOMContentLoaded();
  handleTabsBroadcasting();
  handleModalFocusManagement();
  protectForms();
  handlePanelToggleCookies();
  handleCheckboxUiCookies();
  handleApplicationLogout();
  handleApplicationThemes();
  handleLoginRememberMePreference();
  handleClientRateLimitPolling();
})();

// Awaits the page content to load
function DOMContentLoaded() {
  // Use as
  // await DOMContentLoaded();
  // in an async function.
  return new Promise((resolve, _reject) => {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", resolve);
    } else {
      resolve();
    }
  });
}

// Executes a function in all connected tabs.
// See notifications.js for a usage example.
function handleTabsBroadcasting() {
  window.addEventListener("storage", (event) => {
    if (event.key === fishtestBroadcastKey) {
      const cmd = JSON.parse(event.newValue);
      const cmdFunction = broadcastDispatch[cmd["cmd"]];
      if (cmdFunction) {
        if (cmd["arg"]) cmdFunction(cmd["arg"]);
        else cmdFunction();
      }
    }
  });
}

function handleModalFocusManagement() {
  document.addEventListener("hide.bs.modal", (event) => {
    const modal = event.target;
    if (!(modal instanceof HTMLElement)) {
      return;
    }

    const activeElement = document.activeElement;
    if (activeElement instanceof HTMLElement && modal.contains(activeElement)) {
      activeElement.blur();
    }
  });
}

// CSRF protection for links and forms
function protectForms() {
  const csrfToken = document.querySelector("meta[name='csrf-token']")[
    "content"
  ];

  document.querySelectorAll("form[method='POST']")?.forEach((form) => {
    if (form.querySelector("input[name='csrf_token']")) {
      return;
    }
    const input = document.createElement("input");
    input["type"] = "hidden";
    input["name"] = "csrf_token";
    input["value"] = csrfToken;
    form.append(input);
  });
}

// Gets from the browser the value of a saved cookie.
function readUiCookie(cookieName) {
  if (!cookieName) {
    return "";
  }

  const key = `${cookieName}=`;
  const cookiePart = document.cookie
    .split(";")
    .map((part) => part.trim())
    .find((part) => part.startsWith(key));

  if (!cookiePart) {
    return "";
  }

  const rawValue = cookiePart.slice(key.length);
  try {
    return decodeURIComponent(rawValue);
  } catch {
    return rawValue;
  }
}

function writeUiCookie(name, value, maxAgeSeconds) {
  if (!name) {
    return;
  }
  const maxAge = Number(maxAgeSeconds);
  if (!Number.isFinite(maxAge) || maxAge <= 0) {
    return;
  }
  // Keep UI state cookies available across all pages that reuse the same panels.
  document.cookie = `${name}=${encodeURIComponent(value)}; path=/; max-age=${maxAge}; SameSite=Lax`;
}

function clearUiCookie(name) {
  if (!name) {
    return;
  }
  document.cookie = `${name}=; path=/; max-age=0; SameSite=Lax`;
}

function syncCheckboxUiCookie(checkbox) {
  if (!(checkbox instanceof HTMLInputElement) || checkbox.type !== "checkbox") {
    return;
  }

  const cookieName = checkbox.dataset.uiCookieName;
  if (!cookieName) {
    return;
  }

  const maxAge =
    checkbox.dataset.uiCookieMaxAge || window.uiStateCookieMaxAgeSeconds;
  const checkedValue = checkbox.dataset.uiCookieCheckedValue || "true";
  const uncheckedValue = checkbox.dataset.uiCookieUncheckedValue || "false";

  writeUiCookie(
    cookieName,
    checkbox.checked ? checkedValue : uncheckedValue,
    maxAge,
  );
}

function handleCheckboxUiCookies() {
  document.addEventListener(
    "change",
    (event) => {
      syncCheckboxUiCookie(event.target);
    },
    true,
  );
}

function handlePanelToggleCookies() {
  document.addEventListener("click", (e) => {
    if (!(e.target instanceof Element)) {
      return;
    }
    const button = e.target.closest("[data-toggle-cookie-name]");
    if (!(button instanceof HTMLElement)) {
      return;
    }

    const cookieName = button.dataset.toggleCookieName;
    const maxAge = button.dataset.toggleCookieMaxAge;
    const active = button.textContent.trim() === "Hide";
    const nextState = active ? "Show" : "Hide";

    button.textContent = nextState;
    writeUiCookie(cookieName, nextState, maxAge);
  });
}

function handleLoginRememberMePreference() {
  const checkbox = document.querySelector("[data-remember-me-cookie-name]");
  if (!(checkbox instanceof HTMLInputElement) || checkbox.type !== "checkbox") {
    return;
  }

  const cookieName = checkbox.dataset.rememberMeCookieName;
  const maxAge =
    checkbox.dataset.rememberMeCookieMaxAge ||
    window.uiStateCookieMaxAgeSeconds;
  checkbox.addEventListener("change", () => {
    writeUiCookie(cookieName, checkbox.checked ? "1" : "0", maxAge);
  });
}

function formatBytes(bytes) {
  const units = ["B", "KiB", "MiB", "GiB", "TiB"];
  let unitIndex = 0;
  while (bytes >= 1024 && unitIndex < units.length - 1) {
    bytes /= 1024;
    unitIndex++;
  }
  return `${bytes.toFixed(2)} ${units[unitIndex]}`;
}

function handleApplicationThemes() {
  if (!readUiCookie("theme")) {
    setTheme(mediaTheme());
  }

  try {
    window
      .matchMedia("(prefers-color-scheme: dark)")
      .addEventListener("change", () => setTheme(mediaTheme()));
  } catch (e) {
    console.error(e);
  }

  document
    .getElementById("sun")
    .addEventListener("click", () => setTheme("light"));

  document
    .getElementById("moon")
    .addEventListener("click", () => setTheme("dark"));
}

// Gets prefered theme based on user's system
function mediaTheme() {
  return window.matchMedia("(prefers-color-scheme: dark)").matches
    ? "dark"
    : "light";
}

// Click the sun/moon icons to change the color theme of the site
function setTheme(theme) {
  const darkLink = document.querySelector(
    'head link[href*="/css/theme.dark.css"]',
  );
  if (theme === "dark") {
    document.getElementById("sun").style.display = "";
    document.getElementById("moon").style.display = "none";
    document.documentElement.style.colorScheme = "dark";
    if (darkLink) {
      darkLink.removeAttribute("media");
    } else {
      const link = document.createElement("link");
      link["rel"] = "stylesheet";
      link["href"] = darkThemeHash;
      document.querySelector("head").append(link);
    }
  } else {
    document.getElementById("sun").style.display = "none";
    document.getElementById("moon").style.display = "";
    document.documentElement.style.colorScheme = "";
    darkLink?.remove();
  }
  writeUiCookie("theme", theme, window.uiStateCookieMaxAgeSeconds);
}

function supportsNotifications() {
  return (
    // Notifications only work over a secure connection
    window.location.protocol === "https:" &&
    // Safari on iOS doesn't support them
    "Notification" in window &&
    // Chrome and Opera on Android don't support them
    !(
      navigator.userAgent.match(/Android/i) &&
      navigator.userAgent.match(/Chrome/i)
    )
  );
}

function notify(title, body, link, fallback) {
  // Instrumentation
  log(`Notification: title=${title} body=${body}`);
  if (supportsNotifications() && Notification.permission === "granted") {
    const notification = new Notification(title, {
      body: body,
      requireInteraction: true,
      icon: "/static/img/stockfish.webp",
    });
    notification.onclick = () => {
      window.open(link, "_blank", "noopener,noreferrer");
    };
  } else if (fallback) {
    fallback(title, body, link);
  }
}

// A console log with time stamp and optional stack trace
function log(message, trace) {
  const d = new Date().toISOString();
  const message_ = `${d}: ${message}`;
  if (trace) {
    console.trace(message_);
  } else {
    console.log(message_);
  }
}

// Convert HTTP status code into status text
function getStatusText(code) {
  return (
    {
      100: "Continue",
      101: "Switching Protocols",
      102: "Processing",
      200: "OK",
      201: "Created",
      202: "Accepted",
      203: "Non-authoritative Information",
      204: "No Content",
      205: "Reset Content",
      206: "Partial Content",
      207: "Multi-Status",
      208: "Already Reported",
      226: "IM Used",
      300: "Multiple Choices",
      301: "Moved Permanently",
      302: "Found",
      303: "See Other",
      304: "Not Modified",
      305: "Use Proxy",
      307: "Temporary Redirect",
      308: "Permanent Redirect",
      400: "Bad Request",
      401: "Unauthorized",
      402: "Payment Required",
      403: "Forbidden",
      404: "Not Found",
      405: "Method Not Allowed",
      406: "Not Acceptable",
      407: "Proxy Authentication Required",
      408: "Request Timeout",
      409: "Conflict",
      410: "Gone",
      411: "Length Required",
      412: "Precondition Failed",
      413: "Payload Too Large",
      414: "Request-URI Too Long",
      415: "Unsupported Media Type",
      416: "Requested Range Not Satisfiable",
      417: "Expectation Failed",
      418: "I'm a teapot",
      421: "Misdirected Request",
      422: "Unprocessable Entity",
      423: "Locked",
      424: "Failed Dependency",
      426: "Upgrade Required",
      428: "Precondition Required",
      429: "Too Many Requests",
      431: "Request Header Fields Too Large",
      444: "Connection Closed Without Response",
      451: "Unavailable For Legal Reasons",
      499: "Client Closed Request",
      500: "Internal Server Error",
      501: "Not Implemented",
      502: "Bad Gateway",
      503: "Service Unavailable",
      504: "Gateway Timeout",
      505: "HTTP Version Not Supported",
      506: "Variant Also Negotiates",
      507: "Insufficient Storage",
      508: "Loop Detected",
      510: "Not Extended",
      511: "Network Authentication Required",
      599: "Network Connect Timeout Error",
    }[code] || ""
  );
}

// A custom error for 400 and 500 responses
class HTTPError extends Error {
  constructor(message, options) {
    super(message, options);
    this.name = this.constructor.name;
    this.response = options.response;
  }
}

// A useful function from the python requests package
function raiseForStatus(response) {
  if (response.ok) {
    return;
  }
  options = { response: response };
  throw new HTTPError(
    `request ${response.url} failed with status code ${response.status} (${getStatusText(response.status)})`,
    options,
  );
}

async function handleTitle(count) {
  // async because the document title is often set in javascript
  await DOMContentLoaded();
  let title = document.title;
  // check if there is already a number
  if (/\([\d]+\)/.test(title)) {
    // if so, remove it
    let idx = title.indexOf(" ");
    title = title.slice(idx + 1);
  }
  // add it again if necessary
  if (count > 0) {
    document.title = `(${count}) ${title}`;
  } else {
    document.title = title;
  }
}

function asyncSleep(ms) {
  return new Promise((resolve, _reject) => {
    const t0 = Date.now();
    setTimeout(() => {
      const t1 = Date.now();
      resolve(t1 - t0);
    }, ms);
  });
}

async function fetchText(url, options) {
  const response = await fetch(url, options);
  raiseForStatus(response);
  return response.text();
}

async function fetchJson(url, options) {
  const response = await fetch(url, options);
  raiseForStatus(response);
  return response.json();
}

async function fetchPost(url, payload) {
  const options = {
    method: "POST",
    mode: "cors",
    cache: "no-cache",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  };
  return fetchJson(url, options);
}

function handleApplicationLogout() {
  document.getElementById("logout")?.addEventListener("click", (e) => {
    e.preventDefault();
    logout();
  });
}

// Alerts errors to the UI
function alertError(message) {
  document.getElementById("error_div").style.display = "";
  document.getElementById("error").textContent = message;
}

async function logout_() {
  const csrfToken = document.querySelector("meta[name='csrf-token']")[
    "content"
  ];

  try {
    const response = await fetch("/logout", {
      method: "POST",
      headers: {
        "X-CSRF-Token": csrfToken,
      },
    });

    raiseForStatus(response);
    window.location = "/";
  } catch (error) {
    alertError("Network error: please check your network!");
  }
}

function logout() {
  broadcast("logout_");
}

function broadcast(cmd, arg) {
  const cmdFunction = broadcastDispatch[cmd];
  localStorage.setItem(
    fishtestBroadcastKey,
    JSON.stringify({ cmd: cmd, arg: arg, rnd: Math.random() }),
  );
  if (arg) cmdFunction(arg);
  else cmdFunction();
}

// serializing objects
function saveObject(key, value) {
  const key_ = `__fishtest__${key}`;
  const value_ = JSON.stringify(value);
  localStorage.setItem(key_, value_);
}
function loadObject(key) {
  const key_ = `__fishtest__${key}`;
  const value_ = localStorage.getItem(key_);
  return JSON.parse(value_);
}

function escapeHtml(unsafe) {
  return unsafe
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;")
    .replace(/\n/g, "<br>");
}

// A helper for conveniently adding a timeout
// to a fetch call
const abortTimeout = (timeout) => {
  if (AbortSignal.timeout) {
    return AbortSignal.timeout(timeout);
  } else {
    const controller = new AbortController();
    setTimeout(() => controller.abort(), timeout);
    return controller.signal;
  }
};

// Call the GitHub api to get the current
// rate limit.
async function rateLimit() {
  const url = "https://api.github.com/rate_limit";
  const token = localStorage.getItem("github_token");
  const options = token
    ? {
        headers: {
          Authorization: "Bearer " + token,
        },
      }
    : {};
  options.signal = abortTimeout(3000);
  const rateLimit_ = await fetchJson(url, options);
  return rateLimit_["resources"]["core"];
}

function setGitHubRateLimitLowState(isLow) {
  const value = isLow ? "1" : "0";
  document.documentElement.dataset.githubRateLimitLow = value;
  try {
    localStorage.setItem("fishtest_github_rate_limit_low", value);
  } catch (_error) {
    // localStorage may be unavailable; keep the in-memory DOM state only.
  }
}

function isClientRateLimitLow(rateLimit_) {
  const remaining = Number(rateLimit_?.remaining);
  const used = Number(rateLimit_?.used);
  const reset = Number(rateLimit_?.reset);

  return (
    Number.isFinite(remaining) &&
    Number.isFinite(used) &&
    Number.isFinite(reset) &&
    Date.now() / 1000 <= reset &&
    remaining < used
  );
}

function applyRateLimitDangerState(element, isLow) {
  if (!(element instanceof HTMLElement)) {
    return;
  }

  element.classList.toggle("text-danger", isLow);
}

function updateRateLimitsNavLink(navLink, isLow) {
  if (!(navLink instanceof HTMLAnchorElement)) {
    return;
  }

  setGitHubRateLimitLowState(isLow);
  navLink.textContent = "GitHub Rate Limits";
  applyRateLimitDangerState(navLink, isLow);
}

function updateClientRateLimitCells(
  clientRateLimitDom,
  clientResetDom,
  rateLimit_,
) {
  if (!(clientRateLimitDom instanceof HTMLElement)) {
    return;
  }

  const remaining = Number(rateLimit_?.remaining);
  const reset = Number(rateLimit_?.reset);
  const isLow = isClientRateLimitLow(rateLimit_);

  clientRateLimitDom.textContent = Number.isFinite(remaining)
    ? String(remaining)
    : "-1";
  applyRateLimitDangerState(clientRateLimitDom, isLow);

  if (clientResetDom instanceof HTMLElement) {
    clientResetDom.textContent = Number.isFinite(reset)
      ? new Date(1000 * reset).toLocaleTimeString()
      : "00:00:00";
    applyRateLimitDangerState(clientResetDom, isLow);
  }
}

function handleClientRateLimitPolling() {
  const navLink = document.getElementById("rate-limits-nav-link");
  const clientRateLimitDom = document.getElementById("client_rate_limit");
  const clientResetDom = document.getElementById("client_reset");

  if (
    !(navLink instanceof HTMLAnchorElement) &&
    !(clientRateLimitDom instanceof HTMLElement)
  ) {
    return;
  }

  const pollSecondsRaw =
    navLink instanceof HTMLAnchorElement
      ? navLink.dataset.pollSeconds
      : clientRateLimitDom.dataset.pollSeconds;
  const pollSeconds = Number.parseInt(pollSecondsRaw || "10", 10);
  const pollIntervalMs =
    Math.max(Number.isFinite(pollSeconds) ? pollSeconds : 10, 1) * 1000;

  let pollTimeout = 0;
  let refreshInFlight = false;
  let refreshQueued = false;

  function clearPollTimeout() {
    if (pollTimeout !== 0) {
      window.clearTimeout(pollTimeout);
      pollTimeout = 0;
    }
  }

  function scheduleNextPoll() {
    clearPollTimeout();
    if (document.visibilityState !== "visible") {
      return;
    }

    pollTimeout = window.setTimeout(() => {
      void refreshClientRateLimit();
    }, pollIntervalMs);
  }

  const refreshClientRateLimit = async () => {
    if (refreshInFlight) {
      refreshQueued = true;
      return;
    }

    refreshInFlight = true;
    clearPollTimeout();

    try {
      const clientRateLimit = await rateLimit();
      const isLow = isClientRateLimitLow(clientRateLimit);

      updateRateLimitsNavLink(navLink, isLow);
      updateClientRateLimitCells(
        clientRateLimitDom,
        clientResetDom,
        clientRateLimit,
      );
    } catch (error) {
      if (clientRateLimitDom instanceof HTMLElement) {
        clientRateLimitDom.classList.add("text-danger");
      }
      if (clientResetDom instanceof HTMLElement) {
        clientResetDom.classList.add("text-danger");
      }
      log(error);
    } finally {
      refreshInFlight = false;

      if (refreshQueued) {
        refreshQueued = false;
        void refreshClientRateLimit();
        return;
      }

      scheduleNextPoll();
    }
  };

  function refreshClientRateLimitOnActivation() {
    if (document.visibilityState !== "visible") {
      return;
    }

    void refreshClientRateLimit();
  }

  void refreshClientRateLimit();
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") {
      clearPollTimeout();
      return;
    }

    refreshClientRateLimitOnActivation();
  });
  window.addEventListener("focus", () => {
    refreshClientRateLimitOnActivation();
  });
  window.addEventListener("pageshow", (event) => {
    if (event.persisted) {
      refreshClientRateLimitOnActivation();
    }
  });
}

// Parse headers of a response object.
function remainingApiCalls(response) {
  const headers = response.headers;
  if (headers.get("X-RateLimit-Resource") === "core") {
    if (headers.has("X-RateLimit-Remaining")) {
      return Number(headers.get("X-RateLimit-Remaining"));
    }
  }
  return Number.MAX_VALUE;
}

// === htmx swap observation ===
//
// htmx fires one htmx:before:swap / htmx:after:swap pair per response, both on
// the element that issued the request. Only htmx:before:swap carries the swap
// targets, in detail.tasks, and it lists the main swap and the out-of-band
// elements alike.
//
// Three rules follow. Filter by target: every page polls the pending-users
// navigation badge, so an unfiltered listener runs on that timer. Check the
// status: htmx.config.noSwap maps 4xx and 5xx to swap "none", and
// htmx:after:swap still fires for them. Pair the two events through
// detail.ctx: htmx awaits the swap between them, so two responses in flight
// interleave and arrival order does not pair them.

// True when htmx swapped the response behind this event.
function htmxSwapSucceeded(event) {
  const status = event?.detail?.ctx?.response?.status;
  return typeof status === "number" && status < 400;
}

// The element a swap left in the document, or null when it left nothing.
//
// htmx detaches the target of an outerHTML or delete swap before the pair
// closes, and detail.tasks names the detached element. An outerHTML
// replacement keeps the id, so it is reachable by lookup; a delete swap has no
// replacement. Resolving here is what keeps callers off document-wide rescans:
// hx-swap-oob="true" means outerHTML, and the hidden sort/order/page inputs
// carry it on nearly every poll response the app sends.
function resolveHtmxSwapTarget(target) {
  if (target.isConnected) {
    return target;
  }
  return target.id ? document.getElementById(target.id) : null;
}

// Run callback(targets) once per response that swaps at least one element
// matching the predicate. Targets are resolved to what is in the document when
// the callback runs, and a target whose swap left nothing behind is dropped.
function onHtmxSwap(matches, callback) {
  // Keyed by request context, not by arrival order: htmx.swap() awaits the
  // swap tasks between the two events, so a second response can open and
  // close its own pair inside the first one's. A flat queue hands the first
  // htmx:after:swap every target collected so far, which drops a matched
  // target whenever the interleaving response is an error.
  const pending = new WeakMap();

  document.addEventListener("htmx:before:swap", (event) => {
    const ctx = event?.detail?.ctx;
    const tasks = event?.detail?.tasks;
    if (!ctx || !Array.isArray(tasks)) {
      return;
    }
    const targets = pending.get(ctx) ?? [];
    for (const task of tasks) {
      if (task?.target instanceof Element && matches(task.target)) {
        targets.push(task.target);
      }
    }
    if (targets.length > 0) {
      pending.set(ctx, targets);
    }
  });

  document.addEventListener("htmx:after:swap", (event) => {
    const ctx = event?.detail?.ctx;
    const targets = ctx && pending.get(ctx);
    if (!targets) {
      return;
    }
    pending.delete(ctx);
    if (!htmxSwapSucceeded(event)) {
      return;
    }
    const live = targets.map(resolveHtmxSwapTarget).filter(Boolean);
    if (live.length > 0) {
      callback(live);
    }
  });
}

// True when the request behind this event was cancelled rather than failed.
//
// htmx reports an aborted fetch through htmx:error, the same event a network
// failure uses. Requests are aborted routinely: hx-sync lets a filter form
// replace an in-flight poll on the same queue, and htmx applies a 60s default
// timeout. Treating either as a failed load resets panel state that a
// replacement request is already refilling.
function htmxRequestAborted(event) {
  return event?.detail?.error?.name === "AbortError";
}

// === error responses must not move the page around them ===
//
// htmx.config.noSwap suppresses the main content swap and nothing else. htmx
// runs the history update at the top of swap(), applies out-of-band elements
// from the response body, and adopts the response title, none of them gated on
// the status. A whole error page reaches all three: the address bar would show
// the URL that failed, an id collision with the error page would overwrite live
// content, and the tab would be renamed, over content that never changed.
// htmx 2 gated all of this on the swap itself.

document.addEventListener("htmx:before:history:update", (event) => {
  const status = event?.detail?.response?.status;
  if (typeof status === "number" && status >= 400) {
    event.preventDefault();
  }
});

// Registered before any onHtmxSwap listener: application.js loads ahead of
// every script that calls onHtmxSwap, so this empties detail.tasks first and
// the observers see an error response as swapping nothing.
document.addEventListener("htmx:before:swap", (event) => {
  const ctx = event?.detail?.ctx;
  if (!ctx || htmxSwapSucceeded(event)) {
    return;
  }
  ctx.title = "";
  if (Array.isArray(event.detail.tasks)) {
    event.detail.tasks.length = 0;
  }
});
