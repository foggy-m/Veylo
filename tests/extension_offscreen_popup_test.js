'use strict';

// Deterministic DOM/Chrome/timer mocks: no browser, real clipboard or network.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { createDOM, TestEvent } = require('../extension/tests/dom-adapter.cjs');
const extensionDir = path.resolve(__dirname, '..', 'extension');
const read = name => fs.readFileSync(path.join(extensionDir, name), 'utf8');
const plain = value => value == null ? value : JSON.parse(JSON.stringify(value));
const flush = () => new Promise(resolve => setImmediate(resolve));
function deferred() {
  let resolve;
  const promise = new Promise(yes => { resolve = yes; });
  return { promise, resolve };
}
const youtube = 'https://www.youtube.com/watch?v=abcdefghijk';
const xUrl = 'https://x.com/mock/status/123';
const jobId = 'a1'.repeat(16);
const dir = 'D:\\MockDownloads';
const harnesses = [];

function makeTimers() {
  let id = 0;
  const active = new Map();
  const timeouts = new Map();
  return {
    active,
    setInterval(fn, delay) { active.set(++id, { fn, delay }); return id; },
    clearInterval(id) { active.delete(id); },
    setTimeout(fn, delay) { timeouts.set(++id, { fn, delay }); return id; },
    clearTimeout(id) { timeouts.delete(id); },
    async tick(delay) {
      for (const timer of [...active.values()].filter(timer => timer.delay === delay)) await timer.fn();
      await flush();
    }
  };
}

function defaultResponse(message) {
  if (message.type === 'sg-settings-get') return { ok: true, settings: {} };
  if (message.type === 'sg-settings-set') return { ok: true, settings: message.settings };
  if (['sg-history-get', 'sg-history-refresh'].includes(message.type)) return { ok: true, jobs: [] };
  if (message.type === 'sg-bridge-ping') return { ok: true, engineAvailable: true, version: 'mock-version', features: [], login: {} };
  return { ok: true };
}

function loadUi(script, { href = youtube, responder = defaultResponse, runtimeError } = {}) {
  const { document, window } = createDOM();
  if (script === 'popup.js') document.body.innerHTML = read('popup.html').match(/<body[^>]*>([\s\S]*?)<\/body>/i)[1];
  const timers = makeTimers();
  const location = new URL(href || 'https://example.test/');
  const messages = [];
  const controls = { responder, runtimeError };
  const messageListeners = new Set();
  const runtime = {
    id: 'mock-extension', lastError: null,
    getURL: name => `chrome-extension://mock-extension/${name}`,
    onMessage: { addListener(fn) { messageListeners.add(fn); }, removeListener(fn) { messageListeners.delete(fn); } },
    sendMessage(message, callback) {
      messages.push(plain(message));
      Promise.resolve().then(() => controls.responder(plain(message))).then(response => {
        runtime.lastError = controls.runtimeError ? { message: controls.runtimeError } : null;
        try { callback(plain(response)); } finally { runtime.lastError = null; }
      });
    }
  };
  const chrome = { runtime, tabs: { query(options, callback) {
    assert.deepEqual(plain(options), { active: true, currentWindow: true });
    callback(href ? [{ url: href }] : []);
  } } };
  const context = vm.createContext({ ...window, document, location, chrome, URL, URLSearchParams, console, queueMicrotask,
    setInterval: timers.setInterval, clearInterval: timers.clearInterval,
    setTimeout: timers.setTimeout, clearTimeout: timers.clearTimeout });
  context.window = context;
  context.globalThis = context;
  const load = name => vm.runInContext(read(name), context, { filename: name });
  for (const name of ['shared.js', 'icons.js', 'ui.js', script]) load(name);
  document.getElementById('sg-root')?.shadowRoot.querySelectorAll('link').forEach(link => link.dispatchEvent(new TestEvent('load')));
  const harness = { document, timers, location, messages, controls, context, load,
    get(id) { return document.getElementById(id) || document.getElementById('sg-root')?.shadowRoot.querySelector(`#${id}`) || null; },
    async fire(id, type = 'click') {
      const element = this.get(id);
      assert.ok(element, `missing control ${id}`);
      if (type === 'click') element.click();
      else element.dispatchEvent(new TestEvent(type, { bubbles: true }));
      await flush();
    },
    async event(type, options = {}) { context.dispatchEvent(new TestEvent(type, options)); await flush(); }
  };
  harnesses.push(harness);
  return harness;
}

