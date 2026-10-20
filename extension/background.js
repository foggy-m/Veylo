importScripts('shared.js');

const CLIPBOARD_KEY = 'sg_last_clip';
const JOB_KEY = 'sg_last_job';
const SETTINGS_KEY = 'sg_settings';
const HISTORY_KEY = 'sg_history_v2';
const NATIVE_HOST = 'com.shiguang.fluentytdl_bridge';
const BILI_NATIVE_HOST = 'com.shiguang.bili_bridge';
const POLL_ALARM = 'veylo-active-jobs';
const MENU_ID = 'veylo-open-download';
const HISTORY_LIMIT = 60;
const ACTIVE_STATES = new Set(['queued', 'downloading']);
const END_STATES = new Set(['completed', 'failed', 'canceled']);
const JOB_WARNINGS = new Set([
  '弹幕下载失败，视频已保存。', '字幕下载失败，视频已保存。',
  '部分条目下载失败。', '该视频不属于合集，已按全部分 P 下载。', '已达到直播录制时长上限。',
  '视频已存在，已跳过重复下载。', '没有可用封面，媒体已保存。'
]);
const DEFAULT_SETTINGS = Object.freeze({
  dir: 'default', recentDirs: [], quality: 'best', qualityBili: 'best', part: 'current',
  media: 'video', audioQuality: 'auto', subtitles: 'off', subLangs: 'zh.*,en', metadata: false, notify: false, theme: 'auto',
  sponsorblock: 'off', danmaku: 'off', biliSubtitles: 'off', liveMinutes: '240',
  embedThumbnail: false, splitChapters: false, downloadArchive: false, limitRate: 'off', concurrentFragments: '1'
});
const SPONSORBLOCK = ['off', 'sponsor', 'all'];
const DANMAKU = ['off', 'ass'];
const BILI_SUBTITLES = ['off', 'srt'];
// Neutral legacy fields remain readable in historical ordinary requests.
const LIVE = ['off'];
const LIVE_MINUTES = ['60', '120', '240', '480', '720'];
const ITEMS_SPEC = /^[0-9]{1,5}(-[0-9]{0,5})?(,[0-9]{1,5}(-[0-9]{0,5})?){0,19}$/;
const FEATURE_LABELS = {
  thumbnail: '嵌入封面', chapters: '按章节拆分', archive: '防止重复下载', transfer: '限速与并发分片', 'open-file': '打开文件',
  cancel: '取消下载', sponsorblock: '跳过片段', sections: '片段下载', playlist: '列表下载',
  danmaku: '弹幕', subtitles: 'B 站字幕', collection: '合集下载', 'pick-folder-initial': '打开当前文件夹'
};

// Same grammar as the UI and the native hosts: SS, MM:SS or HH:MM:SS; end may be "inf".
function sectionSeconds(token, end) {
  if (end && token === 'inf') return Infinity;
  const parts = token.split(':');
  if (parts.length === 1) return /^\d{1,5}$/.test(parts[0]) ? Number(parts[0]) : NaN;
  if (parts.length === 2 && /^\d{1,2}$/.test(parts[0]) && /^\d{1,2}$/.test(parts[1]) && Number(parts[1]) < 60) return Number(parts[0]) * 60 + Number(parts[1]);
  if (parts.length === 3 && parts.every(p => /^\d{1,2}$/.test(p)) && Number(parts[1]) < 60 && Number(parts[2]) < 60) return Number(parts[0]) * 3600 + Number(parts[1]) * 60 + Number(parts[2]);
  return NaN;
}

function normalizeSection(value) {
  if (typeof value !== 'string') return null;
  const text = value.replace(/\s+/g, '');
  if (!text) return '';
  if (text.length > 32) return null;
  const match = /^([0-9:]+)-([0-9:]+|inf)$/.exec(text);
  if (!match) return null;
  const start = sectionSeconds(match[1], false);
  const end = sectionSeconds(match[2], true);
  if (!Number.isFinite(start) || Number.isNaN(end) || start < 0 || start >= end || (end !== Infinity && end > 86400)) return null;
  return text;
}
let creatingOffscreen;
let historyRefresh;
let storageQueue = Promise.resolve();

function exclusive(work) {
  const result = storageQueue.then(work);
  storageQueue = result.catch(() => {});
  return result;
}

function pathText(value) {
  return typeof value === 'string' && value.length <= 4096 && !/[\u0000-\u001f]/.test(value);
}

