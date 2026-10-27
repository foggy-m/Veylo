'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ROOT = path.resolve(__dirname, '../extension');
const YOUTUBE = 'https://www.youtube.com/watch?v=jNQXAC9IVRw';
const BILI = 'https://www.bilibili.com/video/BV1GJ411x7h7';
const XURL = 'https://x.com/example/status/1234567890';
const PLAYLIST = 'https://www.youtube.com/playlist?list=PLfixture';
const BANGUMI = 'https://www.bilibili.com/bangumi/play/ep123456';
const SG_HOST = 'com.shiguang.fluentytdl_bridge';
const BILI_HOST = 'com.shiguang.bili_bridge';
const FEATURES = {
  [SG_HOST]: ['cancel', 'sponsorblock', 'sections', 'playlist', 'live', 'pick-folder-initial', 'login-status', 'thumbnail', 'chapters', 'archive', 'transfer', 'open-file'],
  [BILI_HOST]: ['cancel', 'danmaku', 'subtitles', 'collection', 'pick-folder-initial', 'login-status', 'open-file']
};
const clone = value => value === undefined ? value : JSON.parse(JSON.stringify(value));

function setup(initial = {}, options = {}) {
  const db = clone(initial);
  const nativeCalls = [], notifications = [], indicators = [], opened = [], menus = [];
  const jobs = new Map();
  const hooks = {}, listenerCounts = {};
  let number = 0, permitted = false, broken = false;
  const event = name => ({ addListener(fn) { hooks[name] = fn; listenerCounts[name] = (listenerCounts[name] || 0) + 1; } });
  const chrome = {
    runtime: {
      id: 'ikaahmfclfkamdgikchmjnoalpklkddk', lastError: null,
      getURL: p => 'chrome-extension://ikaahmfclfkamdgikchmjnoalpklkddk/' + p,
      getContexts: async () => [{ contextType: 'OFFSCREEN_DOCUMENT' }],
      sendMessage: async () => ({ ok: true }),
      onMessage: event('message'), onInstalled: event('installed'), onStartup: event('startup'),
      sendNativeMessage(host, message, callback) {
        nativeCalls.push({ host, message: clone(message) });
        queueMicrotask(() => {
          if (broken && message.action === 'job-status') return callback({ ok: false, error: 'temporarily disconnected' });
          if (message.action === 'ping') return callback({ ok: true, engineAvailable: true, version: 'test',
            ...(options.legacyOriginalHost ? {} : { mediaOptions: ['video', 'audio-original', 'audio-mp3', 'audio-m4a'] }),
            ...(options.legacyAudioHost ? {} : { audioQualityOptions: ['auto', '320', '256', '192', '128'] }),
            ...(options.legacyFeatures ? {} : { features: clone((options.features || FEATURES)[host]) }),
            ...(options.pingExtra ? clone(options.pingExtra[host] || {}) : {}) });
          if (message.action === 'pick-folder') return callback({ ok: true, path: 'D:\\合成测试\\Folder with spaces' });
          if (message.action === 'cancel-job') {
            const job = jobs.get(message.jobId);
            return callback(job ? { ok: true, jobId: message.jobId, state: ['completed', 'failed', 'canceled'].includes(job.state) ? job.state : 'canceling' } : { ok: false, error: '找不到此下载任务。' });
          }
          if (message.action === 'open-job-folder') return callback({ ok: true });
          if (message.action === 'open-job-file') return callback({ ok: true });
          if (message.action === 'bili-handoff') return callback({ ok: true, launched: false });
          if (message.action === 'job-status') {
            const job = jobs.get(message.jobId);
            return callback(job ? { ok: true, job: clone(job) } : { ok: false, error: 'unknown' });
          }
          if (['yt-download', 'bili-download'].includes(message.action)) {
            const id = String(++number).padStart(32, '0');
            const job = { id, state: 'downloading', percent: 10, downloadDir: message.dir || 'D:\\Default', title: 'Synthetic fixture', media: message.media, files: [] };
            jobs.set(id, job);
            return callback({ ok: true, jobId: id, downloadDir: job.downloadDir, login: true, cookieStatus: 'signed-in' });
          }
          callback({ ok: false, error: 'unsupported action' });
        });
      }
    },
    storage: { local: {
      async get(keys) { const out = {}; for (const key of Array.isArray(keys) ? keys : [keys]) out[key] = clone(db[key]); return out; },
      async set(values) { Object.assign(db, clone(values)); }
    } },
    permissions: { contains: async () => permitted, onAdded: event('permissionsAdded') },
    offscreen: { createDocument: async () => {} },
    action: {
      setBadgeBackgroundColor: async () => {}, setBadgeText: async obj => indicators.push(obj.text), setTitle: async () => {}
    },
    alarms: { create: async () => {}, clear: async () => {}, onAlarm: event('alarm') },
    contextMenus: {
      removeAll(cb) { cb(); }, create(item, cb) { menus.push(item); cb(); }, onClicked: event('menu')
    },
    windows: { create: async options => opened.push(options) }, tabs: { create: async options => opened.push(options) },
    notifications: {
      create: async (id, data) => notifications.push({ id, data }), onClicked: event('noticeClick'), onButtonClicked: event('noticeButton')
    }
  };
  const notificationApi = chrome.notifications;
  if (options.delayedNotifications) delete chrome.notifications;
  const context = vm.createContext({ chrome, URL, URLSearchParams, Date, Set, Map, Object, Promise, Number, console });
  context.importScripts = name => vm.runInContext(fs.readFileSync(path.join(ROOT, name), 'utf8'), context);
  vm.runInContext(fs.readFileSync(path.join(ROOT, 'background.js'), 'utf8'), context, { filename: 'background.js' });
  function send(message, sender = { id: chrome.runtime.id, url: chrome.runtime.getURL('popup.html') }) {
    return new Promise((resolve, reject) => {
      const timeout = setTimeout(() => reject(new Error('Message timed out: ' + message.type)), 1000);
      const handled = hooks.message(message, sender, result => { clearTimeout(timeout); resolve(clone(result)); });
      if (!handled) { clearTimeout(timeout); resolve(null); }
    });
  }
  return { send, db, jobs, nativeCalls, notifications, indicators, opened, menus, hooks, listenerCounts,
    enableNotifications: () => { chrome.notifications = notificationApi; },
    permit: () => { permitted = true; }, disconnect: value => { broken = value; } };
}

