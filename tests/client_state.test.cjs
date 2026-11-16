const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const ts = require("../frontend/node_modules/typescript");

const root = path.resolve(__dirname, "..");
const compiled = new Map();
function load(relative, mocks = {}, globals = {}) {
  if (!compiled.has(relative)) {
    compiled.set(relative, ts.transpileModule(fs.readFileSync(path.join(root, relative), "utf8"), {
      compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020, jsx: ts.JsxEmit.React },
    }).outputText);
  }
  const exports = {};
  vm.runInNewContext(compiled.get(relative), {
    exports, require: (name) => {
      if (!(name in mocks)) throw new Error("Unexpected import: " + name);
      return mocks[name];
    },
    Error, URL, setTimeout, clearTimeout, console, ...globals,
  }, { filename: relative });
  return exports;
}
const plain = (value) => JSON.parse(JSON.stringify(value));
const turn = () => new Promise((resolve) => setImmediate(resolve));

function indexedStorage(initial = []) {
  const records = new Map(initial.map((record) => [record.id, structuredClone(record)]));
  const control = { abortWrites: false, pauseCommits: false, commits: [], successes: 0 };
  function transaction(_store, mode) {
    const draft = new Map(Array.from(records, ([key, value]) => [key, structuredClone(value)]));
    const t = {
      error: null,
      abort() {
        t.error = new Error("QuotaExceededError: transaction aborted");
        setImmediate(() => t.onabort?.());
      },
      objectStore() {
        function request(action) {
          const req = { result: undefined, error: null };
          setImmediate(() => {
            req.result = action();
            req.onsuccess?.();
            control.successes++;
            const finish = () => {
              if (mode === "readwrite" && control.abortWrites) { t.abort(); return; }
              if (mode === "readwrite") {
                records.clear();
                for (const [key, value] of draft) records.set(key, value);
              }
              t.oncomplete?.();
            };
            if (mode === "readwrite" && control.pauseCommits) control.commits.push(finish);
            else setImmediate(finish);
          });
          return req;
        }
        return {
          getAll: () => request(() => Array.from(draft.values())),
          put: (value) => request(() => { draft.set(value.id, structuredClone(value)); return value.id; }),
          delete: (id) => request(() => { draft.delete(id); }),
        };
      },
    };
    return t;
  }
  const db = { transaction, close() {} };
  const indexedDB = {
    open() {
      const req = { result: db };
      setImmediate(() => req.onsuccess?.());
      return req;
    },
  };
  return { indexedDB, records, control };
}

const scope = { apiOrigin: "https://api.example.test", userId: 1, workspaceId: 10 };
function queueHarness(storage = indexedStorage(), online = true, supported = true) {
  return {
    ...storage,
    q: load("frontend/lib/offlineQueue.ts", {}, {
      window: supported ? { indexedDB: storage.indexedDB } : {},
      indexedDB: storage.indexedDB,
      navigator: { onLine: online },
    }),
  };
}
const network = async () => { throw new Error("network down"); };
const record = (id, owner = scope) => ({
  id, scope: owner, method: "post", url: "/field/cases/1/events",
  body: { detail: id, client_action_id: id }, label: id, createdAt: 1, state: "pending",
});

test("outbox confirms only committed writes, rejects quota aborts and unavailable storage", async () => {
  const h = queueHarness();
  h.control.pauseCommits = true;
  let resolved = false;
  let requests = 0;
  const sending = h.q.send(scope, async () => { requests++; }, "post", "/field/cases/1/events", { detail: "keep" }, "note")
    .then(() => { resolved = true; });
  for (let i = 0; i < 20 && !h.control.commits.length; i++) await turn();
  assert.equal(h.control.commits.length, 1);
  assert.equal(resolved, false);
  assert.equal(requests, 0, "network must not start before durable commit");
  h.control.pauseCommits = false;
  h.control.commits.shift()();
  await sending;
  assert.equal(requests, 1);
  assert.equal(resolved, true);

  const failing = queueHarness();
  failing.control.abortWrites = true;
  let attempted = false;
  await assert.rejects(failing.q.send(scope, async () => { attempted = true; }, "post", "/field/cases/1/events", {}, "note"), /Quota/);
  assert.equal(attempted, false);
  assert.equal(failing.records.size, 0);
  const unavailable = queueHarness(undefined, true, false);
  await assert.rejects(unavailable.q.send(scope, network, "post", "/field/cases/1/events", {}, "note"), /chưa được lưu/);
});