function offscreenHarness({ rejectClipboard = false, noClipboard = false, fallback = true, fallbackThrows = false } = {}) {
  const { document } = createDOM();
  const createElement = document.createElement.bind(document);
  document.createElement = tag => {
    const element = createElement(tag);
    element.select = () => { element.selected = true; };
    return element;
  };
  const writes = [], commands = [], listeners = [];
  document.execCommand = command => {
    commands.push(command);
    assert.equal(document.body.children.length, 1);
    assert.equal(document.body.children[0].selected, true);
    if (fallbackThrows) throw new Error('mock fallback failure');
    return fallback;
  };
  const navigator = noClipboard ? {} : { clipboard: { async writeText(text) {
    writes.push(text);
    if (rejectClipboard) throw new Error('mock clipboard failure');
  } } };
  const chrome = { runtime: { id: 'mock-extension', onMessage: { addListener(fn) { listeners.push(fn); } } } };
  const context = vm.createContext({ chrome, document, navigator });
  vm.runInContext(read('offscreen.js'), context, { filename: 'offscreen.js' });
  return { document, writes, commands, listeners };
}
function clipboard(harness, text) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('mock offscreen timeout')), 1000);
    try {
      assert.equal(harness.listeners[0]({ type: 'sg-clipboard-write', text }, { id: 'mock-extension' }, response => {
        clearTimeout(timer); resolve(plain(response));
      }), true);
    } catch (error) { clearTimeout(timer); reject(error); }
  });
}
function job(state = 'downloading') {
  return { id: jobId, platform: 'x', url: xUrl, title: 'Synthetic X download', media: 'video',
    state, percent: 42, downloadDir: dir, error: state === 'failed' ? 'mock failed job' : '', createdAt: '2026-10-18T00:00:00.000Z' };
}

let cases = 0;
async function check(name, body) {
  try { await body(); cases++; console.log(`PASS ${name}`); }
  catch (error) { throw new Error(name, { cause: error }); }
  finally { while (harnesses.length) await harnesses.pop().event('pagehide', { persisted: false }); }
}