function safeSettings(value) {
  const raw = value && typeof value === 'object' ? value : {};
  const out = { ...DEFAULT_SETTINGS, recentDirs: [] };
  if (pathText(raw.dir) && raw.dir && raw.dir !== 'browse') out.dir = raw.dir;
  if (Array.isArray(raw.recentDirs)) out.recentDirs = [...new Set(raw.recentDirs.filter(p => pathText(p) && p && p !== 'default' && p !== 'browse'))].slice(0, 5);
  const choices = {
    quality: ['best', '1080', '720', '480'], qualityBili: ['best', '120', '112', '80', '64', '32', '16'],
    part: ['current', 'all', 'collection'], media: ['video', 'audio-original', 'audio-mp3', 'audio-m4a'], audioQuality: ['auto', '320', '256', '192', '128'],
    subtitles: ['off', 'manual', 'auto'], subLangs: ['zh.*,en', 'zh.*', 'en'], theme: ['auto', 'light', 'dark'],
    sponsorblock: SPONSORBLOCK, danmaku: DANMAKU, biliSubtitles: BILI_SUBTITLES, liveMinutes: ['240'],
    limitRate: ['off', '512K', '1M', '5M', '10M'], concurrentFragments: ['1', '4', '8', '16']
  };
  for (const [key, values] of Object.entries(choices)) if (values.includes(raw[key])) out[key] = raw[key];
  out.metadata = raw.metadata === true;
  out.notify = raw.notify === true;
  for (const key of ['embedThumbnail', 'splitChapters', 'downloadArchive']) out[key] = raw[key] === true;
  return out;
}

async function getSettings() {
  const data = await chrome.storage.local.get([SETTINGS_KEY]);
  return safeSettings(data[SETTINGS_KEY]);
}

async function saveSettings(patch) {
  return exclusive(async () => {
    const current = await getSettings();
    const allowed = {};
    for (const key of Object.keys(DEFAULT_SETTINGS)) {
      if (key !== 'recentDirs' && Object.prototype.hasOwnProperty.call(patch || {}, key)) allowed[key] = patch[key];
    }
    const settings = safeSettings({ ...current, ...allowed });
    if (settings.notify && !(await notificationPermission())) settings.notify = false;
    await chrome.storage.local.set({ [SETTINGS_KEY]: settings });
    return { ok: true, settings };
  });
}

async function sendToClipboard(text) {
  if (!creatingOffscreen) {
    creatingOffscreen = (async () => {
      const contexts = await chrome.runtime.getContexts({
        contextTypes: ['OFFSCREEN_DOCUMENT'], documentUrls: [chrome.runtime.getURL('offscreen.html')]
      });
      if (!contexts.length) await chrome.offscreen.createDocument({
        url: 'offscreen.html', reasons: ['CLIPBOARD'], justification: '按用户操作复制视频链接，或交给本机下载软件解析'
      });
    })().finally(() => { creatingOffscreen = null; });
  }
  await creatingOffscreen;
  const response = await chrome.runtime.sendMessage({ type: 'sg-clipboard-write', text });
  if (response?.ok !== true) throw new Error('写入剪贴板失败，请打开 Veylo 面板重试');
}

function native(message, host = NATIVE_HOST) {
  return new Promise((resolve, reject) => {
    chrome.runtime.sendNativeMessage(host, message, result => {
      const lastError = chrome.runtime.lastError;
      if (lastError) reject(new Error(host === BILI_NATIVE_HOST
        ? 'B 站本机连接不可用，请运行项目目录中的 bilibili/安装Bili下载助手.bat 后重试。'
        : 'YouTube / X 本机连接不可用，请运行项目目录中的 安装浏览器助手.bat 后重试。'));
      else if (result?.ok !== true) reject(new Error(result?.error || '本机下载组件返回错误'));
      else resolve(result);
    });
  });
}

function hostFor(platform) { return platform === 'bili' ? BILI_NATIVE_HOST : NATIVE_HOST; }
function validId(id) { return typeof id === 'string' && /^[a-f0-9]{32}$/.test(id); }
function jobKey(job) { return `${job.platform}:${job.id}`; }
function jobIdentity(id, platform) {
  if (!validId(id) || !['youtube', 'x', 'bili'].includes(platform)) throw new Error('任务信息无效，请重新打开面板');
}
function titleText(value) {
  return typeof value === 'string' ? value.replace(/[\u0000-\u001f]/g, ' ').trim().slice(0, 220) : '';
}
function fallbackTitle(info) {
  const url = new URL(info.url);
  if (info.platform === 'youtube') return `YouTube · ${url.searchParams.get('v') || SG.kindLabel(info.kind)}`;
  if (info.platform === 'x') return `X · ${url.pathname.split('/').filter(Boolean).slice(0, 3).join(' / ')}`;
  return `B 站 · ${url.pathname.split('/').filter(Boolean).pop()}`;
}