test("transient HTTP failures retain every queued action and its idempotency key", async () => {
  for (const status of [401, 408, 429, 500, 502, 503]) {
    const h = queueHarness(indexedStorage([record("a"), record("b")]));
    const ids = [];
    const result = await h.q.flush(scope, async (_m, _u, body) => {
      ids.push(body.client_action_id);
      throw { response: { status } };
    });
    assert.deepEqual(plain(result), { sent: 0, left: 2, rejected: 0 }, "status " + status);
    assert.deepEqual(ids, ["a"], "later actions must wait behind a transient failure");
    assert.deepEqual(Array.from(h.records.keys()), ["a", "b"]);
  }
});

test("outbox survives reload and isolates API, account, workspace, and legacy records", async () => {
  const owners = [scope, { ...scope, userId: 2 }, { ...scope, workspaceId: 11 }, { ...scope, apiOrigin: "https://other.test" }];
  const legacy = record("legacy");
  delete legacy.scope;
  const storage = indexedStorage([...owners.map((owner, i) => record("a" + i, owner)), legacy]);
  const h = queueHarness(storage);
  const called = [];
  await h.q.flush(scope, async (_m, _u, body) => called.push(body.client_action_id));
  assert.deepEqual(called, ["a0"]);
  const restarted = queueHarness(storage);
  for (const owner of owners.slice(1)) assert.equal((await restarted.q.pending(owner)).length, 1);
  assert.equal(await restarted.q.legacyPendingCount(), 1);
  assert.equal(storage.records.size, 4, "other users' edits must survive a session change");
});

test("business rejections are reviewable, retry keeps the original action ID, discard is scoped", async () => {
  const h = queueHarness();
  await assert.rejects(h.q.send(scope, async () => {
    throw { response: { status: 409, data: { detail: "case closed" } } };
  }, "post", "/field/cases/1/events", { detail: "measurement" }, "note"), /case closed/);
  const [rejected] = await h.q.rejected(scope);
  assert.equal(rejected.body.detail, "measurement");
  assert.equal((await h.q.pending(scope)).length, 0);
  await h.q.discardRejected({ ...scope, userId: 2 }, rejected.id);
  assert.equal(h.records.size, 1);
  await h.q.retryRejected(scope, rejected.id);
  const received = [];
  await h.q.flush(scope, async (_m, _u, body) => received.push(body.client_action_id));
  assert.deepEqual(received, [rejected.body.client_action_id]);
  assert.equal(h.records.size, 0);

  h.records.set("discard", { ...record("discard"), state: "rejected", error: "denied" });
  await h.q.discardRejected(scope, "discard");
  assert.equal(h.records.size, 0);
});

test("concurrent syncs deliver once and new sends preserve queue order", async () => {
  const h = queueHarness(indexedStorage([record("first")]));
  let release;
  let started;
  const began = new Promise((resolve) => { started = resolve; });
  const calls = [];
  const sender = async (_m, _u, body) => {
    calls.push(body.client_action_id);
    started();
    await new Promise((resolve) => { release = resolve; });
  };
  const first = h.q.flush(scope, sender);
  await began;
  const second = h.q.flush(scope, sender);
  await turn();
  assert.deepEqual(calls, ["first"]);
  release();
  await Promise.all([first, second]);
  assert.deepEqual(calls, ["first"]);

  const offline = queueHarness(undefined, false);
  await offline.q.send(scope, network, "post", "/field/cases/1/events", { detail: "one" }, "one");
  await offline.q.send(scope, network, "post", "/field/cases/1/events", { detail: "two" }, "two");
  const restarted = queueHarness(offline);
  const details = [];
  await restarted.q.flush(scope, async (_m, _u, body) => details.push(body.detail));
  assert.deepEqual(details, ["one", "two"]);
});

