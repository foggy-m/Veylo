'use strict';

// Chrome APIs are fully mocked. No Native executable, clipboard or network is used.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const extensionDir = path.resolve(__dirname, '..', 'extension');
const read = name => fs.readFileSync(path.join(extensionDir, name), 'utf8');
const plain = value => value == null ? value : JSON.parse(JSON.stringify(value));
const flush = () => new Promise(resolve => setImmediate(resolve));
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
const videoUrl = 'https://www.youtube.com/watch?v=abcdefghijk';
const xUrl = 'https://x.com/mock/status/123';
const jobId = 'a1'.repeat(16);
const oldJob = { id: 'b2'.repeat(16), url: 'https://x.com/mock/status/456', downloadDir: 'D:\\PreviousMockDownloads' };
const nativeDownloadOptions = {
  quality: 'best', part: 'current', dir: '', media: 'video', audioQuality: 'auto',
  subtitles: 'off', subLangs: 'zh.*,en', metadata: false
};
const downloadOptions = {
  ...nativeDownloadOptions, sponsorblock: 'off', section: '', playlist: false, items: '', live: 'off',
  embedThumbnail: false, splitChapters: false, downloadArchive: false, limitRate: 'off', concurrentFragments: 'auto'
};
function event() {
  const listeners = [];
  return { listeners, addListener(listener) { listeners.push(listener); } };
}

function createBackgroundHarness(options = {}) {
  const controls = {
    nativeResult: { ok: true, jobId, downloadDir: 'D:\\MockDownloads', cookieStatus: 'guest' },
    clipboardResult: { ok: true }, contexts: [], ...options
  };
  const store = controls.store || {};
  const messages = [], sets = [], creates = [], nativeCalls = [], contextCalls = [];
  const actionCalls = [], alarmCalls = [], menuCalls = [], notificationCalls = [];
  const events = {
    message: event(), installed: event(), startup: event(), alarm: event(), menuClicked: event(),
    notificationClicked: event(), notificationButtonClicked: event(), permissionAdded: event()
  };
  const listeners = events.message.listeners;
  const runtime = {
    id: 'mock-extension',
    lastError: null,
    getURL: name => `chrome-extension://mock-extension/${name}`,
    async getContexts(filter) {
      contextCalls.push(plain(filter));
      if (controls.getContexts) return controls.getContexts(contextCalls.length);
      return plain(controls.contexts);
    },
    async sendMessage(message) {
      messages.push(plain(message));
      if (controls.clipboardError) throw new Error(controls.clipboardError);
      return plain(controls.clipboardResult);
    },
    sendNativeMessage(host, message, callback) {
      nativeCalls.push({ host, message: plain(message) });
      queueMicrotask(() => {
        if (controls.nativeError) {
          runtime.lastError = { message: controls.nativeError };
          try { callback(undefined); } finally { runtime.lastError = null; }
        } else if (controls.strictLegacy && ['yt-download', 'bili-download'].includes(message.action)
          && Object.keys(message).some(key => !['action', 'url', 'quality', 'part', 'dir', 'media', 'audioQuality', 'subtitles', 'subLangs', 'metadata'].includes(key))) {
          callback({ ok: false, error: 'mock strict legacy: unsupported download fields' });
        } else callback(plain(controls.nativeResult));
      });
    },
    onMessage: events.message,
    onInstalled: events.installed,
    onStartup: events.startup
  };
  const chrome = {
    runtime,
    action: {
      async setBadgeBackgroundColor(value) { actionCalls.push({ method: 'setBadgeBackgroundColor', value: plain(value) }); },
      async setBadgeText(value) { actionCalls.push({ method: 'setBadgeText', value: plain(value) }); },
      async setTitle(value) { actionCalls.push({ method: 'setTitle', value: plain(value) }); }
    },
    alarms: {
      async create(name, info) { alarmCalls.push({ method: 'create', name, info: plain(info) }); },
      async clear(name) { alarmCalls.push({ method: 'clear', name }); return true; },
      onAlarm: events.alarm
    },
    contextMenus: {
      removeAll(callback) { menuCalls.push({ method: 'removeAll' }); callback(); },
      create(info, callback) { menuCalls.push({ method: 'create', info: plain(info) }); callback(); return info.id; },
      onClicked: events.menuClicked
    },
    permissions: {
      async contains() { return controls.notificationPermission === true; },
      onAdded: events.permissionAdded
    },
    notifications: {
      async create(id, info) { notificationCalls.push({ id, info: plain(info) }); return id; },
      onClicked: events.notificationClicked,
      onButtonClicked: events.notificationButtonClicked
    },
    offscreen: {
      async createDocument(options) {
        creates.push(plain(options));
        if (controls.createDocument) await controls.createDocument(creates.length);
        controls.contexts = [{ contextType: 'OFFSCREEN_DOCUMENT', documentUrl: runtime.getURL('offscreen.html') }];
      }
    },
    storage: { local: {
      async set(value) {
        if (controls.storageError) throw new Error(controls.storageError);
        sets.push(plain(value));
        Object.assign(store, plain(value));
      },
      async get(keys) {
        if (controls.storageGetError) throw new Error(controls.storageGetError);
        const names = typeof keys === 'string' ? [keys] : keys;
        return Object.fromEntries(names.filter(key => key in store).map(key => [key, plain(store[key])]));
      }
    } }
  };
  const context = vm.createContext({ chrome, URL, URLSearchParams });
  context.importScripts = (...names) => {
    for (const name of names) vm.runInContext(read(name), context, { filename: name });
  };
  vm.runInContext(read('background.js'), context, { filename: 'background.js' });
  for (const [name, source] of Object.entries(events)) {
    assert.equal(source.listeners.length, 1, `${name} listener must register synchronously when worker starts`);
  }
  return { controls, store, messages, sets, creates, nativeCalls, contextCalls, listeners,
    runtime, events, actionCalls, alarmCalls, menuCalls, notificationCalls };
}