function trimHistory(jobs) {
  const ordered = [...jobs].sort((a, b) => b.createdAt - a.createdAt);
  const active = ordered.filter(j => ACTIVE_STATES.has(j.state));
  const ended = ordered.filter(j => !ACTIVE_STATES.has(j.state)).slice(0, Math.max(0, HISTORY_LIMIT - active.length));
  return [...active, ...ended].sort((a, b) => b.createdAt - a.createdAt);
}

async function getHistory() {
  const data = await chrome.storage.local.get([HISTORY_KEY, JOB_KEY]);
  if (Array.isArray(data[HISTORY_KEY])) return trimHistory(data[HISTORY_KEY].filter(j =>
    j && validId(j.id) && ['youtube', 'x', 'bili'].includes(j.platform) && SG.classify(j.url)));
  const last = data[JOB_KEY];
  if (!last || !validId(last.id) || !SG.classify(last.url)) return [];
  const info = SG.classify(last.url);
  const settings = await getSettings();
  return [{
    id: last.id, platform: info.platform, url: info.url, title: fallbackTitle(info),
    downloadDir: pathText(last.downloadDir) ? last.downloadDir : '', state: 'queued', percent: 0,
    media: 'video', createdAt: Date.now(), updatedAt: Date.now(), notified: true,
    request: { ...requestOptions(info, {
      ...settings, ...LEGACY_REQUEST, audioQuality: settings.audioQuality, media: 'video', quality: info.platform === 'bili' ? settings.qualityBili : settings.quality
    }), url: info.url, mode: 'direct', title: '' }
  }];
}

// Per-download fields default to off for legacy history so a retry never inherits newer global preferences.
const LEGACY_REQUEST = Object.freeze({
  audioQuality: 'auto', sponsorblock: 'off', danmaku: 'off', biliSubtitles: 'off',
  section: '', items: '', playlist: false, live: 'off', liveMinutes: '240',
  embedThumbnail: false, splitChapters: false, downloadArchive: false, limitRate: 'off', concurrentFragments: '1'
});

function playlistId(info) {
  if (info.platform !== 'youtube' || !['video', 'playlist'].includes(info.kind)) return '';
  return new URL(info.url).searchParams.get('list') || '';
}