function mobileHarness({ initial = {}, fetch: fetcher, secureFailure = false } = {}) {
  const local = new Map(Object.entries(initial));
  const secure = new Map();
  const asyncStorage = {
    getItem: async (key) => local.get(key) ?? null,
    setItem: async (key, value) => { local.set(key, value); },
    removeItem: async (key) => { local.delete(key); },
    multiRemove: async (keys) => { for (const key of keys) local.delete(key); },
  };
  const secureStore = {
    isAvailableAsync: async () => true,
    getItemAsync: async (key) => secure.get(key) ?? null,
    setItemAsync: async (key, value) => {
      if (secureFailure) throw new Error("secure storage unavailable");
      secure.set(key, value);
    },
    deleteItemAsync: async (key) => { secure.delete(key); },
  };
  const origins = load("mobile/lib/offlineOrigin.ts", {
    "react-native": { Platform: { OS: "android" } }, "expo-secure-store": secureStore,
  });
  const connections = [];
  class Xhr {
    constructor() { this.responseText = ""; this.status = 200; connections.push(this); }
    open() {}
    setRequestHeader() {}
    send() {}
    abort() { this.aborted = true; this.onabort?.(); }
    progress(text) { this.responseText += text; this.onprogress?.(); }
  }
  const api = load("mobile/lib/api.ts", {
    "@react-native-async-storage/async-storage": { __esModule: true, default: asyncStorage },
    "react-native": { Platform: { OS: "android" } },
    "expo-secure-store": secureStore,
    "./offlineOrigin": origins,
  }, { XMLHttpRequest: Xhr, fetch: fetcher ?? (async () => ({ ok: true, status: 200, json: async () => [] })) });
  return { api, origins, secureStore, secure, local, connections };
}
const user = { id: 1, email: "one@example.test", role: "user", created_at: "2026-10-21" };
function callbacks(events) {
  return { onToken: (token) => events.push(["token", token]), onDone: (sources, id) => events.push(["done", plain(sources), id]), onError: (error) => events.push(["error", error]) };
}
async function connection(h) {
  for (let i = 0; i < 20 && !h.connections.length; i++) await turn();
  assert.equal(h.connections.length, 1);
  return h.connections[0];
}

test("SSE handles every chunk boundary, unicode, CRLF and final event without newline", async () => {
  const payload = 'data: ' + JSON.stringify({ token: "Điện 🔌" }) + "\r\n\r\n" +
    'data: ' + JSON.stringify({ done: true, sources: ["sop"], message_id: 7 });
  for (let split = 0; split <= payload.length; split++) {
    const h = mobileHarness();
    const events = [];
    h.api.streamChat(1, "question", callbacks(events));
    const xhr = await connection(h);
    xhr.progress(payload.slice(0, split));
    xhr.progress(payload.slice(split));
    xhr.onload();
    assert.deepEqual(events, [["token", "Điện 🔌"], ["done", ["sop"], 7]], "split " + split);
  }
});

test("SSE EOF, malformed JSON, HTTP failure, timeout, and cancel each settle once", async () => {
  for (const scenario of ["eof", "malformed", "http", "timeout", "cancel"]) {
    const h = mobileHarness();
    const events = [];
    const cancel = h.api.streamChat(1, "question", callbacks(events));
    const xhr = await connection(h);
    if (scenario === "malformed") xhr.progress("data: {broken}\n");
    if (scenario === "http") { xhr.status = 503; xhr.responseText = JSON.stringify({ detail: "busy" }); }
    if (scenario === "timeout") xhr.ontimeout();
    if (scenario === "cancel") cancel();
    xhr.onload();
    xhr.onerror();
    assert.equal(events.length, 1, scenario);
    assert.equal(events[0][0], "error", scenario);
  }
  const h = mobileHarness();
  const events = [];
  const cancel = h.api.streamChat(1, "question", callbacks(events));
  cancel();
  await turn();
  assert.equal(h.connections.length, 0, "cancel before setup must not start a request");
  assert.equal(events.length, 1);
});

test("native tokens migrate only after secure persistence and 401 notifies auth subscribers", async () => {
  const h = mobileHarness({
    initial: { rag_token: "legacy-token", rag_user: JSON.stringify(user) },
    fetch: async () => ({ status: 401, ok: false }),
  });
  assert.equal(await h.api.getToken(), "legacy-token");
  assert.equal(h.local.has("rag_token"), false);
  assert.equal(h.secure.get("rag_token"), "legacy-token");
  const states = [];
  h.api.subscribeAuth((next) => states.push(next));
  await assert.rejects(h.api.listFieldCases(10), /Phiên đăng nhập/);
  assert.deepEqual(states, [null]);
  assert.equal(await h.api.getToken(), null);

  const failing = mobileHarness({ initial: { rag_token: "keep-until-migrated" }, secureFailure: true });
  await assert.rejects(failing.api.getToken(), /secure storage/);
  assert.equal(failing.local.get("rag_token"), "keep-until-migrated");
});