(async () => {
  await check('offscreen writes exact text and ignores unrelated messages', async () => {
    const h = offscreenHarness();
    assert.deepEqual(await clipboard(h, youtube), { ok: true });
    assert.deepEqual(h.writes, [youtube]);
    assert.equal(h.commands.length, 0);
    assert.equal(h.document.body.children.length, 0);
    assert.equal(h.listeners[0]({ type: 'unrelated' }, {}, () => assert.fail()), false);
  });
  await check('offscreen fallback covers denied/missing API and reports false truthfully', async () => {
    for (const option of [{ rejectClipboard: true }, { noClipboard: true }]) {
      const h = offscreenHarness(option);
      assert.deepEqual(await clipboard(h, youtube), { ok: true });
      assert.deepEqual(h.commands, ['copy']);
      assert.equal(h.document.body.children.length, 0);
    }
    const denied = offscreenHarness({ rejectClipboard: true, fallback: false });
    assert.deepEqual(await clipboard(denied, youtube), { ok: false });
    assert.equal(denied.document.body.children.length, 0);
  });
  await check('offscreen removes temporary textarea when fallback throws', async () => {
    const h = offscreenHarness({ rejectClipboard: true, fallbackThrows: true });
    assert.equal((await clipboard(h, youtube)).ok, false);
    assert.equal(h.document.body.children.length, 0);
  });

  await check('popup reads current tab, parses manual input and blocks unsupported URLs', async () => {
    const h = loadUi('popup.js', { href: 'https://youtu.be/abcdefghijk?t=20' });
    await flush();
    assert.equal(h.get('vy-url').value, youtube);
    assert.equal(h.get('vy-send').disabled, false);
    assert.equal(h.get('vy-quality-row').hidden, false);
    assert.equal(h.document.getElementById('url'), null, 'test-only legacy alias must not enter the product');
    h.get('vy-url').value = 'https://mobile.twitter.com/mock/status/123/video/1';
    await h.fire('vy-url', 'input');
    assert.equal(h.get('vy-quality-row').hidden, false);
    assert.equal(h.get('vy-platform').textContent, 'X');
    assert.equal(h.get('vy-kind').textContent, '视频');
    h.get('vy-url').value = 'https://example.test/mock';
    await h.fire('vy-url', 'input');
    assert.equal(h.get('vy-send').disabled, true);
    assert.equal(h.get('vy-copy').disabled, true);
    await h.fire('vy-send');
    assert.equal(h.messages.filter(msg => msg.type === 'sg-deliver').length, 0);
  });

  await check('popup restores task history and retains refresh errors without inventing job failures', async () => {
    for (const terminal of ['completed', 'failed', 'canceled', 'error']) {
      let next = 'downloading';
      const h = loadUi('popup.js', { responder: msg => {
        if (['sg-history-get', 'sg-history-refresh'].includes(msg.type)) return next === 'error'
          ? { ok: false, error: 'mock polling failure' } : { ok: true, jobs: [job(next)] };
        return defaultResponse(msg);
      } });
      await flush();
      assert.ok(h.messages.some(msg => msg.type === 'sg-history-get'));
      assert.match(h.get('vy-jobs').textContent, /42%/);
      assert.equal(h.timers.active.size, 1);
      next = terminal;
      await h.timers.tick(3000);
      if (terminal === 'error') {
        assert.match(h.get('vy-history-error').textContent, /mock polling failure/);
        assert.equal(h.get('vy-jobs').querySelector('.vy-job').dataset.state, 'downloading');
        assert.match(h.get('vy-jobs').textContent, /42%/);
      } else {
        assert.equal(h.get('vy-jobs').querySelector('.vy-job').dataset.state, terminal);
        assert.match(h.get('vy-jobs').textContent, terminal === 'completed' ? /已完成/ : terminal === 'canceled' ? /已取消/ : /mock failed job/);
      }
      await h.event('pagehide', { persisted: true });
      assert.equal(h.timers.active.size, 0);
      await h.event('pageshow', { persisted: true });
      assert.equal(h.timers.active.size, 1);
      await h.event('pagehide', { persisted: false });
      assert.equal(h.timers.active.size, 0);
      assert.equal(h.document.querySelector('.vy-app'), null);
    }
  });

  await check('popup ping distinguishes connected host with missing engine', async () => {
    for (const engineAvailable of [false, true, undefined]) {
      const h = loadUi('popup.js', { responder: msg => msg.type === 'sg-bridge-ping'
        ? { ok: true, version: 'mock-version', engineAvailable } : defaultResponse(msg) });
      await flush(); await h.fire('vy-bridge-check');
      const status = h.get('vy-status');
      if (engineAvailable === false) {
        assert.match(status.textContent, /桥接已连接，但未找到下载内核/);
        assert.doesNotMatch(status.textContent, /就绪|下载完成|任务已接受/);
        assert.equal(status.dataset.tone, 'err');
      } else assert.match(status.textContent, /已连接 · vmock-version/);
    }
    const failure = loadUi('popup.js', { responder: msg => msg.type === 'sg-bridge-ping'
      ? { ok: false, error: 'mock host absent' } : defaultResponse(msg) });
    await flush(); await failure.fire('vy-bridge-check');
    assert.match(failure.get('vy-status').textContent, /YouTube \/ X：mock host absent/);
    assert.match(failure.get('vy-status').textContent, /bilibili：mock host absent/);
    assert.equal(failure.get('vy-status').dataset.tone, 'err');
  });

  await check('popup delivery/copy keeps busy guard and communicates only confirmed outcomes', async () => {
    const gate = deferred();
    const h = loadUi('popup.js', { responder: msg => msg.type === 'sg-deliver' ? gate.promise : defaultResponse(msg) });
    await flush(); await h.fire('vy-send');
    assert.equal(h.get('vy-send').disabled, true);
    assert.equal(h.get('vy-copy').disabled, true);
    await h.fire('vy-send');
    assert.equal(h.messages.filter(msg => msg.type === 'sg-deliver').length, 1);
    gate.resolve({ ok: true, platform: 'youtube', jobId, downloadDir: dir }); await flush();
    assert.match(h.get('vy-status').textContent, /已加入本机任务/);
    assert.equal(h.get('vy-send').disabled, false);
    h.controls.responder = msg => msg.type === 'sg-copy' ? { ok: false, error: 'mock clipboard denied' } : defaultResponse(msg);
    await h.fire('vy-copy');
    assert.equal(h.get('vy-status-text').textContent, 'mock clipboard denied');
    assert.equal(h.get('vy-status').dataset.tone, 'err');
    h.controls.runtimeError = 'mock runtime disconnected';
    await h.fire('vy-send');
    assert.equal(h.get('vy-status-text').textContent, 'mock runtime disconnected');
  });

  await check('popup X dispatch includes selected quality and refreshes accepted task history', async () => {
    let accepted = false, completed = false;
    const h = loadUi('popup.js', { href: xUrl, responder: msg => {
      if (msg.type === 'sg-bridge-ping') return { ok: true, features: [], login: { x: 'guest' } };
      if (msg.type === 'sg-deliver') { accepted = true; return { ok: true, platform: 'x', jobId, downloadDir: dir, cookieStatus: 'guest' }; }
      if (['sg-history-get', 'sg-history-refresh'].includes(msg.type)) return { ok: true, jobs: accepted ? [job(completed ? 'completed' : 'downloading')] : [] };
      return defaultResponse(msg);
    } });
    await flush(); h.get('vy-quality').value = '720'; await h.fire('vy-quality', 'change'); await h.fire('vy-send');
    assert.deepEqual(h.messages.find(msg => msg.type === 'sg-deliver'), {
      type: 'sg-deliver', url: xUrl, mode: 'direct', quality: '720', part: 'current', dir: 'default', media: 'video',
      audioQuality: 'auto', subtitles: 'off', subLangs: 'zh.*,en', metadata: false,
      sponsorblock: 'off', section: '', live: 'off', liveMinutes: '240', playlist: false, items: '', danmaku: 'off', biliSubtitles: 'off',
      embedThumbnail: false, splitChapters: false, downloadArchive: false, limitRate: 'off', concurrentFragments: '1'
    });
    assert.equal(h.get('vy-login'), null, 'Retired login badge must not be rendered');
    assert.match(h.get('vy-jobs').textContent, /42%/);
    completed = true; await h.timers.tick(3000);
    assert.match(h.get('vy-jobs').textContent, /已完成/);
    assert.equal(h.messages.some(msg => msg.type === 'sg-job-status'), false, 'task center uses history refresh, not legacy single-job polling');
  });

  await check('content script prevents duplicate injection and follows SPA navigation inside Shadow DOM', async () => {
    const h = loadUi('content.js');
    await flush();
    h.load('content.js');
    const root = h.document.getElementById('sg-root');
    assert.equal(h.document.querySelectorAll('#sg-root').length, 1);
    assert.equal(root.hidden, false);
    assert.equal(h.document.getElementById('sg-fab'), null, 'content controls must stay inside Shadow DOM');
    await h.fire('sg-fab');
    assert.equal(h.get('sg-panel').hidden, false);
    assert.equal(h.get('vy-platform').textContent, 'YouTube');
    await h.fire('vy-close');
    assert.equal(h.get('sg-panel').hidden, true);
    assert.equal(h.timers.active.size, 1);
    h.location.href = 'https://x.com/home'; await h.event('popstate');
    assert.equal(root.hidden, true);
    h.location.href = xUrl; await h.timers.tick(1000);
    assert.equal(root.hidden, false);
    await h.fire('sg-fab');
    assert.equal(h.get('vy-url').value, xUrl);
    assert.equal(h.get('vy-platform').textContent, 'X');
    assert.equal(h.get('vy-quality-row').hidden, false);
    h.location.href = youtube; await h.event('yt-navigate-finish');
    assert.equal(h.get('vy-url').value, youtube);
    assert.equal(h.get('vy-platform').textContent, 'YouTube');
    assert.equal(h.get('vy-quality-row').hidden, false);
  });

  await check('content copy/delivery failure never renders success and handoff remains explicit', async () => {
    const h = loadUi('content.js', { responder: msg => ['sg-deliver', 'sg-copy'].includes(msg.type)
      ? { ok: false, error: 'mock clipboard denied' } : defaultResponse(msg) });
    await flush(); await h.fire('sg-fab');
    for (const control of ['vy-send', 'vy-copy']) {
      await h.fire(control);
      assert.equal(h.get('vy-status-text').textContent, 'mock clipboard denied');
      assert.doesNotMatch(h.get('vy-status-text').textContent, /已复制|已写入|已加入/);
      assert.equal(h.get('vy-status').dataset.tone, 'err');
      assert.equal(h.get('vy-send').disabled, false);
    }
    h.controls.responder = defaultResponse;
    await h.fire('vy-handoff');
    assert.match(h.get('vy-status-text').textContent, /链接已复制.*FluentYTDL/);
    assert.equal(h.messages.filter(msg => msg.type === 'sg-deliver').at(-1).mode, 'handoff');
    await h.fire('vy-copy');
    assert.equal(h.get('vy-status-text').textContent, '链接已复制');
    h.controls.runtimeError = 'mock extension invalidated';
    await h.fire('vy-send');
    assert.equal(h.get('vy-status-text').textContent, 'mock extension invalidated');
    assert.equal(h.get('vy-status').dataset.tone, 'err');
  });

  await check('content X dispatch is single while busy and failed history remains visible until close', async () => {
    const gate = deferred();
    let accepted = false;
    const h = loadUi('content.js', { href: xUrl, responder: msg => {
      if (msg.type === 'sg-deliver') return gate.promise;
      if (['sg-history-get', 'sg-history-refresh'].includes(msg.type)) return { ok: true, jobs: accepted ? [job('failed')] : [] };
      return defaultResponse(msg);
    } });
    await flush(); await h.fire('sg-fab');
    h.get('vy-quality').value = '480'; await h.fire('vy-quality', 'change');
    await h.fire('vy-send'); await h.fire('vy-send');
    const requests = h.messages.filter(msg => msg.type === 'sg-deliver');
    assert.equal(requests.length, 1);
    assert.equal(requests[0].url, xUrl);
    assert.equal(requests[0].quality, '480');
    assert.equal(requests[0].mode, 'direct');
    accepted = true;
    gate.resolve({ ok: true, platform: 'x', jobId, downloadDir: dir, cookieStatus: 'signed-in' }); await flush();
    assert.match(h.get('vy-jobs').textContent, /mock failed job/);
    assert.equal(h.get('vy-jobs').querySelector('.vy-job').dataset.state, 'failed');
    assert.equal([...h.timers.active.values()].filter(timer => timer.delay === 3000).length, 1);
    await h.fire('vy-close');
    assert.equal([...h.timers.active.values()].filter(timer => timer.delay === 3000).length, 0);
    assert.equal([...h.timers.active.values()].filter(timer => timer.delay === 1000).length, 1);
  });

  console.log(`extension_offscreen_popup_test: ${cases} groups passed (offscreen fallback, popup health/history, content navigation and UI responses)`);
})().catch(error => { console.error(error); process.exitCode = 1; });