function requestOptions(info, raw) {
  if (raw.live !== undefined && raw.live !== 'off') throw new Error('直播录制已停用，请重新提交普通视频下载；旧任务仍可查看或取消。');
  if (raw.danmaku === 'xml') throw new Error('XML 原始弹幕已停用，请重新提交并选择 ASS 弹幕或不下载弹幕。');
  const settings = safeSettings(raw);
  const requestedQuality = info.platform === 'bili' ? (raw.quality ?? settings.qualityBili) : (raw.quality ?? settings.quality);
  const qualities = info.platform === 'bili' ? ['best', '120', '112', '80', '64', '32', '16'] : ['best', '1080', '720', '480'];
  if (!qualities.includes(requestedQuality)) throw new Error('不支持的画质选项');
  if (raw.media !== undefined && !['video', 'audio-original', 'audio-mp3', 'audio-m4a'].includes(raw.media)) throw new Error('不支持的下载格式');
  if (raw.audioQuality !== undefined && !['auto', '320', '256', '192', '128'].includes(raw.audioQuality)) throw new Error('不支持的音质选项');
  if (raw.part !== undefined && !['current', 'all', 'collection'].includes(raw.part)) throw new Error('不支持的分 P 范围');
  for (const [key, values, label] of [['sponsorblock', SPONSORBLOCK, '跳过片段'], ['danmaku', DANMAKU, '弹幕'], ['biliSubtitles', BILI_SUBTITLES, '字幕'], ['live', LIVE, '直播录制'], ['liveMinutes', LIVE_MINUTES, '录制时长']]) {
    if (raw[key] !== undefined && !values.includes(raw[key])) throw new Error(`不支持的${label}选项`);
  }
  if (raw.playlist !== undefined && typeof raw.playlist !== 'boolean') throw new Error('不支持的列表选项');
  const requestedSection = raw.section === undefined ? '' : normalizeSection(raw.section);
  if (requestedSection === null) throw new Error('片段格式无效，请填写如 1:30-2:45 的起止时间');
  if (raw.items !== undefined && (typeof raw.items !== 'string' || raw.items.length > 120 || (raw.items && !ITEMS_SPEC.test(raw.items)))) {
    throw new Error('列表范围格式无效，请填写如 1-10,15');
  }
  const media = settings.media;
  for (const key of ['embedThumbnail', 'splitChapters', 'downloadArchive']) {
    if (raw[key] !== undefined && typeof raw[key] !== 'boolean') throw new Error('不支持的下载选项：' + key);
  }
  for (const [key, values] of [['limitRate', ['off', '512K', '1M', '5M', '10M']], ['concurrentFragments', ['1', '4', '8', '16']]]) {
    if (raw[key] !== undefined && !values.includes(raw[key])) throw new Error('不支持的下载选项：' + key);
  }
  const youtube = info.platform === 'youtube';
  let playlist = false;
  if (raw.playlist === true && youtube) {
    if (info.kind === 'channel') throw new Error('频道请使用“送入 FluentYTDL”，不会自动批量下载');
    if (!playlistId(info)) throw new Error('该链接不是播放列表');
    playlist = true;
  }
  const live = 'off';
  const section = (youtube && (info.kind === 'video' || playlist)) || info.platform === 'x' ? requestedSection : '';
  if (playlist && section) throw new Error('列表下载不支持片段，请清空片段');
  if (section && media === 'audio-original') throw new Error('原音轨不重新编码，无法截取片段；请改选 MP3 / M4A 或清空片段。');
  if (info.platform !== 'bili' && settings.embedThumbnail && media === 'audio-original') throw new Error('嵌入封面请改选视频、MP3 或 M4A。');
  if (info.platform !== 'bili' && settings.splitChapters && (section || media === 'audio-original')) throw new Error('按章节拆分不能与原音轨或时间片段共用。');
  return {
    quality: media === 'video' ? requestedQuality : 'best', part: info.platform === 'bili' ? settings.part : 'current',
    dir: settings.dir === 'default' ? '' : settings.dir, media, audioQuality: media === 'video' || media === 'audio-original' ? 'auto' : settings.audioQuality,
    subtitles: youtube && media === 'video' ? settings.subtitles : 'off',
    subLangs: settings.subLangs, metadata: info.platform !== 'bili' && settings.metadata,
    // SponsorBlock needs re-cutting: never for original audio, YouTube only.
    sponsorblock: youtube && media !== 'audio-original' ? settings.sponsorblock : 'off',
    section, playlist, items: playlist ? (raw.items || '') : '', live, liveMinutes: settings.liveMinutes,
    danmaku: info.platform === 'bili' ? settings.danmaku : 'off',
    biliSubtitles: info.platform === 'bili' ? settings.biliSubtitles : 'off',
    embedThumbnail: info.platform !== 'bili' && settings.embedThumbnail,
    splitChapters: info.platform !== 'bili' && settings.splitChapters,
    downloadArchive: info.platform !== 'bili' && settings.downloadArchive,
    limitRate: info.platform !== 'bili' ? settings.limitRate : 'off',
    concurrentFragments: info.platform !== 'bili' ? settings.concurrentFragments : '1'
  };
}

function nativeDownload(info, url, options) {
  const message = {
    action: info.platform === 'bili' ? 'bili-download' : 'yt-download', url,
    quality: options.quality, part: options.part, dir: options.dir, media: options.media, audioQuality: options.audioQuality,
    subtitles: options.subtitles, subLangs: options.subLangs, metadata: options.metadata
  };
  if (info.platform === 'bili') return {
    ...message,
    ...(options.danmaku !== 'off' ? { danmaku: options.danmaku } : {}),
    ...(options.biliSubtitles !== 'off' ? { biliSubtitles: options.biliSubtitles } : {})
  };
  return {
    ...message,
    ...(options.embedThumbnail ? { embedThumbnail: true } : {}),
    ...(options.splitChapters ? { splitChapters: true } : {}),
    ...(options.downloadArchive ? { downloadArchive: true } : {}),
    ...(options.limitRate !== 'off' ? { limitRate: options.limitRate } : {}),
    ...(options.concurrentFragments !== '1' ? { concurrentFragments: options.concurrentFragments } : {}),
    ...(options.sponsorblock !== 'off' ? { sponsorblock: options.sponsorblock } : {}),
    ...(options.section ? { section: options.section } : {}),
    ...(options.playlist ? { playlist: true, items: options.items } : {})
  };
}

function requiredFeatures(info, options) {
  if (info.platform === 'bili') {
    return [options.danmaku !== 'off' && 'danmaku', options.biliSubtitles !== 'off' && 'subtitles', options.part === 'collection' && 'collection'].filter(Boolean);
  }
  return [options.sponsorblock !== 'off' && 'sponsorblock', options.section && 'sections', options.playlist && 'playlist',
    options.embedThumbnail && 'thumbnail', options.splitChapters && 'chapters', options.downloadArchive && 'archive',
    (options.limitRate !== 'off' || options.concurrentFragments !== '1') && 'transfer'].filter(Boolean);
}