test("a late 401 cannot clear a newer login", async () => {
  let received;
  let release;
  const started = new Promise((resolve) => { received = resolve; });
  const h = mobileHarness({ fetch: async () => {
    received();
    return new Promise((resolve) => { release = resolve; });
  } });
  await h.api.saveAuth("old", user);
  const pendingRequest = h.api.listFieldCases(10);
  await started;
  await h.api.saveAuth("new", { ...user, id: 2 });
  const states = [];
  h.api.subscribeAuth((next) => states.push(next));
  release({ status: 401, ok: false });
  await assert.rejects(pendingRequest, /Phiên đăng nhập/);
  assert.equal(await h.api.getToken(), "new");
  assert.deepEqual(states, []);
});

test("cache purge removes cases stored only in the overview and preserves pending edits", async () => {
  const h = mobileHarness();
  const offline = load("mobile/lib/fieldOffline.ts", {
    "expo-secure-store": h.secureStore,
    "react-native": { Platform: { OS: "android" } },
    "./api": h.api,
    "./offlineOrigin": h.origins,
  });
  const workspace = { id: 10, name: "team", role: "owner", member_count: 1 };
  await offline.cacheFieldOverview(1, workspace, [{ id: 8, workspace_id: 10, steps: [], events: [] }]);
  await offline.enqueueFieldAction(1, offline.prepareFieldAction({ kind: "note", case_id: 8, detail: "keep" }));
  await offline.removeCachedFieldCase(1, 8);
  assert.equal((await offline.getCachedActiveFieldOverview(1)).cases.length, 0);
  assert.equal(await offline.getCachedFieldCase(1, 8), null);
  assert.equal(await offline.getPendingFieldActionCount(1), 1);
});

test("case detail purges cache on HTTP 403/404 instead of using the offline fallback", async () => {
  for (const status of [403, 404]) {
    const h = mobileHarness();
    const calls = [];
    const api = { ...h.api, loadUser: async () => user,
      getFieldCase: async () => { throw new h.api.ApiError("revoked", status); } };
    let focus;
    const states = [];
    const react = {
      __esModule: true,
      default: { createElement: () => null },
      useCallback: (fn) => fn,
      useState: (initial) => [initial, (next) => states.push(next)],
    };
    const screen = load("mobile/screens/FieldCaseDetailScreen.tsx", {
      react,
      "react-native": { StyleSheet: { create: (styles) => styles } },
      "@react-navigation/native": { useFocusEffect: (fn) => { focus = fn; } },
      "@expo/vector-icons": { Ionicons: {} },
      "../lib/api": api,
      "../lib/fieldOffline": {
        isSecureOfflineStorageAvailable: async () => true,
        syncPendingFieldActions: async () => ({ pending: 1, errors: [] }),
        getRejectedFieldActions: async () => [],
        removeCachedFieldCase: async (...args) => { calls.push(args); },
        getCachedFieldCase: async () => { throw new Error("must not fall back after revocation"); },
      },
      "../lib/text": { markdownToPlain: (value) => value },
      "../lib/theme": { colors: {}, radius: {} },
      "../components/GradientButton": { __esModule: true, default: () => null },
      "../components/EvidencePanel": { __esModule: true, default: () => null },
      "../components/CasePhotosSection": { __esModule: true, default: () => null },
    });
    screen.default({ route: { params: { caseId: 8 } }, navigation: {} });
    focus();
    for (let i = 0; i < 20 && !calls.length; i++) await turn();
    assert.deepEqual(calls, [[1, 8]], "status " + status);
    assert.ok(states.includes(null), "clear previously displayed detail");
  }
});

function offlineHarness(h) {
  return load("mobile/lib/fieldOffline.ts", {
    "expo-secure-store": h.secureStore, "react-native": { Platform: { OS: "android" } },
    "./api": h.api, "./offlineOrigin": h.origins,
  });
}

test("changing backend cannot read or replay the original backend's offline data", async () => {
  const requests = [];
  const h = mobileHarness({ fetch: async (_url, options) => {
    requests.push(options);
    return { ok: true, status: 200, json: async () => ({}) };
  } });
  const originalUrl = await h.api.getApiUrl();
  await h.api.saveAuth("original", user);
  const offline = offlineHarness(h);
  await offline.enqueueFieldAction(1, offline.prepareFieldAction({ kind: "note", case_id: 8, detail: "original backend" }));
  await h.api.setApiUrl("https://other.example.test");
  await h.api.saveAuth("other", user); // Numeric IDs may collide across servers.
  assert.equal(await offline.isSecureOfflineStorageAvailable(), false);
  await assert.rejects(offline.syncPendingFieldActions(1), /máy chủ khác/);
  await assert.rejects(offline.getPendingFieldActionCount(1), /máy chủ khác/);
  assert.equal(requests.length, 0);
  await h.api.setApiUrl(originalUrl);
  await h.api.saveAuth("original-again", user);
  assert.equal(await offline.isSecureOfflineStorageAvailable(), true);
  assert.equal(await offline.getPendingFieldActionCount(1), 1, "returning to the original server recovers the same pending edit");
});

