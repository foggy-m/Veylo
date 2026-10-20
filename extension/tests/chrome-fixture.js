/* Synthetic local-only Chrome API fixture. No cookies, real paths, native hosts or network. */
(function (global) {
  'use strict';
  const DEFAULTS = {
    dir: 'default', recentDirs: [], quality: 'best', qualityBili: 'best', part: 'current',
    media: 'video', audioQuality: 'auto', subtitles: 'off', subLangs: 'zh.*,en', metadata: false, notify: false, theme: 'auto',
    sponsorblock: 'off', danmaku: 'off', biliSubtitles: 'off', liveMinutes: '240'
  };
  // Synthetic 2.3.0 host capabilities. The fixture defaults to legacy hosts (no features) unless hosts: 'current'.
  const FEATURES = {
    x: ['cancel', 'sponsorblock', 'sections', 'playlist', 'live', 'pick-folder-initial', 'login-status', 'thumbnail', 'chapters', 'archive', 'transfer', 'open-file'],
    bili: ['cancel', 'danmaku', 'subtitles', 'collection', 'pick-folder-initial', 'login-status', 'open-file']
  };
  const LOGIN = { x: { youtube: 'signed-in', x: 'guest' }, bili: { bilibili: 'signed-in' } };
  const URLS = {
    youtube: 'https://www.youtube.com/watch?v=aqz-KE-bpKQ',
    playlist: 'https://www.youtube.com/playlist?list=PL_VEYLO_DEMO_2026',
    channel: 'https://www.youtube.com/@veylo-demo/videos',
    x: 'https://x.com/veylo_demo/status/1000000000000000001',
    bili: 'https://www.bilibili.com/video/BV1xx411c7mD?p=2',
    bangumi: 'https://www.bilibili.com/bangumi/play/ep123456',
    playlistVideo: 'https://www.youtube.com/watch?v=aqz-KE-bpKQ&list=PL_VEYLO_DEMO_2026'
  };
  const TITLES = {
    youtube: 'A slower kind of Sunday — a short film',
    x: 'Small moments, worth keeping.',
    bili: '把普通日子，留成喜欢的片段 · 第二集'
  };
  const LONG_PATH = 'D:\\Veylo Demo (synthetic)\\收藏与灵感\\2026\\十月与远方\\这是刻意构造的超长保存目录用于测试截断与完整路径提示';
  const clone = value => JSON.parse(JSON.stringify(value));
  const stamp = '2026-10-15T09:30:00.000Z';

  function makeJobs() {
    return [
      { id: 'demo-bili-active', platform: 'bili', url: URLS.bili, title: TITLES.bili, downloadDir: LONG_PATH, state: 'downloading', percent: 46, media: 'video', createdAt: stamp, updatedAt: stamp },
      { id: 'demo-youtube-done', platform: 'youtube', url: URLS.youtube, title: TITLES.youtube, downloadDir: 'D:\\Veylo Demo (synthetic)\\Films', state: 'completed', percent: 100, media: 'video', createdAt: '2026-10-15T08:00:00.000Z', updatedAt: stamp, files: ['a-slower-sunday.mp4'] },
      { id: 'demo-x-failed', platform: 'x', url: URLS.x, title: TITLES.x, downloadDir: 'D:\\Veylo Demo (synthetic)\\Clips', state: 'failed', percent: 0, error: '合成失败：本机下载内核暂不可用。修复连接后可重试。', media: 'audio-mp3', request: { url: URLS.x, mode: 'direct', media: 'audio-mp3', audioQuality: '256' }, createdAt: '2026-10-14T18:10:00.000Z', updatedAt: stamp },
      { id: 'demo-youtube-queued', platform: 'youtube', url: URLS.youtube, title: 'Notes from the coast / 海边随记', downloadDir: 'D:\\Veylo Demo (synthetic)\\Audio', state: 'queued', percent: 0, media: 'audio-m4a', request: { url: URLS.youtube, mode: 'direct', media: 'audio-m4a', audioQuality: 'auto' }, createdAt: '2026-10-15T09:25:00.000Z', updatedAt: stamp }
    ];
  }

  function create(config = {}) {
    const store = {
      settings: { ...clone(DEFAULTS), ...clone(config.settings || {}) },
      jobs: clone(config.jobs === undefined ? makeJobs() : config.jobs),
      calls: [], copied: '', opened: [], errors: {},
      delays: { ...(config.delays || {}) },
      permissionGranted: config.permissionGranted === true,
      permissionDecision: config.permissionDecision !== false,
      engineAvailable: { x: true, bili: true, ...(config.engineAvailable || {}) },
      pickCanceled: false, nextPath: LONG_PATH,
      features: config.hosts === 'current' ? clone(FEATURES) : { x: [], bili: [], ...clone(config.features || {}) },
      login: { ...clone(LOGIN), ...clone(config.login || {}) }, pingExtra: clone(config.pingExtra || {}),
      cancelImmediate: config.cancelImmediate === true,
      activeTab: { url: URLS[config.platform || 'bili'], title: TITLES[config.platform || 'bili'] || '' },
      sequence: 0, inFlightHistory: 0, maxHistoryInFlight: 0, snapshotHistory: false
    };
    const messageListeners = new Set();

    function deliver(payload, retryJob) {
      if (payload.mode === 'handoff') return { ok: true, launched: payload.url.includes('bilibili'), url: payload.url };
      const info = global.SG?.classify(payload.url) || { platform: payload.url.includes('bilibili') ? 'bili' : payload.url.includes('x.com') ? 'x' : 'youtube' };
      const job = {
        id: `demo-created-${++store.sequence}`, platform: info.platform, url: payload.url,
        title: payload.title || retryJob?.title || TITLES[info.platform],
        downloadDir: payload.dir && payload.dir !== 'default' ? payload.dir : 'D:\\Veylo Demo (synthetic)\\Downloads',
        state: 'queued', percent: 0, media: payload.media || retryJob?.media || 'video',
        request: { ...clone(payload), audioQuality: payload.audioQuality || 'auto' }, createdAt: stamp, updatedAt: stamp
      };
      store.jobs.unshift(job);
      return { ok: true, jobId: job.id, platform: job.platform, downloadDir: job.downloadDir, url: job.url, cookieStatus: 'guest', login: false };
    }

    function route(payload) {
      if (store.errors[payload.type]) return { ok: false, error: store.errors[payload.type] };
      switch (payload.type) {
        case 'sg-settings-get': return { ok: true, settings: clone(store.settings) };
        case 'sg-settings-set': {
          const patch = clone(payload.settings || {});
          if ('media' in patch && !['video', 'audio-original', 'audio-mp3', 'audio-m4a'].includes(patch.media)) patch.media = 'video';
          if (patch.notify === true && !store.permissionGranted) patch.notify = false;
          Object.assign(store.settings, patch);
          return { ok: true, settings: clone(store.settings) };
        }
        case 'sg-pick-folder': {
          if (store.pickCanceled) return { canceled: true };
          store.settings.dir = store.nextPath;
          store.settings.recentDirs = [...new Set([store.nextPath, ...store.settings.recentDirs])];
          return { ok: true, path: store.nextPath, recentDirs: clone(store.settings.recentDirs), settings: clone(store.settings) };
        }
        case 'sg-copy': store.copied = payload.url; return { ok: true };
        case 'sg-deliver': return deliver(payload);
        case 'sg-history-get':
        case 'sg-history-refresh': return { ok: true, jobs: clone(store.jobs.slice(0, 60)) };
        case 'sg-history-clear':
          store.jobs = store.jobs.filter(job => !['completed', 'failed', 'canceled'].includes(job.state));
          return { ok: true, jobs: clone(store.jobs) };
        case 'sg-job-retry': {
          const job = store.jobs.find(item => item.id === payload.jobId && item.platform === payload.platform);
          if (!job || !['failed', 'canceled'].includes(job.state)) return { ok: false, error: '只有失败或已取消的任务可以重新下载' };
          return deliver({ ...job.request, url: job.url, title: job.title, dir: job.downloadDir, media: job.media, audioQuality: job.request?.audioQuality ?? 'auto', mode: 'direct' }, job);
        }
        case 'sg-job-cancel': {
          const job = store.jobs.find(item => item.id === payload.jobId && item.platform === payload.platform);
          if (!job || !['queued', 'downloading'].includes(job.state)) return { ok: false, error: '只有进行中的任务可以取消' };
          if (!store.features[job.platform === 'bili' ? 'bili' : 'x'].includes('cancel')) return { ok: false, error: '请更新本机组件后使用“取消下载”' };
          job.cancelRequested = true;
          if (store.cancelImmediate) { job.state = 'canceled'; job.error = '已取消下载。'; }
          return { ok: true, jobs: clone(store.jobs.slice(0, 60)) };
        }
        case 'sg-job-open-folder': store.opened.push({ jobId: payload.jobId, platform: payload.platform }); return { ok: true };
        case 'sg-job-open-file': store.opened.push({ jobId: payload.jobId, platform: payload.platform, fileIndex: payload.fileIndex }); return { ok: true };
        case 'sg-job-status': return { ok: true, job: clone(store.jobs.find(job => job.id === payload.jobId) || null) };
        case 'sg-status': return { ok: true, job: clone(store.jobs[0] || null), last: null };
        case 'sg-bridge-ping': {
          const host = payload.host === 'bili' ? 'bili' : 'x';
          return {
            ok: true, engineAvailable: store.engineAvailable[host] !== false, version: '2.0-demo',
            features: clone(store.features[host]), login: clone(store.login[host]),
            ...clone(store.pingExtra[host] || {})
          };
        }
        default: return { ok: false, error: `未知的合成 API：${payload.type}` };
      }
    }

    const chrome = {
      runtime: {
        lastError: null,
        getURL(path) { return new URL(`../${path}`, config.baseURL || global.location?.href || 'http://localhost/tests/ui-demo.html').href; },
        sendMessage(payload, callback) {
          const copy = clone(payload);
          store.calls.push(copy);
          const history = ['sg-history-get', 'sg-history-refresh'].includes(copy.type);
          if (history) {
            store.inFlightHistory += 1;
            store.maxHistoryInFlight = Math.max(store.maxHistoryInFlight, store.inFlightHistory);
          }
          // Settings responses are snapshotted before the delay to reproduce stale-response races.
          const response = ['sg-settings-get', 'sg-settings-set'].includes(copy.type) || history && store.snapshotHistory ? route(copy) : null;
          const delay = typeof store.delays[copy.type] === 'function' ? store.delays[copy.type](copy) : store.delays[copy.type] || 0;
          setTimeout(() => {
            if (history) store.inFlightHistory -= 1;
            callback(clone(response || route(copy)));
          }, delay);
        },
        onMessage: {
          addListener(listener) { messageListeners.add(listener); },
          removeListener(listener) { messageListeners.delete(listener); }
        }
      },
      tabs: { query(_query, callback) { setTimeout(() => callback([clone(store.activeTab)]), 0); } },
      permissions: {
        request(details, callback) {
          store.calls.push({ type: 'permissions.request', ...clone(details) });
          const granted = store.permissionDecision;
          store.permissionGranted = granted;
          setTimeout(() => callback(granted), 0);
          return undefined;
        },
        contains(_details, callback) { setTimeout(() => callback(store.permissionGranted), 0); }
      }
    };
    return {
      chrome, store,
      emit(message) { for (const listener of messageListeners) listener(message, {}, () => {}); },
      resetJobs(mode = 'mixed') {
        store.jobs = mode === 'empty' ? [] : mode === 'failed' ? makeJobs().filter(job => job.state === 'failed') : mode === 'active' ? makeJobs().filter(job => ['queued', 'downloading'].includes(job.state)) : makeJobs();
      },
      count(type) { return store.calls.filter(call => call.type === type).length; },
      last(type) { return [...store.calls].reverse().find(call => call.type === type); }
    };
  }
  global.VeyloFixture = Object.freeze({ create, URLS, TITLES, LONG_PATH, makeJobs, DEFAULTS, FEATURES });
})(globalThis);