function hostFeatures(capability) {
  return Array.isArray(capability?.features) ? capability.features.filter(f => typeof f === 'string' && /^[a-z][a-z-]{0,39}$/.test(f) && !['live', 'login-status'].includes(f)) : [];
}

function requireFeatures(capability, features) {
  const available = hostFeatures(capability);
  const missing = features.filter(f => !available.includes(f));
  if (missing.length) throw new Error(`请更新本机组件后使用“${missing.map(f => FEATURE_LABELS[f] || f).join('、')}”`);
}

function safePing(result, host) {
  return {
    ok: true,
    ...(typeof result.version === 'string' && result.version.length <= 80 && /^[A-Za-z0-9][A-Za-z0-9 ._+-]*$/.test(result.version) ? { version: result.version } : {}),
    ...(typeof result.engineAvailable === 'boolean' ? { engineAvailable: result.engineAvailable } : {}),
    features: hostFeatures(result), login: {}
  };
}

async function deliver(raw, mode = 'direct', supplied = {}) {
  const info = SG.classify(raw);
  if (!info) throw new Error('请使用 YouTube 视频、X 单条推文或 B 站播放页链接');
  if (!['direct', 'handoff'].includes(mode)) throw new Error('不支持的下载方式');
  if (mode === 'handoff') {
    if (info.platform === 'bili') {
      const response = await native({ action: 'bili-handoff', url: info.url }, BILI_NATIVE_HOST);
      await chrome.storage.local.set({ [CLIPBOARD_KEY]: { url: info.url, at: Date.now() } });
      return { ok: true, platform: info.platform, url: info.url, launched: response.launched === true };
    }
    if (info.platform !== 'youtube') throw new Error('X 链接请使用直接下载');
    await sendToClipboard(info.url);
    await chrome.storage.local.set({ [CLIPBOARD_KEY]: { url: info.url, at: Date.now() } });
    return { ok: true, platform: info.platform, kind: info.kind, url: info.url };
  }
  if (info.platform === 'youtube' && info.kind === 'channel') throw new Error('频道请使用“送入 FluentYTDL”，不会自动批量下载');
  if (info.platform === 'youtube' && info.kind === 'playlist' && supplied.playlist !== true) throw new Error('播放列表请选择“下载列表”或“送入 FluentYTDL”');
  const current = await getSettings();
  const options = requestOptions(info, {
    ...current, ...supplied, quality: supplied.quality ?? (info.platform === 'bili' ? current.qualityBili : current.quality)
  });
  const target = options.playlist ? 'https://www.youtube.com/playlist?list=' + playlistId(info) : info.url;
  const features = requiredFeatures(info, options);
  const needsAudioCheck = options.media === 'audio-original' || (options.media !== 'video' && options.audioQuality !== 'auto');
  if (needsAudioCheck || features.length) {
    // Old hosts would ignore or misread new fields, so refuse before any job or history entry exists.
    const capability = await native({ action: 'ping' }, hostFor(info.platform));
    if (options.media === 'audio-original') {
      if (!Array.isArray(capability.mediaOptions) || !capability.mediaOptions.includes('audio-original')) {
        throw new Error('请更新对应的 Veylo 本机下载组件后再使用“原音轨”');
      }
    } else if (options.media !== 'video' && options.audioQuality !== 'auto') {
      if (!Array.isArray(capability.audioQualityOptions) || !capability.audioQualityOptions.includes(options.audioQuality)) {
        throw new Error('请更新对应的 Veylo 本机下载组件后再选择音质，或改用“内核默认”');
      }
    }
    requireFeatures(capability, features);
  }
  const result = await native(nativeDownload(info, target, options), hostFor(info.platform));
  if (!validId(result.jobId) || !pathText(result.downloadDir) || !result.downloadDir.trim()) throw new Error('本机组件返回的任务信息无效');
  const now = Date.now();
  await exclusive(async () => {
    const jobs = await getHistory();
    const key = `${info.platform}:${result.jobId}`;
    const existing = jobs.find(j => jobKey(j) === key);
    if (!existing) jobs.unshift({
      id: result.jobId, platform: info.platform, url: target,
      title: titleText(supplied.title) || fallbackTitle(SG.classify(target) || info), media: options.media,
      downloadDir: result.downloadDir, state: 'queued', percent: 0, error: '',
      createdAt: now, updatedAt: now, notified: false, files: [],
      request: { url: target, mode: 'direct', ...options, title: titleText(supplied.title) }
    });
    await chrome.storage.local.set({
      [HISTORY_KEY]: trimHistory(jobs),
      [JOB_KEY]: { id: result.jobId, url: target, downloadDir: result.downloadDir, platform: info.platform }
    });
  });
  const jobs = await getHistory();
  await updateIndicators(jobs);
  await ensureAlarm(jobs);
  return { ...result, platform: info.platform, url: target };
}