test("captured mobile session rejects account changes and sync preserves remaining edits", async () => {
  const requests = [];
  let h;
  h = mobileHarness({ fetch: async (_url, options) => {
    requests.push(options.headers.Authorization);
    await h.api.saveAuth("new-owner", { ...user, id: 2 });
    return { ok: true, status: 200, json: async () => ({}) };
  } });
  await h.api.saveAuth("old-owner", user);
  const stale = await h.api.captureSession(1);
  await h.api.saveAuth("new-owner", { ...user, id: 2 });
  await assert.rejects(h.api.updateFieldCase(8, { status: "waiting" }, stale), /thay đổi/);
  assert.equal(requests.length, 0);

  await h.api.saveAuth("old-owner", user);
  const offline = offlineHarness(h);
  for (const detail of ["first", "second"]) {
    await offline.enqueueFieldAction(1, offline.prepareFieldAction({ kind: "note", case_id: 8, detail }));
  }
  const result = await offline.syncPendingFieldActions(1);
  assert.deepEqual(requests, ["Bearer old-owner"], "never send the old owner's edits with the next owner's token");
  assert.equal(result.pending, 1);
  assert.equal(await offline.getPendingFieldActionCount(1), 1);
});

function webHarness(fetcher) {
  const local = new Map([["rag_session", "current"], ["rag_user", JSON.stringify(user)]]);
  const location = { pathname: "/field", href: "" };
  const hooks = {};
  const fetches = [];
  const instance = {
    defaults: { headers: { common: {} } },
    interceptors: {
      request: { use(ok) { hooks.request = ok; } },
      response: { use(_ok, fail) { hooks.reject = fail; } },
    },
  };
  let created;
  const axios = { create: (options) => { created = options; return instance; }, isAxiosError: () => false };
  const web = load("frontend/lib/api.ts", { axios: { __esModule: true, default: axios } }, {
    window: { location },
    localStorage: {
      getItem: (key) => local.get(key) ?? null,
      setItem: (key, value) => local.set(key, value),
      removeItem: (key) => local.delete(key),
    },
    process: { env: { NEXT_PUBLIC_API_URL: "https://api.example.test" } },
    fetch: async (url, options) => { fetches.push([url, options]); return fetcher(url, options, local); },
  });
  return { web, local, location, hooks, fetches, created: () => created };
}

test("web Axios and streaming 401s only clear their own session", async () => {
  const h = webHarness(async (url, _options, local) => {
    if (url.endsWith("/auth/logout")) return { ok: true, status: 200, json: async () => ({}) };
    // The login changes while this streaming request is in flight.
    local.set("rag_session", "next");
    return { ok: false, status: 401, json: async () => ({ detail: "expired" }) };
  });
  await assert.rejects(h.hooks.reject({ response: { status: 401 }, config: { url: "/field/cases", sessionEpoch: "previous" } }));
  assert.equal(h.local.get("rag_session"), "current");
  const events = [];
  await h.web.streamChat(1, "question", callbacks(events));
  assert.equal(h.local.get("rag_session"), "next", "stale streaming 401 must leave the next session intact");
  await assert.rejects(h.hooks.reject({ response: { status: 401 }, config: { url: "/field/cases", sessionEpoch: "next" } }));
  assert.equal(h.local.has("rag_session"), false);
  assert.equal(h.local.has("rag_user"), false);
  assert.equal(h.location.href, "/login");
  assert.ok(h.fetches.some(([url]) => url.endsWith("/auth/logout")), "an expired session also clears its cookie");
});

test("web session lives in an httpOnly cookie: no bearer token is stored or sent", async () => {
  const h = webHarness(async () => ({ ok: false, status: 500, json: async () => ({ detail: "x" }) }));
  const options = h.created();
  assert.equal(options.withCredentials, true, "axios sends the session cookie");
  assert.equal(options.headers["X-Requested-With"], "fieldops", "CSRF header on every request");
  assert.equal(options.headers["X-Auth-Mode"], "cookie", "login responses set a cookie instead of returning a token");
  const config = h.hooks.request({ headers: {} });
  assert.equal(config.sessionEpoch, "current");
  await h.web.streamChat(1, "question", callbacks([]));
  const [, streamOptions] = h.fetches[0];
  assert.equal(streamOptions.credentials, "include");
  assert.equal(streamOptions.headers.Authorization, undefined);
  assert.equal(streamOptions.headers["X-Requested-With"], "fieldops");
  h.local.set("rag_token", "legacy-token");
  assert.equal(h.web.takeLegacyToken(), "legacy-token");
  assert.equal(h.local.has("rag_token"), false, "the legacy localStorage token is removed after migration");
});