(async () => {
  let cases = 0;
  const test = async (name, work) => { await work(); cases++; console.log('PASS ' + name); };
  const env = setup({ sg_settings: { dir: 'default', quality: '720', qualityBili: '32', recentDirs: ['D:\\Old'] } });
  await test('download speed is validated, replaced on every poll and cleared on disconnection or completion', async () => {
    const speedEnv = setup();
    const accepted = await speedEnv.send({ type: 'sg-deliver', mode: 'direct', url: BILI });
    const nativeJob = speedEnv.jobs.get(accepted.jobId);
    const stored = () => speedEnv.db.sg_history_v2.find(job => job.id === accepted.jobId);
    for (const speed of [0, 512, 2.4 * 1024 ** 2, -1, '1024', null, undefined]) {
      nativeJob.speedBytesPerSecond = speed;
      await speedEnv.send({ type: 'sg-history-refresh' });
      assert.equal(stored().speedBytesPerSecond, typeof speed === 'number' && speed >= 0 ? speed : null);
    }
    nativeJob.speedBytesPerSecond = 1024;
    await speedEnv.send({ type: 'sg-history-refresh' });
    speedEnv.disconnect(true);
    await speedEnv.send({ type: 'sg-history-refresh' });
    assert.equal(stored().speedBytesPerSecond, null);
    assert.equal(stored().state, 'downloading');
    const completeEnv = setup();
    const complete = await completeEnv.send({ type: 'sg-deliver', mode: 'direct', url: BILI });
    Object.assign(completeEnv.jobs.get(complete.jobId), { state: 'completed', speedBytesPerSecond: 1024 });
    await completeEnv.send({ type: 'sg-history-refresh' });
    assert.equal(completeEnv.db.sg_history_v2.find(job => job.id === complete.jobId).speedBytesPerSecond, null);
  });
  await test('automatic fragment concurrency uses host defaults, explicit single concurrency is sent and retained on retry', async () => {
    const auto = setup();
    assert.equal((await auto.send({ type: 'sg-settings-get' })).settings.concurrentFragments, 'auto');
    await auto.send({ type: 'sg-deliver', mode: 'direct', url: YOUTUBE });
    assert.equal('concurrentFragments' in auto.nativeCalls.at(-1).message, false);
    const explicit = setup({ sg_settings: { concurrentFragments: '1' } });
    const accepted = await explicit.send({ type: 'sg-deliver', mode: 'direct', url: YOUTUBE });
    assert.equal(explicit.nativeCalls.at(-1).message.concurrentFragments, '1');
    Object.assign(explicit.db.sg_history_v2[0], { state: 'failed' });
    await explicit.send({ type: 'sg-settings-set', settings: { concurrentFragments: 'auto' } });
    await explicit.send({ type: 'sg-job-retry', platform: 'youtube', jobId: accepted.jobId });
    assert.equal(explicit.nativeCalls.at(-1).message.concurrentFragments, '1');
  });
  await test('old settings migrate without losing selected qualities or folders', async () => {
    const result = await env.send({ type: 'sg-settings-get' });
    assert.equal(result.settings.media, 'video');
    assert.equal(result.settings.quality, '720');
    assert.deepEqual(result.settings.recentDirs, ['D:\\Old']);
  });
  await test('untrusted sender cannot invoke native components', async () => {
    const count = env.nativeCalls.length;
    assert.equal(await env.send({ type: 'sg-deliver', url: BILI }, { id: 'other' }), null);
    assert.equal(env.nativeCalls.length, count);
  });
  await test('page content senders cannot access any privileged broker operation', async () => {
    const count = env.nativeCalls.length;
    const types = ['sg-deliver', 'sg-copy', 'sg-status', 'sg-job-status', 'sg-bridge-ping',
      'sg-pick-folder', 'sg-job-cancel', 'sg-settings-get', 'sg-settings-set',
      'sg-history-get', 'sg-history-refresh', 'sg-history-clear', 'sg-job-retry',
      'sg-job-open-folder', 'sg-job-open-file'];
    for (const url of [YOUTUBE, BILI, 'https://www.youtube.com/popup.html',
      'chrome-extension://ikaahmfclfkamdgikchmjnoalpklkddk/tests/ui-demo.html']) {
      for (const type of types) {
        assert.equal(await env.send({ type, url: BILI }, { id: 'ikaahmfclfkamdgikchmjnoalpklkddk', url, tab: { id: 7 } }), null);
      }
    }
    assert.equal(await env.send({ type: 'sg-history-get' }, { id: 'ikaahmfclfkamdgikchmjnoalpklkddk' }), null);
    assert.equal(env.nativeCalls.length, count);
  });
  let acceptedBili, acceptedYt;
  await test('Bilibili direct defaults to saved Bilibili quality and records a task', async () => {
    acceptedBili = await env.send({ type: 'sg-deliver', mode: 'direct', url: BILI });
    assert.equal(acceptedBili.ok, true);
    assert.equal(env.nativeCalls.at(-1).message.quality, '32');
    assert.equal(env.nativeCalls.at(-1).message.media, 'video');
    assert.equal(env.indicators.at(-1), '1');
  });
  await test('YouTube direct defaults to saved YouTube quality', async () => {
    acceptedYt = await env.send({ type: 'sg-deliver', mode: 'direct', url: YOUTUBE });
    assert.equal(acceptedYt.ok, true);
    assert.equal(env.nativeCalls.at(-1).message.quality, '720');
  });
  await test('audio/subtitle options are normalized for their platform', async () => {
    const result = await env.send({ type: 'sg-deliver', mode: 'direct', url: BILI, media: 'audio-mp3', subtitles: 'auto', metadata: true });
    assert.equal(result.ok, true);
    assert.equal(env.nativeCalls.at(-1).message.quality, 'best');
    assert.equal(env.nativeCalls.at(-1).message.subtitles, 'off');
    assert.equal(env.nativeCalls.at(-1).message.metadata, false);
    await env.send({ type: 'sg-deliver', mode: 'direct', url: YOUTUBE, media: 'video', subtitles: 'auto', subLangs: 'en', metadata: true });
    assert.equal(env.nativeCalls.at(-1).message.subtitles, 'auto');
    assert.equal(env.nativeCalls.at(-1).message.metadata, true);
  });
  await test('unsupported formats and playlist direct are rejected before native dispatch', async () => {
    const count = env.nativeCalls.length;
    assert.equal((await env.send({ type: 'sg-deliver', mode: 'direct', url: YOUTUBE, media: '--exec=evil' })).ok, false);
    assert.equal((await env.send({ type: 'sg-deliver', mode: 'direct', url: 'https://www.youtube.com/playlist?list=PLfixture' })).ok, false);
    assert.equal(env.nativeCalls.length, count);
  });
  await test('concurrent accepts do not overwrite other history records', async () => {
    await Promise.all(Array.from({ length: 5 }, () => env.send({ type: 'sg-deliver', mode: 'direct', url: XURL })));
    const result = await env.send({ type: 'sg-history-get' });
    assert.equal(result.jobs.length, 9);
    assert.equal(new Set(result.jobs.map(j => j.id)).size, 9);
  });
  await test('native folder selection persists even if its UI disappears', async () => {
    const result = await env.send({ type: 'sg-pick-folder', host: 'bili' });
    assert.equal(result.settings.dir, 'D:\\合成测试\\Folder with spaces');
    assert.equal((await env.send({ type: 'sg-settings-get' })).settings.dir, result.path);
    assert.equal(result.recentDirs[1], 'D:\\Old');
  });
  await test('settings whitelist preserves recent folders and refuses notification opt-in without permission', async () => {
    const result = await env.send({ type: 'sg-settings-set', settings: { notify: true, recentDirs: ['X:\\inject'], media: 'audio-m4a', arbitrary: true } });
    assert.equal(result.settings.notify, false);
    assert.equal(result.settings.media, 'audio-m4a');
    assert.equal(result.settings.recentDirs[0], 'D:\\合成测试\\Folder with spaces');
    assert.equal('arbitrary' in result.settings, false);
  });
  await test('temporary bridge disconnect does not falsely fail a live download', async () => {
    env.disconnect(true);
    const result = await env.send({ type: 'sg-history-refresh' });
    assert.equal(result.jobs.find(j => j.id === acceptedBili.jobId).state, 'queued');
    assert.equal(result.jobs.find(j => j.id === acceptedBili.jobId).connectionError, 'temporarily disconnected');
    env.disconnect(false);
  });
  await test('completed task notification is opt-in and sent once', async () => {
    env.permit();
    await env.send({ type: 'sg-settings-set', settings: { notify: true } });
    env.jobs.get(acceptedBili.jobId).state = 'completed';
    env.jobs.get(acceptedBili.jobId).percent = 100;
    env.jobs.get(acceptedBili.jobId).files = ['D:\\Default\\fixture.mp4'];
    await env.send({ type: 'sg-history-refresh' });
    await env.send({ type: 'sg-history-refresh' });
    assert.equal(env.notifications.length, 1);
    assert.equal(env.notifications[0].data.title, 'Veylo · 下载完成');
    const result = await env.send({ type: 'sg-history-get' });
    assert.equal(result.jobs.find(j => j.id === acceptedBili.jobId).files.length, 1);
  });
  await test('retry uses failed task options, not current global defaults', async () => {
    env.jobs.get(acceptedYt.jobId).state = 'failed';
    env.jobs.get(acceptedYt.jobId).error = 'Synthetic failure';
    await env.send({ type: 'sg-history-refresh' });
    const result = await env.send({ type: 'sg-job-retry', jobId: acceptedYt.jobId, platform: 'youtube' });
    assert.equal(result.ok, true);
    assert.equal(env.nativeCalls.at(-1).message.media, 'video');
    assert.equal(env.nativeCalls.at(-1).message.quality, '720');
    assert.equal(env.nativeCalls.at(-1).message.dir, '');
  });
  await test('folder open is restricted to a task already in local history', async () => {
    assert.equal((await env.send({ type: 'sg-job-open-folder', jobId: acceptedBili.jobId, platform: 'bili' })).ok, true);
    const nativeMessage = env.nativeCalls.at(-1).message;
    assert.deepEqual(nativeMessage, { action: 'open-job-folder', jobId: acceptedBili.jobId });
    assert.equal((await env.send({ type: 'sg-job-open-folder', jobId: 'f'.repeat(32), platform: 'bili' })).ok, false);
  });
  await test('clear history keeps all active tasks and never calls native deletion', async () => {
    const count = env.nativeCalls.length;
    const before = (await env.send({ type: 'sg-history-get' })).jobs;
    const active = before.filter(j => ['queued', 'downloading'].includes(j.state));
    const result = await env.send({ type: 'sg-history-clear' });
    assert.equal(result.jobs.length, active.length);
    assert.ok(result.jobs.every(j => ['queued', 'downloading'].includes(j.state)));
    assert.equal(env.nativeCalls.length, count);
  });
  await test('context menu opens a prefilled panel without starting a download', async () => {
    env.hooks.installed();
    await new Promise(resolve => setTimeout(resolve, 10));
    assert.equal(env.menus[0].title, '在 Veylo 中下载…');
    const count = env.nativeCalls.filter(c => c.message.action.endsWith('download')).length;
    env.hooks.menu({ menuItemId: 'veylo-open-download', linkUrl: BILI }, {});
    await new Promise(resolve => setTimeout(resolve, 10));
    assert.ok(env.opened[0].url.includes('?url='));
    assert.equal(env.nativeCalls.filter(c => c.message.action.endsWith('download')).length, count);
  });
  await test('old last-job record is carried into the task center', async () => {
    const old = setup({ sg_last_job: { id: 'a'.repeat(32), platform: 'youtube', url: YOUTUBE, downloadDir: 'D:\\Old' } });
    const result = await old.send({ type: 'sg-history-get' });
    assert.equal(result.jobs.length, 1);
    assert.equal(result.jobs[0].notified, true);
    assert.equal(result.jobs[0].media, 'video');
  });
  await test('Bilibili all-parts preference does not leak into other platforms or legacy retries', async () => {
    const mixed = setup({ sg_settings: { part: 'all' } });
    await mixed.send({ type: 'sg-deliver', mode: 'direct', url: BILI });
    assert.equal(mixed.nativeCalls.at(-1).message.part, 'all');
    for (const [url, platform] of [[YOUTUBE, 'youtube'], [XURL, 'x']]) {
      const accepted = await mixed.send({ type: 'sg-deliver', mode: 'direct', url });
      assert.equal(accepted.ok, true);
      assert.equal(mixed.nativeCalls.at(-1).message.part, 'current');
      const stored = mixed.db.sg_history_v2.find(job => job.id === accepted.jobId);
      assert.equal(stored.request.part, 'current');
      stored.request.part = 'all';
      mixed.jobs.get(accepted.jobId).state = 'failed';
      await mixed.send({ type: 'sg-history-refresh' });
      const retried = await mixed.send({ type: 'sg-job-retry', jobId: accepted.jobId, platform });
      assert.equal(retried.ok, true);
      assert.equal(mixed.nativeCalls.at(-1).message.part, 'current');
    }
    assert.equal((await mixed.send({ type: 'sg-settings-get' })).settings.part, 'all');
  });
  await test('legacy Bilibili last-job migration uses Bilibili quality rather than YouTube quality', async () => {
    const old = setup({
      sg_settings: { quality: '720', qualityBili: '32', part: 'all' },
      sg_last_job: { id: 'b'.repeat(32), platform: 'bili', url: BILI, downloadDir: 'D:\\Old' }
    });
    const history = await old.send({ type: 'sg-history-get' });
    assert.equal(history.ok, true);
    assert.equal(history.jobs.length, 1);
    assert.equal(history.jobs[0].request.quality, '32');
    assert.equal(history.jobs[0].request.part, 'all');
    assert.equal(history.jobs[0].notified, true);
    assert.equal((await old.send({ type: 'sg-history-refresh' })).ok, true);
  });
  await test('notification listeners attach once after optional permission and route only valid job actions', async () => {
    const late = setup({}, { delayedNotifications: true });
    assert.equal((await late.send({ type: 'sg-settings-get' })).ok, true);
    assert.equal(late.listenerCounts.noticeClick, undefined);
    late.hooks.permissionsAdded({ permissions: ['storage'] });
    assert.equal(late.listenerCounts.noticeClick, undefined);
    late.enableNotifications();
    late.hooks.permissionsAdded({ permissions: ['notifications'] });
    late.hooks.permissionsAdded({ permissions: ['notifications'] });
    assert.equal(late.listenerCounts.noticeClick, 1);
    assert.equal(late.listenerCounts.noticeButton, 1);
    const accepted = await late.send({ type: 'sg-deliver', mode: 'direct', url: BILI });
    const noticeId = `veylo:bili:${accepted.jobId}`;
    const folderCalls = () => late.nativeCalls.filter(call => call.message.action === 'open-job-folder');
    late.hooks.noticeClick(noticeId);
    await new Promise(resolve => setTimeout(resolve, 10));
    assert.equal(folderCalls().length, 1);
    assert.equal(folderCalls()[0].host, 'com.shiguang.bili_bridge');
    late.hooks.noticeButton(noticeId, 1);
    late.hooks.noticeClick('unrelated-notice');
    late.hooks.noticeClick(`veylo:bili:${'f'.repeat(32)}`);
    await new Promise(resolve => setTimeout(resolve, 10));
    assert.equal(folderCalls().length, 1);
    late.hooks.noticeButton(noticeId, 0);
    await new Promise(resolve => setTimeout(resolve, 10));
    assert.equal(folderCalls().length, 2);
    assert.deepEqual(folderCalls()[1].message, { action: 'open-job-folder', jobId: accepted.jobId });
  });
  await test('audio quality defaults to auto and only supported bitrate preferences persist', async () => {
    const audio = setup({ sg_settings: { media: 'audio-mp3', quality: '720', dir: 'D:\\Audio' } });
    const original = await audio.send({ type: 'sg-settings-get' });
    assert.equal(original.settings.audioQuality, 'auto');
    assert.equal(original.settings.quality, '720');
    for (const audioQuality of ['320', '256', '192', '128', 'auto']) {
      const saved = await audio.send({ type: 'sg-settings-set', settings: { audioQuality } });
      assert.equal(saved.settings.audioQuality, audioQuality);
      assert.equal((await audio.send({ type: 'sg-settings-get' })).settings.audioQuality, audioQuality);
      assert.equal(saved.settings.dir, 'D:\\Audio');
    }
    const invalid = await audio.send({ type: 'sg-settings-set', settings: { audioQuality: '999 --exec=invalid' } });
    assert.equal(invalid.settings.audioQuality, 'auto');
  });
  await test('each audio format sends the selected encoding bitrate on all three platforms and records it', async () => {
    const audio = setup();
    for (const url of [YOUTUBE, XURL, BILI]) {
      for (const media of ['audio-mp3', 'audio-m4a']) {
        for (const audioQuality of ['auto', '320', '256', '192', '128']) {
          const accepted = await audio.send({ type: 'sg-deliver', mode: 'direct', url, media, audioQuality });
          assert.equal(accepted.ok, true);
          assert.equal(audio.nativeCalls.at(-1).message.audioQuality, audioQuality);
          assert.equal(audio.nativeCalls.at(-1).message.quality, 'best');
          const stored = (await audio.send({ type: 'sg-history-get' })).jobs.find(job => job.id === accepted.jobId);
          assert.equal(stored.request.audioQuality, audioQuality);
          assert.equal(stored.request.media, media);
        }
      }
    }
    await audio.send({ type: 'sg-settings-set', settings: { media: 'audio-m4a', audioQuality: '256' } });
    assert.equal((await audio.send({ type: 'sg-deliver', mode: 'direct', url: YOUTUBE })).ok, true);
    assert.equal(audio.nativeCalls.at(-1).message.audioQuality, '256');
  });
  await test('invalid audio bitrates are rejected before native dispatch and video ignores the saved audio preference', async () => {
    const audio = setup({ sg_settings: { audioQuality: '320' } });
    for (const audioQuality of ['0', '64', '999', '320k', '--exec=invalid', '', null, 320, {}]) {
      const count = audio.nativeCalls.length;
      const result = await audio.send({ type: 'sg-deliver', mode: 'direct', url: YOUTUBE, media: 'audio-mp3', audioQuality });
      assert.equal(result.ok, false);
      assert.match(result.error, /音质/);
      assert.equal(audio.nativeCalls.length, count);
    }
    for (const url of [YOUTUBE, XURL, BILI]) {
      const accepted = await audio.send({ type: 'sg-deliver', mode: 'direct', url, media: 'video', audioQuality: '256' });
      assert.equal(accepted.ok, true);
      assert.equal(audio.nativeCalls.at(-1).message.audioQuality, 'auto');
      assert.equal((await audio.send({ type: 'sg-history-get' })).jobs.find(job => job.id === accepted.jobId).request.audioQuality, 'auto');
    }
    assert.equal((await audio.send({ type: 'sg-settings-get' })).settings.audioQuality, '320');
  });
  await test('audio retry keeps its original bitrate and old requests default to auto rather than current preferences', async () => {
    const audio = setup();
    for (const [url, platform] of [[YOUTUBE, 'youtube'], [XURL, 'x'], [BILI, 'bili']]) {
      const accepted = await audio.send({ type: 'sg-deliver', mode: 'direct', url, media: 'audio-m4a', audioQuality: '256' });
      audio.jobs.get(accepted.jobId).state = 'failed';
      await audio.send({ type: 'sg-history-refresh' });
      await audio.send({ type: 'sg-settings-set', settings: { media: 'video', audioQuality: '128' } });
      assert.equal((await audio.send({ type: 'sg-job-retry', jobId: accepted.jobId, platform })).ok, true);
      assert.equal(audio.nativeCalls.at(-1).message.media, 'audio-m4a');
      assert.equal(audio.nativeCalls.at(-1).message.audioQuality, '256');
      delete audio.db.sg_history_v2.find(job => job.id === accepted.jobId).request.audioQuality;
      const retried = await audio.send({ type: 'sg-job-retry', jobId: accepted.jobId, platform });
      assert.equal(retried.ok, true);
      assert.equal(audio.nativeCalls.at(-1).message.audioQuality, 'auto');
      assert.equal((await audio.send({ type: 'sg-history-get' })).jobs.find(job => job.id === retried.jobId).request.audioQuality, 'auto');
    }
    assert.equal((await audio.send({ type: 'sg-settings-get' })).settings.audioQuality, '128');
  });
  await test('explicit audio bitrate requires a supporting native component instead of silently using an old host', async () => {
    const old = setup({}, { legacyAudioHost: true });
    for (const url of [YOUTUBE, XURL, BILI]) {
      const acceptedBefore = old.nativeCalls.filter(call => call.message.action.endsWith('download')).length;
      const result = await old.send({ type: 'sg-deliver', mode: 'direct', url, media: 'audio-mp3', audioQuality: '320' });
      assert.equal(result.ok, false);
      assert.match(result.error, /更新.*本机/);
      assert.equal(old.nativeCalls.filter(call => call.message.action.endsWith('download')).length, acceptedBefore);
    }
    assert.equal((await old.send({ type: 'sg-history-get' })).jobs.length, 0);
    const count = old.nativeCalls.filter(call => call.message.action === 'ping').length;
    assert.equal((await old.send({ type: 'sg-deliver', mode: 'direct', url: YOUTUBE, media: 'audio-m4a', audioQuality: 'auto' })).ok, true);
    assert.equal((await old.send({ type: 'sg-deliver', mode: 'direct', url: BILI, media: 'video', audioQuality: '320' })).ok, true);
    assert.equal(old.nativeCalls.filter(call => call.message.action === 'ping').length, count);
    const supported = setup();
    assert.equal((await supported.send({ type: 'sg-deliver', mode: 'direct', url: BILI, media: 'audio-m4a', audioQuality: '256' })).ok, true);
    const ping = supported.nativeCalls.find(call => call.message.action === 'ping');
    assert.equal(ping.host, 'com.shiguang.bili_bridge');
  });
  await test('a malformed stored audio bitrate is not silently converted on retry', async () => {
    const audio = setup();
    const accepted = await audio.send({ type: 'sg-deliver', mode: 'direct', url: YOUTUBE, media: 'audio-mp3', audioQuality: '192' });
    audio.jobs.get(accepted.jobId).state = 'failed';
    await audio.send({ type: 'sg-history-refresh' });
    audio.db.sg_history_v2.find(job => job.id === accepted.jobId).request.audioQuality = null;
    const count = audio.nativeCalls.length;
    const result = await audio.send({ type: 'sg-job-retry', jobId: accepted.jobId, platform: 'youtube' });
    assert.equal(result.ok, false);
    assert.match(result.error, /音质/);
    assert.equal(audio.nativeCalls.length, count);
  });
  await test('original audio is a persistent format without overwriting fixed-bitrate or video preferences', async () => {
    const original = setup({ sg_settings: { media: 'audio-mp3', audioQuality: '256', quality: '720', qualityBili: '64', dir: 'D:\\Audio' } });
    const saved = await original.send({ type: 'sg-settings-set', settings: { media: 'audio-original' } });
    assert.equal(saved.ok, true);
    assert.equal(saved.settings.media, 'audio-original');
    assert.equal(saved.settings.audioQuality, '256');
    assert.equal(saved.settings.quality, '720');
    assert.equal(saved.settings.qualityBili, '64');
    assert.equal(saved.settings.dir, 'D:\\Audio');
    assert.equal((await original.send({ type: 'sg-settings-get' })).settings.media, 'audio-original');
  });
  await test('original audio normalizes unused bitrate and video extras while recording the actual requested mode', async () => {
    const original = setup({ sg_settings: { audioQuality: '256', quality: '720', qualityBili: '64', subtitles: 'auto', subLangs: 'en', metadata: true, part: 'all' } });
    for (const [url, platform] of [[YOUTUBE, 'youtube'], [XURL, 'x'], [BILI, 'bili']]) {
      const accepted = await original.send({ type: 'sg-deliver', mode: 'direct', url, media: 'audio-original', audioQuality: '320' });
      assert.equal(accepted.ok, true);
      const sent = original.nativeCalls.at(-1);
      assert.equal(sent.host, platform === 'bili' ? 'com.shiguang.bili_bridge' : 'com.shiguang.fluentytdl_bridge');
      assert.equal(sent.message.media, 'audio-original');
      assert.equal(sent.message.audioQuality, 'auto');
      assert.equal(sent.message.quality, 'best');
      assert.equal(sent.message.subtitles, 'off');
      assert.equal(sent.message.part, platform === 'bili' ? 'all' : 'current');
      assert.equal(sent.message.metadata, platform !== 'bili');
      const stored = (await original.send({ type: 'sg-history-get' })).jobs.find(job => job.id === accepted.jobId);
      assert.equal(stored.media, 'audio-original');
      assert.equal(stored.request.media, 'audio-original');
      assert.equal(stored.request.audioQuality, 'auto');
    }
    assert.equal((await original.send({ type: 'sg-settings-get' })).settings.audioQuality, '256');
    const count = original.nativeCalls.length;
    const bad = await original.send({ type: 'sg-deliver', mode: 'direct', url: YOUTUBE, media: 'audio-original', audioQuality: null });
    assert.equal(bad.ok, false);
    assert.match(bad.error, /音质/);
    assert.equal(original.nativeCalls.length, count);
  });
  await test('original audio requires advertised media support before accepting or recording a job', async () => {
    const old = setup({}, { legacyOriginalHost: true });
    for (const url of [YOUTUBE, XURL, BILI]) {
      const count = old.nativeCalls.filter(call => call.message.action.endsWith('download')).length;
      const result = await old.send({ type: 'sg-deliver', mode: 'direct', url, media: 'audio-original' });
      assert.equal(result.ok, false);
      assert.match(result.error, /更新.*本机.*原音轨/);
      assert.equal(old.nativeCalls.filter(call => call.message.action.endsWith('download')).length, count);
    }
    assert.equal((await old.send({ type: 'sg-history-get' })).jobs.length, 0);
    assert.equal((await old.send({ type: 'sg-deliver', mode: 'direct', url: BILI, media: 'video' })).ok, true);
    assert.equal((await old.send({ type: 'sg-deliver', mode: 'direct', url: YOUTUBE, media: 'audio-mp3', audioQuality: 'auto' })).ok, true);
    const supported = setup();
    assert.equal((await supported.send({ type: 'sg-deliver', mode: 'direct', url: BILI, media: 'audio-original' })).ok, true);
    const ping = supported.nativeCalls.find(call => call.message.action === 'ping');
    assert.equal(ping.host, 'com.shiguang.bili_bridge');
    assert.deepEqual(ping.message, { action: 'ping' });
  });
  await test('retry preserves original mode and legacy conversion settings instead of following current preferences', async () => {
    const original = setup();
    for (const [url, platform] of [[YOUTUBE, 'youtube'], [XURL, 'x'], [BILI, 'bili']]) {
      const accepted = await original.send({ type: 'sg-deliver', mode: 'direct', url, media: 'audio-original' });
      assert.equal(accepted.ok, true);
      original.jobs.get(accepted.jobId).state = 'failed';
      await original.send({ type: 'sg-history-refresh' });
      await original.send({ type: 'sg-settings-set', settings: { media: 'audio-mp3', audioQuality: '320' } });
      const retried = await original.send({ type: 'sg-job-retry', jobId: accepted.jobId, platform });
      assert.equal(retried.ok, true);
      assert.equal(original.nativeCalls.at(-1).message.media, 'audio-original');
      assert.equal(original.nativeCalls.at(-1).message.audioQuality, 'auto');
      const saved = original.db.sg_history_v2.find(job => job.id === retried.jobId);
      assert.equal(saved.request.media, 'audio-original');
      assert.equal(saved.request.audioQuality, 'auto');
      assert.equal((await original.send({ type: 'sg-settings-get' })).settings.audioQuality, '320');
    }
    const old = setup({ sg_history_v2: [{ id: 'a'.repeat(32), platform: 'youtube', url: YOUTUBE, state: 'failed', media: 'audio-m4a', request: { media: 'audio-m4a', audioQuality: 'auto' } }], sg_settings: { media: 'audio-original', audioQuality: '320' } });
    assert.equal((await old.send({ type: 'sg-job-retry', jobId: 'a'.repeat(32), platform: 'youtube' })).ok, true);
    assert.equal(old.nativeCalls.at(-1).message.media, 'audio-m4a');
    assert.equal(old.nativeCalls.at(-1).message.audioQuality, 'auto');
  });
  // ---- 2.3.0 contract cases ----
  const downloads = e => e.nativeCalls.filter(call => call.message.action.endsWith('download'));
  await test('2.3.0 settings defaults, choices and persistence', async () => {
    const env2 = setup();
    const initial = (await env2.send({ type: 'sg-settings-get' })).settings;
    assert.equal(initial.sponsorblock, 'off');
    assert.equal(initial.danmaku, 'off');
    assert.equal(initial.biliSubtitles, 'off');
    assert.equal(initial.liveMinutes, '240');
    for (const key of ['section', 'items', 'live', 'playlist']) assert.equal(key in initial, false);
    const saved = await env2.send({ type: 'sg-settings-set', settings: { sponsorblock: 'all', danmaku: 'xml', biliSubtitles: 'srt', liveMinutes: '720', part: 'collection', section: '1-2', live: 'from-start' } });
    assert.equal(saved.settings.sponsorblock, 'all');
    assert.equal(saved.settings.danmaku, 'off');
    assert.equal(saved.settings.biliSubtitles, 'srt');
    assert.equal(saved.settings.liveMinutes, '240');
    assert.equal(saved.settings.part, 'collection');
    assert.equal('section' in saved.settings, false);
    assert.equal('live' in saved.settings, false);
    const invalid = await env2.send({ type: 'sg-settings-set', settings: { sponsorblock: 'skip', danmaku: 'srt', biliSubtitles: 'ass', liveMinutes: 240 } });
    assert.equal(invalid.settings.sponsorblock, 'off');
    assert.equal(invalid.settings.danmaku, 'off');
    assert.equal(invalid.settings.biliSubtitles, 'off');
    assert.equal(invalid.settings.liveMinutes, '240');
  });
  await test('default downloads omit new feature keys without requiring host capabilities', async () => {
    const env2 = setup({}, { legacyFeatures: true });
    for (const url of [YOUTUBE, XURL, BILI]) {
      const result = await env2.send({ type: 'sg-deliver', mode: 'direct', url });
      assert.equal(result.ok, true);
      const message = env2.nativeCalls.at(-1).message;
      for (const key of ['sponsorblock', 'section', 'playlist', 'items', 'live', 'liveMaxMinutes', 'danmaku', 'biliSubtitles']) {
        assert.equal(key in message, false, `legacy request must omit ${key}`);
      }
      const saved = env2.db.sg_history_v2.find(job => job.id === result.jobId).request;
      assert.equal(saved.sponsorblock, 'off');
      assert.equal(saved.danmaku, 'off');
      assert.equal(saved.biliSubtitles, 'off');
      assert.equal(saved.playlist, false);
    }
    assert.equal(env2.nativeCalls.some(call => call.message.action === 'ping'), false);
  });
  await test('new fields are normalized per platform and only the relevant host fields are sent', async () => {
    const env2 = setup({ sg_settings: { sponsorblock: 'sponsor', danmaku: 'ass', biliSubtitles: 'srt', liveMinutes: '60' } });
    const yt = await env2.send({ type: 'sg-deliver', mode: 'direct', url: YOUTUBE, section: '1:30-2:45' });
    assert.equal(yt.ok, true);
    let sent = env2.nativeCalls.at(-1);
    assert.equal(sent.host, SG_HOST);
    assert.equal(sent.message.sponsorblock, 'sponsor');
    assert.equal(sent.message.section, '1:30-2:45');
    for (const key of ['playlist', 'items', 'live', 'liveMaxMinutes']) assert.equal(key in sent.message, false, key);
    assert.equal('danmaku' in sent.message, false);
    assert.equal('biliSubtitles' in sent.message, false);
    const stored = env2.db.sg_history_v2.find(job => job.id === yt.jobId).request;
    for (const [key, value] of Object.entries({ sponsorblock: 'sponsor', section: '1:30-2:45', danmaku: 'off', biliSubtitles: 'off', playlist: false, items: '', live: 'off', liveMinutes: '240' })) assert.deepEqual(stored[key], value, key);
    await env2.send({ type: 'sg-deliver', mode: 'direct', url: XURL, section: '10-20' });
    sent = env2.nativeCalls.at(-1);
    assert.equal('sponsorblock' in sent.message, false);
    assert.equal(sent.message.section, '10-20');
    assert.equal('live' in sent.message, false);
    await env2.send({ type: 'sg-deliver', mode: 'direct', url: BILI, section: '10-20', sponsorblock: 'all' });
    sent = env2.nativeCalls.at(-1);
    assert.equal(sent.host, BILI_HOST);
    assert.equal(sent.message.danmaku, 'ass');
    assert.equal(sent.message.biliSubtitles, 'srt');
    for (const key of ['sponsorblock', 'section', 'live', 'playlist', 'items', 'liveMaxMinutes']) assert.equal(key in sent.message, false, key);
    const bili = env2.db.sg_history_v2.find(job => job.platform === 'bili').request;
    assert.equal(bili.sponsorblock, 'off');
    assert.equal(bili.section, '');
    assert.equal(bili.danmaku, 'ass');
  });
  await test('new fields are strictly validated before any native dispatch', async () => {
    const env2 = setup();
    const bad = [
      { url: YOUTUBE, sponsorblock: '--exec' }, { url: YOUTUBE, sponsorblock: true }, { url: BILI, danmaku: 'srt' }, { url: BILI, biliSubtitles: 'ass' },
      { url: YOUTUBE, section: '2:00-1:00' }, { url: YOUTUBE, section: 'abc' }, { url: YOUTUBE, section: 12 }, { url: YOUTUBE, section: '0-86401' },
      { url: YOUTUBE, section: '1:61-2:00' }, { url: YOUTUBE, section: '1-2;rm' }, { url: XURL, section: '5-5' }, { url: YOUTUBE, section: '1-' + '9'.repeat(40) },
      { url: PLAYLIST, playlist: true, items: '1-10;x' }, { url: PLAYLIST, playlist: true, items: 5 }, { url: PLAYLIST, playlist: 'yes' },
      { url: YOUTUBE, live: 'on' }, { url: YOUTUBE, live: 'from-start', liveMinutes: '999' }, { url: YOUTUBE, liveMinutes: 60 }
    ];
    for (const payload of bad) {
      const count = env2.nativeCalls.length;
      const result = await env2.send({ type: 'sg-deliver', mode: 'direct', ...payload });
      assert.equal(result.ok, false, JSON.stringify(payload));
      assert.equal(env2.nativeCalls.length, count, JSON.stringify(payload));
    }
    for (const section of ['1:30-2:45', '0-inf', '01:02:03-01:02:04', '5-10']) {
      assert.equal((await env2.send({ type: 'sg-deliver', mode: 'direct', url: YOUTUBE, section })).ok, true, section);
      assert.equal(env2.nativeCalls.at(-1).message.section, section);
    }
    assert.equal((await env2.send({ type: 'sg-deliver', mode: 'direct', url: YOUTUBE, section: ' 1:30 - 2:45 ' })).ok, true);
    assert.equal(env2.nativeCalls.at(-1).message.section, '1:30-2:45');
    assert.equal((await env2.send({ type: 'sg-history-get' })).jobs.length, 5);
  });
  await test('incompatible combinations are rejected and SponsorBlock is normalized off where it cannot apply', async () => {
    const env2 = setup({ sg_settings: { sponsorblock: 'all' } });
    for (const payload of [
      { url: YOUTUBE, media: 'audio-original', section: '1-2' }, { url: XURL, media: 'audio-original', section: '1-2' },
      { url: YOUTUBE, live: 'from-start', section: '1-2' }, { url: YOUTUBE, live: 'from-start', media: 'audio-original' },
      { url: PLAYLIST, playlist: true, section: '1-2' }, { url: PLAYLIST, playlist: true, live: 'from-start' }
    ]) {
      const count = env2.nativeCalls.length;
      const result = await env2.send({ type: 'sg-deliver', mode: 'direct', ...payload });
      assert.equal(result.ok, false, JSON.stringify(payload));
      assert.match(result.error, /[\u4e00-\u9fa5]/);
      assert.equal(env2.nativeCalls.length, count);
    }
    assert.equal((await env2.send({ type: 'sg-deliver', mode: 'direct', url: YOUTUBE, media: 'audio-original' })).ok, true);
    assert.equal('sponsorblock' in env2.nativeCalls.at(-1).message, false);
    assert.equal((await env2.send({ type: 'sg-deliver', mode: 'direct', url: YOUTUBE, live: 'from-start' })).ok, false);
    assert.equal((await env2.send({ type: 'sg-deliver', mode: 'direct', url: YOUTUBE, media: 'audio-mp3' })).ok, true);
    assert.equal(env2.nativeCalls.at(-1).message.sponsorblock, 'all');
  });
  await test('hosts lacking a feature never receive the non-default field and no history is created', async () => {
    const old = setup({}, { legacyFeatures: true });
    for (const payload of [
      { url: YOUTUBE, sponsorblock: 'sponsor' }, { url: YOUTUBE, section: '1-2' }, { url: XURL, section: '1-2' },
      { url: PLAYLIST, playlist: true }, { url: BILI, danmaku: 'ass' }, { url: BILI, biliSubtitles: 'srt' }, { url: BILI, part: 'collection' }, { url: BANGUMI, part: 'collection' }
    ]) {
      const before = downloads(old).length;
      const result = await old.send({ type: 'sg-deliver', mode: 'direct', ...payload });
      assert.equal(result.ok, false, JSON.stringify(payload));
      assert.match(result.error, /请更新本机组件后使用/);
      assert.equal(downloads(old).length, before);
    }
    assert.equal((await old.send({ type: 'sg-history-get' })).jobs.length, 0);
    const partial = setup({}, { features: { [SG_HOST]: ['cancel', 'sections'], [BILI_HOST]: ['danmaku'] } });
    assert.equal((await partial.send({ type: 'sg-deliver', mode: 'direct', url: YOUTUBE, section: '1-2' })).ok, true);
    assert.equal((await partial.send({ type: 'sg-deliver', mode: 'direct', url: YOUTUBE, sponsorblock: 'all' })).ok, false);
    assert.equal((await partial.send({ type: 'sg-deliver', mode: 'direct', url: BILI, danmaku: 'ass' })).ok, true);
    assert.equal((await partial.send({ type: 'sg-deliver', mode: 'direct', url: BILI, biliSubtitles: 'srt' })).ok, false);
    const pings = old.nativeCalls.filter(call => call.message.action === 'ping').length;
    assert.equal((await old.send({ type: 'sg-deliver', mode: 'direct', url: YOUTUBE })).ok, true);
    assert.equal((await old.send({ type: 'sg-deliver', mode: 'direct', url: BILI, part: 'all' })).ok, true);
    assert.equal(old.nativeCalls.filter(call => call.message.action === 'ping').length, pings, 'default fields must not require a ping');
  });
  await test('playlist direct download sends the canonical list with item spec; channels stay handoff-only', async () => {
    const env2 = setup();
    const accepted = await env2.send({ type: 'sg-deliver', mode: 'direct', url: PLAYLIST, playlist: true, items: '1-10,15', media: 'audio-m4a', sponsorblock: 'sponsor' });
    assert.equal(accepted.ok, true);
    const sent = env2.nativeCalls.at(-1).message;
    assert.equal(sent.action, 'yt-download');
    assert.equal(sent.url, PLAYLIST);
    assert.equal(sent.playlist, true);
    assert.equal(sent.items, '1-10,15');
    assert.equal(sent.sponsorblock, 'sponsor');
    const watch = await env2.send({ type: 'sg-deliver', mode: 'direct', url: 'https://www.youtube.com/watch?v=jNQXAC9IVRw&list=PLfixture', playlist: true });
    assert.equal(watch.ok, true);
    assert.equal(env2.nativeCalls.at(-1).message.url, PLAYLIST);
    const count = env2.nativeCalls.length;
    assert.equal((await env2.send({ type: 'sg-deliver', mode: 'direct', url: 'https://www.youtube.com/@fixture/videos', playlist: true })).ok, false);
    assert.equal((await env2.send({ type: 'sg-deliver', mode: 'direct', url: YOUTUBE, playlist: true })).ok, false);
    assert.equal((await env2.send({ type: 'sg-deliver', mode: 'direct', url: PLAYLIST, items: '1-3' })).ok, false);
    assert.equal(env2.nativeCalls.length, count);
    const stored = env2.db.sg_history_v2.find(job => job.id === accepted.jobId);
    assert.equal(stored.request.playlist, true);
    assert.equal(stored.request.items, '1-10,15');
    env2.jobs.get(accepted.jobId).state = 'failed';
    await env2.send({ type: 'sg-history-refresh' });
    assert.equal((await env2.send({ type: 'sg-job-retry', jobId: accepted.jobId, platform: 'youtube' })).ok, true);
    assert.equal(env2.nativeCalls.at(-1).message.playlist, true);
    assert.equal(env2.nativeCalls.at(-1).message.items, '1-10,15');
    assert.equal(env2.nativeCalls.at(-1).message.media, 'audio-m4a');
  });
  await test('retry never infers playlist consent from a URL when the original request omitted or disabled it', async () => {
    for (const playlist of [undefined, false]) {
      const request = { url: PLAYLIST, mode: 'direct', media: 'video', audioQuality: 'auto', quality: 'best', part: 'current', dir: '', subtitles: 'off', subLangs: 'zh.*,en', metadata: false };
      if (playlist !== undefined) request.playlist = playlist;
      const record = { id: 'e'.repeat(32), platform: 'youtube', url: PLAYLIST, state: 'canceled', media: 'video', request };
      const env2 = setup({ sg_history_v2: [record] });
      assert.equal((await env2.send({ type: 'sg-job-retry', jobId: record.id, platform: 'youtube' })).ok, false);
      assert.equal(env2.nativeCalls.length, 0);
      assert.deepEqual(env2.db.sg_history_v2, [record]);
    }
  });
  await test('Bilibili collection part is accepted for video and bangumi when advertised', async () => {
    const env2 = setup({ sg_settings: { part: 'collection' } });
    assert.equal((await env2.send({ type: 'sg-deliver', mode: 'direct', url: BILI })).ok, true);
    assert.equal(env2.nativeCalls.at(-1).message.part, 'collection');
    assert.equal((await env2.send({ type: 'sg-deliver', mode: 'direct', url: BANGUMI })).ok, true);
    assert.equal(env2.nativeCalls.at(-1).message.part, 'collection');
    assert.equal((await env2.send({ type: 'sg-deliver', mode: 'direct', url: YOUTUBE })).ok, true);
    assert.equal(env2.nativeCalls.at(-1).message.part, 'current');
    assert.equal((await env2.send({ type: 'sg-deliver', mode: 'direct', url: BILI, part: 'episodes' })).ok, false);
  });
  await test('cancel validates history, calls only cancel-job with jobId and records the request', async () => {
    const env2 = setup();
    env2.permit();
    await env2.send({ type: 'sg-settings-set', settings: { notify: true } });
    const accepted = await env2.send({ type: 'sg-deliver', mode: 'direct', url: YOUTUBE, sponsorblock: 'all', section: '1-9' });
    const result = await env2.send({ type: 'sg-job-cancel', jobId: accepted.jobId, platform: 'youtube' });
    assert.equal(result.ok, true);
    const call = env2.nativeCalls.filter(c => c.message.action === 'cancel-job');
    assert.equal(call.length, 1);
    assert.equal(call[0].host, SG_HOST);
    assert.deepEqual(call[0].message, { action: 'cancel-job', jobId: accepted.jobId });
    let stored = env2.db.sg_history_v2.find(job => job.id === accepted.jobId);
    assert.equal(stored.cancelRequested, true);
    assert.equal(stored.state, 'downloading');
    env2.jobs.get(accepted.jobId).state = 'canceled';
    env2.jobs.get(accepted.jobId).error = '已取消下载。';
    await env2.send({ type: 'sg-history-refresh' });
    await env2.send({ type: 'sg-history-refresh' });
    stored = env2.db.sg_history_v2.find(job => job.id === accepted.jobId);
    assert.equal(stored.state, 'canceled');
    assert.equal(env2.notifications.length, 0, 'canceled jobs must not notify');
    const count = env2.nativeCalls.length;
    assert.equal((await env2.send({ type: 'sg-job-cancel', jobId: accepted.jobId, platform: 'youtube' })).ok, false, 'ended jobs cannot be canceled');
    assert.equal((await env2.send({ type: 'sg-job-cancel', jobId: 'f'.repeat(32), platform: 'youtube' })).ok, false);
    assert.equal((await env2.send({ type: 'sg-job-cancel', jobId: '../x', platform: 'youtube' })).ok, false);
    assert.equal(env2.nativeCalls.length, count);
    const retried = await env2.send({ type: 'sg-job-retry', jobId: accepted.jobId, platform: 'youtube' });
    assert.equal(retried.ok, true);
    assert.equal(env2.nativeCalls.at(-1).message.sponsorblock, 'all');
    assert.equal(env2.nativeCalls.at(-1).message.section, '1-9');
    const cleared = await env2.send({ type: 'sg-history-clear' });
    assert.equal(cleared.jobs.some(job => job.id === accepted.jobId), false);
    assert.ok(cleared.jobs.some(job => job.id === retried.jobId));
    const bili = await env2.send({ type: 'sg-deliver', mode: 'direct', url: BILI });
    assert.equal((await env2.send({ type: 'sg-job-cancel', jobId: bili.jobId, platform: 'bili' })).ok, true);
    assert.equal(env2.nativeCalls.filter(c => c.message.action === 'cancel-job').at(-1).host, BILI_HOST);
  });
  await test('cancel refuses hosts without the cancel feature and legacy retries default new fields off', async () => {
    const old = setup({ sg_settings: { sponsorblock: 'all', danmaku: 'xml' } }, { legacyFeatures: true });
    const accepted = await old.send({ type: 'sg-deliver', mode: 'direct', url: XURL });
    assert.equal(accepted.ok, true);
    const result = await old.send({ type: 'sg-job-cancel', jobId: accepted.jobId, platform: 'x' });
    assert.equal(result.ok, false);
    assert.match(result.error, /请更新本机组件后使用/);
    assert.equal(old.nativeCalls.filter(c => c.message.action === 'cancel-job').length, 0);
    const legacy = setup({ sg_settings: { sponsorblock: 'all', danmaku: 'xml' }, sg_history_v2: [
      { id: 'c'.repeat(32), platform: 'youtube', url: YOUTUBE, state: 'canceled', media: 'video', request: { media: 'video', audioQuality: 'auto' } },
      { id: 'd'.repeat(32), platform: 'bili', url: BILI, state: 'failed', media: 'video', request: { media: 'video', audioQuality: 'auto', part: 'current' } }
    ] }, { legacyFeatures: true });
    assert.equal((await legacy.send({ type: 'sg-job-retry', jobId: 'c'.repeat(32), platform: 'youtube' })).ok, true);
    assert.equal('sponsorblock' in legacy.nativeCalls.at(-1).message, false);
    assert.equal((await legacy.send({ type: 'sg-job-retry', jobId: 'd'.repeat(32), platform: 'bili' })).ok, true);
    assert.equal('danmaku' in legacy.nativeCalls.at(-1).message, false);
  });
  await test('pick-folder passes initialDir only for a saved directory on hosts advertising pick-folder-initial', async () => {
    const env2 = setup();
    await env2.send({ type: 'sg-pick-folder', host: 'x', initialDir: 'D:\\Ignored' });
    assert.deepEqual(env2.nativeCalls.filter(c => c.message.action === 'pick-folder').at(-1).message, { action: 'pick-folder' }, 'default dir must omit initialDir');
    await env2.send({ type: 'sg-pick-folder', host: 'bili', initialDir: 'D:\\合成测试\\Folder with spaces' });
    const picked = env2.nativeCalls.filter(c => c.message.action === 'pick-folder').at(-1);
    assert.equal(picked.host, BILI_HOST);
    assert.deepEqual(picked.message, { action: 'pick-folder', initialDir: 'D:\\合成测试\\Folder with spaces' });
    await env2.send({ type: 'sg-pick-folder', host: 'x', initialDir: 'D:\\bad\u0001path' });
    assert.deepEqual(env2.nativeCalls.filter(c => c.message.action === 'pick-folder').at(-1).message, { action: 'pick-folder' });
    const old = setup({ sg_settings: { dir: 'D:\\Saved' } }, { legacyFeatures: true });
    const result = await old.send({ type: 'sg-pick-folder', host: 'x', initialDir: 'D:\\Saved' });
    assert.equal(result.ok, true);
    assert.deepEqual(old.nativeCalls.filter(c => c.message.action === 'pick-folder').at(-1).message, { action: 'pick-folder' });
  });
  await test('bridge ping returns only UI fields and removes retired login claims and capabilities', async () => {
    const marker = 'synthetic-private-value';
    const env2 = setup({}, { pingExtra: {
      [SG_HOST]: {
        login: { youtube: 'signed-in', x: marker, bilibili: 'signed-in', extra: 'guest' },
        cookie: marker, token: marker, password: marker, details: { value: marker }, appRoot: 'D:\\Synthetic private root',
        mediaOptions: ['video', marker], audioQualityOptions: ['auto', marker]
      },
      [BILI_HOST]: { login: { bilibili: 'guest', youtube: 'signed-in', x: 'signed-in' }, features: ['cancel', '<b>', 7], biliRoot: 'D:\\Synthetic private root', extra: marker }
    } });
    const sg = await env2.send({ type: 'sg-bridge-ping', host: 'x' });
    assert.deepEqual(sg, { ok: true, version: 'test', engineAvailable: true, features: FEATURES[SG_HOST].filter(f => !['live', 'login-status'].includes(f)), login: {} });
    const bili = await env2.send({ type: 'sg-bridge-ping', host: 'bili' });
    assert.deepEqual(bili, { ok: true, version: 'test', engineAvailable: true, features: ['cancel'], login: {} });
    assert.equal(JSON.stringify([sg, bili]).includes(marker), false);
    const malformed = setup({}, { pingExtra: { [SG_HOST]: { version: { value: marker }, engineAvailable: marker }, [BILI_HOST]: { login: { bilibili: 'expired' } } } });
    const invalid = await malformed.send({ type: 'sg-bridge-ping', host: 'x' });
    assert.equal('version' in invalid, false);
    assert.equal('engineAvailable' in invalid, false);
    assert.deepEqual((await malformed.send({ type: 'sg-bridge-ping', host: 'bili' })).login, {});
  });
  await test('job status keeps fileCount and only contracted warnings', async () => {
    const env2 = setup();
    const accepted = await env2.send({ type: 'sg-deliver', mode: 'direct', url: PLAYLIST, playlist: true });
    const warnings = ['部分条目下载失败。', '弹幕下载失败，视频已保存。', '字幕下载失败，视频已保存。', '该视频不属于合集，已按全部分 P 下载。', '已达到直播录制时长上限。'];
    Object.assign(env2.jobs.get(accepted.jobId), { state: 'completed', fileCount: 37, warnings: ['synthetic-private-value', warnings[0], 5, warnings[1], 'x'.repeat(900), ...warnings.slice(2)], files: ['D:\\a.mp4'] });
    await env2.send({ type: 'sg-history-refresh' });
    const stored = env2.db.sg_history_v2.find(job => job.id === accepted.jobId);
    assert.equal(stored.fileCount, 37);
    assert.deepEqual(stored.warnings, warnings);
  });
  await test('practical options persist, reach YouTube and X, and remain off for Bilibili', async () => {
    const env2 = setup();
    const settings = { embedThumbnail: true, splitChapters: true, downloadArchive: true, limitRate: '1M', concurrentFragments: '4' };
    const saved = await env2.send({ type: 'sg-settings-set', settings });
    for (const [key, value] of Object.entries(settings)) assert.equal(saved.settings[key], value);
    for (const url of [YOUTUBE, XURL]) {
      const result = await env2.send({ type: 'sg-deliver', mode: 'direct', url });
      assert.equal(result.ok, true, result.error);
      for (const [key, value] of Object.entries(settings)) {
        assert.equal(env2.nativeCalls.at(-1).message[key], value);
        assert.equal(env2.db.sg_history_v2.find(job => job.id === result.jobId).request[key], value);
      }
    }
    assert.equal((await env2.send({ type: 'sg-deliver', mode: 'direct', url: BILI })).ok, true);
    for (const key of Object.keys(settings)) assert.equal(key in env2.nativeCalls.at(-1).message, false);
    for (const patch of [{ embedThumbnail: 'true' }, { splitChapters: 1 }, { downloadArchive: null }, { limitRate: '1M --exec bad' }, { concurrentFragments: 4 }]) {
      const before = env2.nativeCalls.length;
      assert.equal((await env2.send({ type: 'sg-deliver', mode: 'direct', url: YOUTUBE, ...patch })).ok, false);
      assert.equal(env2.nativeCalls.length, before);
    }
  });
  await test('practical options require supported hosts and legacy retries never inherit them', async () => {
    const old = setup({}, { legacyFeatures: true });
    for (const patch of [{ embedThumbnail: true }, { splitChapters: true }, { downloadArchive: true }, { limitRate: '5M' }, { concurrentFragments: '8' }]) {
      assert.equal((await old.send({ type: 'sg-deliver', mode: 'direct', url: YOUTUBE, ...patch })).ok, false);
      assert.equal(old.nativeCalls.some(call => call.message.action === 'yt-download'), false);
    }
    const env2 = setup();
    const accepted = await env2.send({ type: 'sg-deliver', mode: 'direct', url: YOUTUBE });
    Object.assign(env2.db.sg_history_v2[0], { state: 'failed' });
    await env2.send({ type: 'sg-settings-set', settings: { embedThumbnail: true, splitChapters: true, downloadArchive: true, limitRate: '10M', concurrentFragments: '16' } });
    assert.equal((await env2.send({ type: 'sg-job-retry', jobId: accepted.jobId, platform: 'youtube' })).ok, true);
    for (const key of ['embedThumbnail', 'splitChapters', 'downloadArchive', 'limitRate', 'concurrentFragments']) assert.equal(key in env2.nativeCalls.at(-1).message, false);
  });
  await test('open file only routes a known task with a valid recorded index and host capability', async () => {
    const env2 = setup();
    const accepted = await env2.send({ type: 'sg-deliver', mode: 'direct', url: BILI });
    Object.assign(env2.db.sg_history_v2[0], { state: 'completed', files: ['D:\\Default\\video.mp4'] });
    assert.equal((await env2.send({ type: 'sg-job-open-file', jobId: accepted.jobId, platform: 'bili', fileIndex: 0 })).ok, true);
    assert.deepEqual(env2.nativeCalls.at(-1), { host: BILI_HOST, message: { action: 'open-job-file', jobId: accepted.jobId, fileIndex: 0, filePath: 'D:\\Default\\video.mp4' } });
    assert.equal((await env2.send({ type: 'sg-job-open-file', jobId: accepted.jobId, platform: 'bili', fileIndex: 0, filePath: 'D:\\Elsewhere\\other.mp4' })).ok, false);
    for (const fileIndex of [-1, 1, '0', null]) assert.equal((await env2.send({ type: 'sg-job-open-file', jobId: accepted.jobId, platform: 'bili', fileIndex })).ok, false);
    assert.equal((await env2.send({ type: 'sg-job-open-file', jobId: 'f'.repeat(32), platform: 'bili', fileIndex: 0 })).ok, false);
  });
  await test('retired live and XML requests are rejected on every platform before dispatch or history mutation', async () => {
    const env2 = setup();
    for (const url of [YOUTUBE, XURL, BILI, PLAYLIST]) {
      for (const retired of [{ live: 'from-start' }, { danmaku: 'xml' }]) {
        const before = env2.nativeCalls.length;
        const result = await env2.send({ type: 'sg-deliver', mode: 'direct', url, ...(url === PLAYLIST ? { playlist: true } : {}), ...retired });
        assert.equal(result.ok, false);
        assert.match(result.error, /已停用/);
        assert.equal(env2.nativeCalls.length, before);
        assert.equal(env2.db.sg_history_v2, undefined);
      }
    }
  });
  await test('retired preferences normalize off without altering other choices and old retries cannot silently change output', async () => {
    const env2 = setup({ sg_settings: { danmaku: 'xml', liveMinutes: '720', media: 'audio-m4a', audioQuality: '256', dir: 'D:\\Chosen' } });
    const saved = (await env2.send({ type: 'sg-settings-get' })).settings;
    assert.equal(saved.danmaku, 'off'); assert.equal(saved.liveMinutes, '240');
    assert.equal(saved.dir, 'D:\\Chosen'); assert.equal(saved.audioQuality, '256');
    const accepted = await env2.send({ type: 'sg-deliver', mode: 'direct', url: YOUTUBE });
    const job = env2.db.sg_history_v2[0];
    for (const retired of [{ live: 'from-start' }, { danmaku: 'xml' }]) {
      job.state = 'failed'; job.request = { ...job.request, live: 'off', danmaku: 'off', ...retired };
      const before = env2.nativeCalls.length;
      const result = await env2.send({ type: 'sg-job-retry', jobId: accepted.jobId, platform: 'youtube' });
      assert.equal(result.ok, false); assert.match(result.error, /已停用/);
      assert.equal(env2.nativeCalls.length, before);
    }
    job.state = 'downloading';
    const canceled = await env2.send({ type: 'sg-job-cancel', jobId: accepted.jobId, platform: 'youtube' });
    assert.equal(canceled.ok, true, canceled.error);
    assert.equal(env2.nativeCalls.some(call => call.message.action === 'cancel-job'), true);
  });
  console.log(`\n${cases} Veylo background regression cases passed.`);
})().catch(error => { console.error(error); process.exitCode = 1; });