async function pickFolder(host, initialDir) {
  const nativeHost = host === 'bili' ? BILI_NATIVE_HOST : NATIVE_HOST;
  const message = { action: 'pick-folder' };
  // Only the currently saved directory may seed the dialog, and only on hosts that understand the key.
  if (pathText(initialDir) && initialDir && initialDir !== 'default' && initialDir === (await getSettings()).dir) {
    const capability = await native({ action: 'ping' }, nativeHost).catch(() => null);
    if (hostFeatures(capability).includes('pick-folder-initial')) message.initialDir = initialDir;
  }
  const result = await native(message, nativeHost);
  if (result.canceled === true || !pathText(result.path) || !result.path.trim()) return { ok: true, canceled: true };
  return exclusive(async () => {
    const current = await getSettings();
    const settings = { ...current, dir: result.path, recentDirs: [result.path, ...current.recentDirs.filter(p => p !== result.path)].slice(0, 5) };
    await chrome.storage.local.set({ [SETTINGS_KEY]: settings });
    return { ok: true, path: result.path, recentDirs: settings.recentDirs, settings };
  });
}

function safeJobUpdate(job) {
  if (!job || !['queued', 'downloading', 'completed', 'failed', 'canceled'].includes(job.state)) throw new Error('任务状态无效');
  const result = { state: job.state, updatedAt: Date.now(), connectionError: '' };
  if (Number.isFinite(job.percent)) result.percent = Math.max(0, Math.min(100, job.percent));
  if (typeof job.error === 'string') result.error = job.error.slice(0, 600);
  if (pathText(job.downloadDir) && job.downloadDir) result.downloadDir = job.downloadDir;
  if (titleText(job.title)) result.title = titleText(job.title);
  if (Array.isArray(job.files)) result.files = job.files.filter(p => pathText(p) && p).slice(0, 20);
  if (Number.isInteger(job.fileCount) && job.fileCount >= 0) result.fileCount = Math.min(job.fileCount, 100000);
  if (Array.isArray(job.warnings)) result.warnings = job.warnings.filter(w => JOB_WARNINGS.has(w)).slice(0, 5);
  return result;
}

async function refreshHistory() {
  if (historyRefresh) return historyRefresh;
  historyRefresh = (async () => {
    const previous = await getHistory();
    const active = previous.filter(j => ACTIVE_STATES.has(j.state));
    const updates = await Promise.all(active.map(async j => {
      try {
        const response = await native({ action: 'job-status', jobId: j.id }, hostFor(j.platform));
        return { key: jobKey(j), patch: safeJobUpdate(response.job) };
      } catch (e) {
        // A disconnected browser host does not mean a detached download stopped.
        return { key: jobKey(j), patch: { connectionError: titleText(e.message) } };
      }
    }));
    const notifications = [];
    const jobs = await exclusive(async () => {
      const current = await getHistory();
      const patches = new Map(updates.map(u => [u.key, u.patch]));
      const merged = current.map(j => {
        const patch = patches.get(jobKey(j));
        const updated = patch ? { ...j, ...patch } : j;
        if (patch && END_STATES.has(updated.state) && !updated.notified) {
          updated.notified = true;
          // A user-requested cancel is not news worth an OS notification.
          if (updated.state !== 'canceled') notifications.push(updated);
        }
        return updated;
      });
      const trimmed = trimHistory(merged);
      if (updates.length || !previous.length) await chrome.storage.local.set({ [HISTORY_KEY]: trimmed });
      return trimmed;
    });
    await Promise.all(notifications.map(notifyFinished));
    await updateIndicators(jobs);
    await ensureAlarm(jobs);
    return { ok: true, jobs };
  })().finally(() => { historyRefresh = null; });
  return historyRefresh;
}

async function notificationPermission() {
  try { return !!chrome.permissions && await chrome.permissions.contains({ permissions: ['notifications'] }); }
  catch { return false; }
}