// ---------------------------------------------------------------- audit 2026-11-16 regressions
test("fresh install that switches to the real server keeps offline mode (no binding to the dev default)", async () => {
  const h = mobileHarness();
  const offline = offlineHarness(h);
  assert.equal(await offline.isSecureOfflineStorageAvailable(), true, "reading must not bind the offline origin");
  await h.api.setApiUrl("https://fieldops.example.test");
  await h.api.saveAuth("real", user);
  assert.equal(await offline.isSecureOfflineStorageAvailable(), true);
  await offline.enqueueFieldAction(1, offline.prepareFieldAction({ kind: "note", case_id: 8, detail: "on site" }));
  assert.equal(await offline.getPendingFieldActionCount(1), 1);
});

test("device locked to the dev server by an old version recovers offline mode unless it holds that user's data", async () => {
  const legacyOrigin = "fieldops.offline.v1.api_origin";
  // Old version bound the default dev origin on a mere read; this user never stored anything.
  const empty = mobileHarness();
  empty.secure.set(legacyOrigin, "http://100.121.99.57:8001");
  await empty.api.setApiUrl("https://fieldops.example.test");
  await empty.api.saveAuth("real", user);
  assert.equal(await offlineHarness(empty).isSecureOfflineStorageAvailable(), true);

  // Same legacy binding, but this user has queued edits there: they stay isolated.
  const holding = mobileHarness();
  holding.secure.set(legacyOrigin, "http://100.121.99.57:8001");
  holding.secure.set("fieldops.offline.v1.1.actions.manifest", JSON.stringify({ generation: "g", chunks: 1 }));
  await holding.api.setApiUrl("https://fieldops.example.test");
  await holding.api.saveAuth("real", user);
  assert.equal(await offlineHarness(holding).isSecureOfflineStorageAvailable(), false);
});

test("web outbox moves an action that keeps failing with 5xx to rejected and unblocks later ones", async () => {
  const h = queueHarness(indexedStorage([record("a"), { ...record("b"), createdAt: 2 }]));
  const sent = [];
  const sender = async (_m, _u, body) => {
    if (body.client_action_id === "a") throw { response: { status: 500 } };
    sent.push(body.client_action_id);
  };
  for (let i = 1; i < h.q.MAX_SERVER_FAILURES; i++) {
    assert.deepEqual(plain(await h.q.flush(scope, sender)), { sent: 0, left: 2, rejected: 0 }, "attempt " + i);
  }
  const final = await h.q.flush(scope, sender);
  assert.deepEqual(plain(final), { sent: 1, left: 0, rejected: 1 });
  assert.deepEqual(sent, ["b"]);
  const [rejectedAction] = await h.q.rejected(scope);
  assert.equal(rejectedAction.id, "a");
  await h.q.retryRejected(scope, "a");
  assert.equal((await h.q.pending(scope))[0].serverFailures, 0, "manual retry starts a fresh count");
});

test("mobile sync moves an action that keeps failing with 5xx to rejected and sends later edits", async () => {
  const sent = [];
  const h = mobileHarness({ fetch: async (_url, options) => {
    const body = JSON.parse(options.body);
    if (body.detail === "poison") return { ok: false, status: 500, json: async () => ({ detail: "boom" }) };
    sent.push(body.detail);
    return { ok: true, status: 200, json: async () => ({}) };
  } });
  await h.api.saveAuth("tech", user);
  const offline = offlineHarness(h);
  for (const detail of ["poison", "later"]) {
    await offline.enqueueFieldAction(1, offline.prepareFieldAction({ kind: "note", case_id: 8, detail }));
  }
  for (let i = 1; i < offline.MAX_SERVER_FAILURES; i++) {
    const result = await offline.syncPendingFieldActions(1);
    assert.equal(result.pending, 2, "attempt " + i);
  }
  const result = await offline.syncPendingFieldActions(1);
  assert.equal(result.rejected, 1);
  assert.equal(result.pending, 0);
  assert.ok(sent.includes("later"));
});
