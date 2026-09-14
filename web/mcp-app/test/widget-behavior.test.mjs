import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const widgetPath =
  process.env.WIDGET_HTML ??
  fileURLToPath(new URL("../../../src/vidxp/assets/mcp_app/index.html", import.meta.url));
const html = fs.readFileSync(widgetPath, "utf8");
const script = html.match(/<script>([\s\S]*?)<\/script>/)?.[1];
assert.ok(script, "inline widget script exists");

class FakeNode {
  constructor(tag = "fragment") {
    this.tagName = tag.toUpperCase();
    this.children = [];
    this.listeners = new Map();
    this.style = { setProperty() {} };
    this.dataset = {};
    this.attributes = new Map();
    this.className = "";
    this.textContent = "";
    this.disabled = false;
    this.hidden = false;
    this.id = "";
  }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = [...nodes]; }
  addEventListener(type, callback) { this.listeners.set(type, callback); }
  setAttribute(name, value) { this.attributes.set(name, String(value)); }
  async click() { return this.listeners.get("click")?.({ preventDefault() {} }); }
}

function descendants(node) {
  return [node, ...node.children.flatMap((child) => descendants(child))];
}

function createWidget() {
  const roots = Object.fromEntries(
    ["title", "lede", "content", "notice", "expand"].map((id) => {
      const node = new FakeNode(id === "expand" ? "button" : "div");
      node.id = id;
      return [id, node];
    }),
  );
  const messageListeners = [];
  const posted = [];
  const timers = new Map();
  let nextTimer = 1;
  const parent = { postMessage(message) { posted.push(message); } };
  const document = {
    documentElement: new FakeNode("html"),
    getElementById(id) {
      if (roots[id]) return roots[id];
      return descendants(roots.content).find((node) => node.id === id) ?? null;
    },
    createElement(tag) { return new FakeNode(tag); },
    createDocumentFragment() { return new FakeNode(); },
  };
  const window = {
    parent,
    openai: undefined,
    setTimeout(callback, delay) {
      const id = nextTimer++;
      timers.set(id, { callback, delay });
      return id;
    },
    clearTimeout(id) { timers.delete(id); },
    addEventListener(type, callback) {
      if (type === "message") messageListeners.push(callback);
    },
  };
  vm.runInNewContext(script, {
    window,
    document,
    ResizeObserver: undefined,
    Promise,
    Map,
    Set,
    Number,
    String,
    Array,
    Object,
    Error,
    console,
  });

  const send = (data) => {
    for (const listener of messageListeners) listener({ source: parent, data });
  };
  const toolResult = (structuredContent) => send({
    jsonrpc: "2.0",
    method: "ui/notifications/tool-result",
    params: { structuredContent, content: [] },
  });
  const findButton = (label) => descendants(roots.content).find(
    (node) => node.tagName === "BUTTON" && node.textContent === label,
  );
  const toolCalls = () => posted.filter((message) => message.method === "tools/call");
  const pollTimers = () => [...timers.values()].filter(({ delay }) => delay < 15000);
  const runPollTimers = async () => {
    const entries = [...timers.entries()].filter(([, { delay }]) => delay < 15000);
    for (const [id, { callback }] of entries) {
      timers.delete(id);
      await callback();
    }
  };
  return { roots, posted, timers, send, toolResult, findButton, toolCalls, pollTimers, runPollTimers };
}

const progress = {
  view: "job",
  job_id: "job-1",
  kind: "index",
  state: "running",
  terminal: false,
  poll_after_seconds: 1,
  progress: { stage: "speech", current: 1, total: 2 },
};
const emptyLibrary = { items: [], total: 0, next_cursor: null };

test("empty MediaPage renders the library empty state without swallowing unrelated item results", () => {
  const widget = createWidget();
  widget.toolResult(emptyLibrary);
  assert.equal(widget.roots.title.textContent, "Select a video");
  assert.equal(widget.roots.lede.textContent, "No registered videos yet. Upload one first.");

  widget.toolResult({ items: [], status: "not a media page" });
  assert.equal(widget.roots.title.textContent, "VidXP");
  assert.equal(widget.roots.lede.textContent, "This tool result does not include an interactive view.");
});

test("initial MediaPage with an unrecoverable filter context does not offer unsafe pagination", () => {
  const widget = createWidget();
  widget.toolResult({
    items: [{ media_id: "media-1", original_filename: "video.mp4", state: "ready" }],
    total: 2,
    next_cursor: "opaque-filter-bound-cursor",
  });
  assert.equal(widget.roots.title.textContent, "Select a video");
  assert.equal(widget.findButton("Load more"), undefined);
});

test("leaving progress cancels its timer", async () => {
  const widget = createWidget();
  widget.toolResult(progress);
  assert.equal(widget.pollTimers().length, 1);
  widget.toolResult(emptyLibrary);
  assert.equal(widget.pollTimers().length, 0);
  await widget.runPollTimers();
  assert.equal(widget.toolCalls().length, 0);
});

test("an in-flight stale poll cannot replace a newer view", async () => {
  const widget = createWidget();
  widget.toolResult({ ...progress, terminal: true });
  const refresh = widget.findButton("Refresh status");
  const click = refresh.click();
  await Promise.resolve();
  const call = widget.toolCalls().at(-1);
  assert.ok(call, "refresh issued get_job_status");
  widget.toolResult(emptyLibrary);
  widget.send({
    jsonrpc: "2.0",
    id: call.id,
    result: { structuredContent: { ...progress, state: "completed", terminal: true }, content: [] },
  });
  await click;
  assert.equal(widget.roots.title.textContent, "Select a video");
});

test("resource teardown cancels scheduled and in-flight progress work", async () => {
  const widget = createWidget();
  widget.toolResult(progress);
  widget.send({ jsonrpc: "2.0", id: 999, method: "ui/resource-teardown" });
  assert.equal(widget.pollTimers().length, 0);
  await widget.runPollTimers();
  assert.equal(widget.toolCalls().length, 0);
  assert.ok(widget.posted.some((message) => message.id === 999 && message.result));
});

test("failed manual refresh re-enables the button for retry", async () => {
  const widget = createWidget();
  widget.toolResult({ ...progress, terminal: true });
  const refresh = widget.findButton("Refresh status");
  const firstClick = refresh.click();
  await Promise.resolve();
  const firstCall = widget.toolCalls().at(-1);
  assert.equal(refresh.disabled, true);
  widget.send({ jsonrpc: "2.0", id: firstCall.id, error: { message: "network down" } });
  await firstClick;
  assert.equal(refresh.disabled, false);
  assert.equal(widget.roots.notice.textContent, "Could not refresh job status.");

  const secondClick = refresh.click();
  await Promise.resolve();
  const secondCall = widget.toolCalls().at(-1);
  assert.notEqual(secondCall.id, firstCall.id);
  widget.send({
    jsonrpc: "2.0",
    id: secondCall.id,
    result: { structuredContent: { ...progress, terminal: true }, content: [] },
  });
  await secondClick;
});