async function notifyFinished(job) {
  const settings = await getSettings();
  if (!settings.notify || !(await notificationPermission()) || !chrome.notifications) return;
  try {
    await chrome.notifications.create(`veylo:${job.platform}:${job.id}`, {
      type: 'basic', iconUrl: chrome.runtime.getURL('icons/icon128.png'),
      title: job.state === 'completed' ? 'Veylo · 下载完成' : 'Veylo · 下载未完成',
      message: job.state === 'completed' ? job.title : (job.error || job.title),
      buttons: [{ title: '打开保存位置' }], isClickable: true
    });
  } catch { /* OS notification settings do not affect downloads. */ }
}

async function updateIndicators(jobs) {
  const count = jobs.filter(j => ACTIVE_STATES.has(j.state)).length;
  await chrome.action.setBadgeBackgroundColor({ color: '#15282c' });
  await chrome.action.setBadgeText({ text: count ? (count > 99 ? '99+' : String(count)) : '' });
  await chrome.action.setTitle({ title: count ? `Veylo · ${count} 个下载进行中` : 'Veylo · 留下喜欢的内容' });
}

async function ensureAlarm(jobs) {
  if (jobs.some(j => ACTIVE_STATES.has(j.state))) await chrome.alarms.create(POLL_ALARM, { periodInMinutes: 1 });
  else await chrome.alarms.clear(POLL_ALARM);
}

async function findJob(id, platform) {
  jobIdentity(id, platform);
  const job = (await getHistory()).find(j => j.id === id && j.platform === platform);
  if (!job) throw new Error('找不到此任务记录');
  return job;
}

async function retryJob(id, platform) {
  const old = await findJob(id, platform);
  if (!['failed', 'canceled'].includes(old.state)) throw new Error('只有失败或已取消的任务可以重新下载');
  if (!old.request) throw new Error('这条旧记录没有下载选项，请重新提交链接');
  const request = { ...LEGACY_REQUEST, ...old.request };
  return deliver(old.url, 'direct', request);
}

async function cancelJob(id, platform) {
  const job = await findJob(id, platform);
  if (!ACTIVE_STATES.has(job.state)) throw new Error('只有进行中的任务可以取消');
  const host = hostFor(platform);
  requireFeatures(await native({ action: 'ping' }, host), ['cancel']);
  await native({ action: 'cancel-job', jobId: id }, host);
  await exclusive(async () => {
    const jobs = await getHistory();
    const target = jobs.find(j => j.id === id && j.platform === platform);
    if (target) target.cancelRequested = true;
    await chrome.storage.local.set({ [HISTORY_KEY]: trimHistory(jobs) });
  });
  if (historyRefresh) await historyRefresh.catch(() => {});
  return refreshHistory();
}

async function openJobFolder(id, platform) {
  await findJob(id, platform);
  return native({ action: 'open-job-folder', jobId: id }, hostFor(platform));
}

async function openJobFile(id, platform, fileIndex, expectedPath) {
  const job = await findJob(id, platform);
  if (!Number.isInteger(fileIndex) || fileIndex < 0 || fileIndex >= (job.files || []).length) throw new Error('文件信息无效，请刷新任务记录');
  const filePath = job.files[fileIndex];
  if (expectedPath !== undefined && expectedPath !== filePath) throw new Error('文件记录已变化，请刷新任务记录');
  requireFeatures(await native({ action: 'ping' }, hostFor(platform)), ['open-file']);
  return native({ action: 'open-job-file', jobId: id, fileIndex, filePath }, hostFor(platform));
}

async function clearEnded() {
  const jobs = await exclusive(async () => {
    const kept = (await getHistory()).filter(j => ACTIVE_STATES.has(j.state));
    await chrome.storage.local.set({ [HISTORY_KEY]: kept, [JOB_KEY]: kept.length ? {
      id: kept[0].id, platform: kept[0].platform, url: kept[0].url, downloadDir: kept[0].downloadDir
    } : null });
    return kept;
  });
  await updateIndicators(jobs);
  await ensureAlarm(jobs);
  return { ok: true, jobs };
}

async function openPanel(url = '') {
  const clean = SG.cleanUrl(url);
  const target = chrome.runtime.getURL('popup.html') + (clean ? `?url=${encodeURIComponent(clean)}` : '');
  try { await chrome.windows.create({ url: target, type: 'popup', width: 440, height: 740 }); }
  catch { await chrome.tabs.create({ url: target }); }
}

function notificationJob(id) {
  const match = /^veylo:(youtube|x|bili):([a-f0-9]{32})$/.exec(id || '');
  return match ? { platform: match[1], id: match[2] } : null;
}