function request(harness, message, sender = { id: 'mock-extension', url: 'chrome-extension://mock-extension/popup.html' }) {
  return new Promise((resolve, reject) => {
    const timeout = setTimeout(() => reject(new Error(`no response for ${message.type}`)), 1000);
    try {
      const returned = harness.listeners[0](message, sender, response => { clearTimeout(timeout); resolve(plain(response)); });
      assert.equal(returned, true, 'async response channel must stay open');
    } catch (error) { clearTimeout(timeout); reject(error); }
  });
}

let cases = 0;
async function check(name, body) {
  try { await body(); cases++; console.log(`PASS ${name}`); }
  catch (error) { throw new Error(name, { cause: error }); }
}

(async () => {
  await check('X dispatch canonicalizes URL and saves only a valid job', async () => {
    const h = createBackgroundHarness();
    const accepted = await request(h, { type: 'sg-deliver', url: 'https://mobile.twitter.com/mock/status/123/video/1?s=20', quality: '720' });
    assert.deepEqual(accepted, { ...h.controls.nativeResult, platform: 'x', url: xUrl });
    assert.deepEqual(h.nativeCalls, [{ host: 'com.shiguang.fluentytdl_bridge', message: {
      action: 'yt-download', url: xUrl, ...nativeDownloadOptions, quality: '720'
    } }]);
    assert.deepEqual(h.store.sg_last_job, { id: jobId, url: xUrl, downloadDir: 'D:\\MockDownloads', platform: 'x' });
    assert.equal(h.store.sg_history_v2.length, 1);
    assert.equal(h.store.sg_history_v2[0].id, jobId);
    assert.equal(h.store.sg_history_v2[0].platform, 'x');
    assert.equal(h.store.sg_history_v2[0].state, 'queued');
    assert.deepEqual(h.store.sg_history_v2[0].request, {
      url: xUrl, mode: 'direct', ...downloadOptions, quality: '720', liveMinutes: '240',
      danmaku: 'off', biliSubtitles: 'off', title: ''
    });
    assert.deepEqual(h.actionCalls, [
      { method: 'setBadgeBackgroundColor', value: { color: '#15282c' } },
      { method: 'setBadgeText', value: { text: '1' } },
      { method: 'setTitle', value: { title: 'Veylo · 1 个下载进行中' } }
    ]);
    assert.deepEqual(h.alarmCalls, [{ method: 'create', name: 'veylo-active-jobs', info: { periodInMinutes: 1 } }]);
    assert.equal(h.messages.length, 0);
    assert.equal(h.creates.length, 0);
    await request(h, { type: 'sg-deliver', url: xUrl });
    assert.deepEqual(h.nativeCalls[1], { host: 'com.shiguang.fluentytdl_bridge', message: {
      action: 'yt-download', url: xUrl, ...nativeDownloadOptions
    } });
    assert.equal(h.nativeCalls[1].message.quality, 'best');
    assert.equal(h.store.sg_history_v2.length, 1, 'a duplicate native job must not duplicate history');
  });

  await check('default direct downloads use strict legacy hosts without new fields or ping', async () => {
    const targets = [
      { platform: 'youtube', url: videoUrl, host: 'com.shiguang.fluentytdl_bridge', action: 'yt-download',
        extras: { sponsorblock: 'off', section: '', playlist: false, items: '', live: 'off', liveMaxMinutes: 240 } },
      { platform: 'x', url: xUrl, host: 'com.shiguang.fluentytdl_bridge', action: 'yt-download',
        extras: { sponsorblock: 'off', section: '', playlist: false, items: '', live: 'off', liveMaxMinutes: 240 } },
      { platform: 'bili', url: 'https://www.bilibili.com/video/BV1xx411c7mD', host: 'com.shiguang.bili_bridge', action: 'bili-download',
        extras: { danmaku: 'off', biliSubtitles: 'off' } }
    ];
    for (const { platform, url, host, action, extras } of targets) {
      const h = createBackgroundHarness({ strictLegacy: true });
      const message = { action, url, ...nativeDownloadOptions };
      for (const [key, value] of Object.entries(extras)) {
        const rejected = await new Promise(resolve => h.runtime.sendNativeMessage(host, { ...message, [key]: value }, resolve));
        assert.deepEqual(rejected, { ok: false, error: 'mock strict legacy: unsupported download fields' },
          `strict legacy mock must reject even the default value of ${key}`);
      }
      const previousCalls = h.nativeCalls.length;
      assert.deepEqual(await request(h, { type: 'sg-deliver', url }), { ...h.controls.nativeResult, platform, url });
      assert.deepEqual(h.nativeCalls.slice(previousCalls), [{ host, message }],
        'default direct delivery must send exactly one legacy-compatible download without ping');
      assert.deepEqual(h.store.sg_last_job, { id: jobId, platform, url, downloadDir: 'D:\\MockDownloads' });
      assert.deepEqual(h.store.sg_history_v2[0].request, {
        url, mode: 'direct', ...downloadOptions, liveMinutes: '240', danmaku: 'off', biliSubtitles: 'off', title: ''
      }, 'omitting wire defaults must not lose retry options in history');
      assert.equal(h.messages.length + h.creates.length, 0);
    }
  });

  await check('malformed X success never replaces a previous saved job', async () => {
    const malformed = [
      { ok: true }, { ok: true, jobId },
      { ok: true, jobId, downloadDir: '' }, { ok: true, jobId, downloadDir: ' \t\n ' },
      { ok: true, jobId, downloadDir: 123 }, { ok: true, jobId, downloadDir: null },
      ...[null, 123, '', 'a'.repeat(31), 'a'.repeat(33), 'A'.repeat(32), 'g'.repeat(32)].map(id => ({ ok: true, jobId: id, downloadDir: 'D:\\MockDownloads' }))
    ];
    for (const nativeResult of malformed) {
      const h = createBackgroundHarness({ nativeResult, store: { sg_last_job: plain(oldJob) } });
      const response = await request(h, { type: 'sg-deliver', url: xUrl });
      assert.equal(response.ok, false, 'malformed native success must fail');
      assert.match(response.error, /任务|桥接/);
      assert.equal(h.sets.length, 0);
      assert.deepEqual(h.store.sg_last_job, oldJob);
    }
  });

  await check('clipboard delivery persists only after success and copy preserves history', async () => {
    const h = createBackgroundHarness();
    const response = await request(h, { type: 'sg-deliver', mode: 'handoff', url: 'https://youtu.be/abcdefghijk?t=20' });
    assert.deepEqual(response, { ok: true, platform: 'youtube', kind: 'video', url: videoUrl });
    assert.equal(h.creates.length, 1);
    assert.deepEqual(h.creates[0].reasons, ['CLIPBOARD']);
    assert.equal(h.creates[0].url, 'offscreen.html');
    assert.deepEqual(h.contextCalls[0], { contextTypes: ['OFFSCREEN_DOCUMENT'], documentUrls: ['chrome-extension://mock-extension/offscreen.html'] });
    assert.deepEqual(h.messages, [{ type: 'sg-clipboard-write', text: videoUrl }]);
    assert.equal(h.store.sg_last_clip.url, videoUrl);
    assert.ok(Number.isFinite(h.store.sg_last_clip.at));
    const saved = plain(h.store);
    assert.deepEqual(await request(h, { type: 'sg-copy', mode: 'handoff', url: xUrl }), { ok: true });
    assert.deepEqual(h.messages[1], { type: 'sg-clipboard-write', text: xUrl });
    assert.deepEqual(h.store, saved);
    assert.equal(h.nativeCalls.length, 0);
  });

  await check('clipboard false/missing/transport failure does not persist a success', async () => {
    for (const option of [{ clipboardResult: { ok: false } }, { clipboardResult: undefined }, { clipboardResult: {} }, { clipboardResult: { ok: 'yes' } }, { clipboardError: 'mock transport failure' }]) {
      const h = createBackgroundHarness({ ...option, store: { sg_last_clip: { url: 'old-mock', at: 1 }, sg_last_job: plain(oldJob) } });
      const previous = plain(h.store);
      const response = await request(h, { type: 'sg-deliver', mode: 'handoff', url: videoUrl });
      assert.equal(response.ok, false);
      assert.equal(h.sets.length, 0);
      assert.deepEqual(h.store, previous);
    }
  });

  await check('concurrent clipboard calls share one in-flight offscreen creation', async () => {
    const gate = deferred();
    const h = createBackgroundHarness({ createDocument: () => gate.promise });
    const first = request(h, { type: 'sg-copy', mode: 'handoff', url: videoUrl });
    const second = request(h, { type: 'sg-copy', mode: 'handoff', url: videoUrl });
    await flush();
    assert.equal(h.creates.length, 1);
    assert.equal(h.messages.length, 0, 'clipboard must wait for document creation');
    gate.resolve();
    assert.deepEqual(await Promise.all([first, second]), [{ ok: true }, { ok: true }]);
    assert.equal(h.messages.length, 2);
  });

  await check('a delayed empty context snapshot cannot trigger duplicate creation', async () => {
    const secondSnapshot = deferred();
    const creation = deferred();
    const h = createBackgroundHarness({
      getContexts: call => call === 1 ? Promise.resolve([]) : secondSnapshot.promise,
      createDocument: () => creation.promise
    });
    const first = request(h, { type: 'sg-copy', mode: 'handoff', url: videoUrl });
    await flush();
    const second = request(h, { type: 'sg-copy', mode: 'handoff', url: videoUrl });
    await flush();
    creation.resolve();
    assert.equal((await first).ok, true);
    secondSnapshot.resolve([]);
    assert.equal((await second).ok, true);
    assert.equal(h.creates.length, 1, 'query and creation must use the same concurrency guard');
  });

  await check('offscreen creation failure releases the guard for a retry', async () => {
    const h = createBackgroundHarness({ createDocument: call => call === 1 ? Promise.reject(new Error('mock creation failed')) : Promise.resolve() });
    assert.equal((await request(h, { type: 'sg-deliver', mode: 'handoff', url: videoUrl })).ok, false);
    assert.equal(h.sets.length, 0);
    assert.equal(h.messages.length, 0);
    assert.equal((await request(h, { type: 'sg-deliver', mode: 'handoff', url: videoUrl })).ok, true);
    assert.equal(h.creates.length, 2);
  });

  await check('new worker restores storage and reuses a surviving offscreen document', async () => {
    const first = createBackgroundHarness();
    await request(first, { type: 'sg-deliver', mode: 'handoff', url: videoUrl });
    await request(first, { type: 'sg-deliver', url: xUrl });
    const restored = createBackgroundHarness({ store: first.store, contexts: first.controls.contexts });
    assert.deepEqual(await request(restored, { type: 'sg-status' }), { ok: true, last: first.store.sg_last_clip, job: first.store.sg_last_job });
    await request(restored, { type: 'sg-copy', mode: 'handoff', url: videoUrl });
    assert.equal(restored.creates.length, 0);
    const noDocument = createBackgroundHarness({ store: first.store });
    await request(noDocument, { type: 'sg-copy', mode: 'handoff', url: videoUrl });
    assert.equal(noDocument.creates.length, 1);
    assert.deepEqual((await request(createBackgroundHarness(), { type: 'sg-status' })), { ok: true, last: null, job: null });
  });

  await check('job-status and ping forward protocol values including missing engine', async () => {
    for (const state of ['queued', 'downloading', 'completed', 'failed', 'canceled']) {
      const nativeResult = { ok: true, job: { id: jobId, state, percent: state === 'completed' ? 100 : 20, downloadDir: 'D:\\MockDownloads', error: state === 'failed' ? 'mock failure' : state === 'canceled' ? '已取消下载。' : '' } };
      const h = createBackgroundHarness({ nativeResult });
      assert.deepEqual(await request(h, { type: 'sg-job-status', jobId }), nativeResult);
      assert.deepEqual(h.nativeCalls[0].message, { action: 'job-status', jobId });
      assert.equal(h.sets.length, 0);
    }
    for (const engineAvailable of [true, false]) {
      const nativeResult = { ok: true, version: 'mock-version', engineAvailable };
      const h = createBackgroundHarness({ nativeResult });
      assert.deepEqual(await request(h, { type: 'sg-bridge-ping' }), { ...nativeResult, features: [], login: {} });
      assert.deepEqual(h.nativeCalls[0], { host: 'com.shiguang.fluentytdl_bridge', message: { action: 'ping' } });
    }
  });

  await check('terminal history updates stop polling and canceled jobs never notify', async () => {
    for (const state of ['completed', 'failed', 'canceled']) {
      const h = createBackgroundHarness({ notificationPermission: true, store: { sg_settings: { notify: true } } });
      await request(h, { type: 'sg-deliver', url: xUrl });
      const error = state === 'failed' ? 'mock failure' : state === 'canceled' ? '已取消下载。' : '';
      const percent = state === 'completed' ? 100 : 20;
      h.controls.nativeResult = { ok: true, job: { id: jobId, state, percent, error, downloadDir: 'D:\\MockDownloads' } };
      const refreshed = await request(h, { type: 'sg-history-refresh' });
      assert.equal(refreshed.ok, true);
      assert.equal(refreshed.jobs.length, 1);
      assert.equal(refreshed.jobs[0].state, state);
      assert.equal(refreshed.jobs[0].percent, percent);
      assert.equal(refreshed.jobs[0].error, error);
      assert.equal(refreshed.jobs[0].notified, true);
      assert.equal(refreshed.jobs[0].connectionError, '');
      assert.deepEqual(h.store.sg_history_v2, refreshed.jobs);
      assert.deepEqual(h.nativeCalls[1], { host: 'com.shiguang.fluentytdl_bridge', message: { action: 'job-status', jobId } });
      assert.deepEqual(h.actionCalls.slice(-3), [
        { method: 'setBadgeBackgroundColor', value: { color: '#15282c' } },
        { method: 'setBadgeText', value: { text: '' } },
        { method: 'setTitle', value: { title: 'Veylo · 留下喜欢的内容' } }
      ]);
      assert.deepEqual(h.alarmCalls.at(-1), { method: 'clear', name: 'veylo-active-jobs' });
      assert.equal(h.notificationCalls.length, state === 'canceled' ? 0 : 1,
        'cancellation must not notify even when notifications are enabled and permitted');
      const nativeCount = h.nativeCalls.length;
      assert.deepEqual(await request(h, { type: 'sg-history-refresh' }), refreshed);
      assert.equal(h.nativeCalls.length, nativeCount, 'terminal jobs must not be polled again');
      assert.equal(h.notificationCalls.length, state === 'canceled' ? 0 : 1);
      assert.deepEqual(await request(h, { type: 'sg-history-clear' }), { ok: true, jobs: [] });
      assert.deepEqual(h.store.sg_history_v2, []);
      assert.equal(h.store.sg_last_job, null);
    }
  });

  await check('Native runtime errors and invalid ok flags never persist a job', async () => {
    for (const option of [{ nativeError: 'mock unavailable' }, { nativeResult: undefined }, { nativeResult: {} }, { nativeResult: { ok: false, error: 'mock protocol failure' } }, { nativeResult: { ok: 'yes', jobId, downloadDir: 'D:\\MockDownloads' } }]) {
      const h = createBackgroundHarness({ ...option, store: { sg_last_job: plain(oldJob) } });
      const response = await request(h, { type: 'sg-deliver', url: xUrl });
      assert.equal(response.ok, false);
      if (option.nativeError) assert.equal(response.error, 'YouTube / X 本机连接不可用，请运行项目目录中的 安装浏览器助手.bat 后重试。');
      if (option.nativeResult?.ok === false) assert.equal(response.error, 'mock protocol failure');
      assert.deepEqual(h.store.sg_last_job, oldJob);
      assert.equal(h.sets.length, 0);
    }
  });

  await check('storage failures and invalid URLs cannot report delivery success', async () => {
    const h = createBackgroundHarness({ storageError: 'mock storage failure' });
    for (const message of [
      { type: 'sg-deliver', mode: 'handoff', url: videoUrl },
      { type: 'sg-deliver', mode: 'direct', url: videoUrl },
      { type: 'sg-deliver', mode: 'direct', url: xUrl }
    ]) assert.deepEqual(await request(h, message), { ok: false, error: 'mock storage failure' });
    assert.deepEqual(h.store, {});
    assert.equal(h.sets.length, 0);
    const readFailure = createBackgroundHarness({ storageGetError: 'mock storage read failure' });
    for (const message of [{ type: 'sg-status' }, { type: 'sg-deliver', mode: 'direct', url: xUrl }]) {
      assert.deepEqual(await request(readFailure, message), { ok: false, error: 'mock storage read failure' });
    }
    assert.equal(readFailure.nativeCalls.length, 0);
    assert.equal(readFailure.sets.length, 0);
    const invalid = createBackgroundHarness();
    for (const type of ['sg-deliver', 'sg-copy']) assert.equal((await request(invalid, { type, url: 'https://example.test/mock' })).ok, false);
    assert.equal(invalid.nativeCalls.length + invalid.messages.length + invalid.creates.length, 0);
  });

  await check('foreign senders and unrelated messages are ignored', async () => {
    const h = createBackgroundHarness();
    let responses = 0;
    for (const sender of [{ id: 'foreign-extension' }, {}, { id: undefined }]) {
      for (const message of [{ type: 'sg-bridge-ping' }, { type: 'sg-deliver', mode: 'handoff', url: videoUrl }]) {
        assert.equal(h.listeners[0](message, sender, () => responses++), false);
      }
    }
    for (const message of [{ type: 'unrelated' }, {}, null]) {
      assert.equal(h.listeners[0](message, { id: 'mock-extension' }, () => responses++), false);
    }
    await flush();
    assert.equal(responses, 0);
    assert.equal(h.nativeCalls.length, 0);
    assert.equal(h.messages.length + h.creates.length + h.sets.length, 0);
    assert.equal(h.actionCalls.length + h.alarmCalls.length, 0);
  });

  console.log(`extension_background_test: ${cases} groups passed (mock Native/clipboard/offscreen/storage/worker restart)`);
})().catch(error => { console.error(error); process.exitCode = 1; });