function registerMenu() {
  if (!chrome.contextMenus) return;
  chrome.contextMenus.removeAll(() => {
    void chrome.runtime.lastError;
    chrome.contextMenus.create({
      id: MENU_ID, title: '在 Veylo 中下载…', contexts: ['link', 'page', 'video'],
      targetUrlPatterns: ['*://*.youtube.com/*', '*://youtu.be/*', '*://*.youtu.be/*', '*://*.youtube-nocookie.com/*', '*://*.x.com/*', '*://*.twitter.com/*', '*://*.bilibili.com/*']
    }, () => { void chrome.runtime.lastError; });
  });
}

if (chrome.contextMenus?.onClicked) chrome.contextMenus.onClicked.addListener((info, tab) => {
  if (info.menuItemId !== MENU_ID) return;
  const candidates = [info.linkUrl, info.pageUrl, tab?.url, info.srcUrl];
  const url = candidates.find(u => SG.classify(u));
  openPanel(url || '').catch(() => {});
});
if (chrome.alarms?.onAlarm) chrome.alarms.onAlarm.addListener(alarm => {
  if (alarm.name === POLL_ALARM) refreshHistory().catch(() => {});
});
if (chrome.runtime?.onInstalled?.addListener) chrome.runtime.onInstalled.addListener(() => { registerMenu(); refreshHistory().catch(() => {}); });
if (chrome.runtime?.onStartup?.addListener) chrome.runtime.onStartup.addListener(() => { registerMenu(); refreshHistory().catch(() => {}); });
let notificationListenersAttached = false;
function attachNotificationListeners() {
  if (notificationListenersAttached || !chrome.notifications) return;
  const clicked = id => {
    const identity = notificationJob(id);
    if (identity) openJobFolder(identity.id, identity.platform).catch(() => {});
  };
  chrome.notifications.onClicked.addListener(clicked);
  chrome.notifications.onButtonClicked.addListener((id, index) => { if (index === 0) clicked(id); });
  notificationListenersAttached = true;
}
attachNotificationListeners();
chrome.permissions?.onAdded?.addListener(permissions => {
  if (permissions.permissions?.includes('notifications')) attachNotificationListeners();
});

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  // Privileged UI is extension-origin only. Never broker page content-script requests.
  if (sender.id !== chrome.runtime.id || typeof sender.url !== 'string'
      || sender.url.split(/[?#]/, 1)[0] !== chrome.runtime.getURL('popup.html')) return false;
  let task;
  if (msg?.type === 'sg-deliver') task = deliver(msg.url, msg.mode, msg);
  else if (msg?.type === 'sg-copy') {
    const url = SG.cleanUrl(msg.url);
    task = url ? sendToClipboard(url).then(() => ({ ok: true })) : Promise.reject(new Error('不支持的链接'));
  } else if (msg?.type === 'sg-status') {
    task = chrome.storage.local.get([CLIPBOARD_KEY, JOB_KEY]).then(d => ({ ok: true, last: d[CLIPBOARD_KEY] || null, job: d[JOB_KEY] || null }));
  } else if (msg?.type === 'sg-job-status') {
    task = (async () => { jobIdentity(msg.jobId, msg.platform || 'x'); return native({ action: 'job-status', jobId: msg.jobId }, hostFor(msg.platform)); })();
  } else if (msg?.type === 'sg-bridge-ping') task = native({ action: 'ping' }, hostFor(msg.host)).then(result => safePing(result, hostFor(msg.host)));
  else if (msg?.type === 'sg-pick-folder') task = pickFolder(msg.host, msg.initialDir);
  else if (msg?.type === 'sg-job-cancel') task = cancelJob(msg.jobId, msg.platform);
  else if (msg?.type === 'sg-settings-get') task = getSettings().then(settings => ({ ok: true, settings }));
  else if (msg?.type === 'sg-settings-set') task = saveSettings(msg.settings);
  else if (msg?.type === 'sg-history-get') task = getHistory().then(jobs => ({ ok: true, jobs }));
  else if (msg?.type === 'sg-history-refresh') task = refreshHistory();
  else if (msg?.type === 'sg-history-clear') task = clearEnded();
  else if (msg?.type === 'sg-job-retry') task = retryJob(msg.jobId, msg.platform);
  else if (msg?.type === 'sg-job-open-folder') task = openJobFolder(msg.jobId, msg.platform);
  else if (msg?.type === 'sg-job-open-file') task = openJobFile(msg.jobId, msg.platform, msg.fileIndex, msg.filePath);
  else return false;
  Promise.resolve(task).then(sendResponse).catch(e => sendResponse({ ok: false, error: e.message || String(e) }));
  return true;
});
