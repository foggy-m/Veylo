/* Veylo shared UI. No network requests, page data scraping, or credential reads. */
var SGUi = (() => {
  'use strict';

  const mounted = new WeakMap();
  const DEFAULTS = Object.freeze({
    dir: 'default', recentDirs: [], quality: 'best', qualityBili: 'best', part: 'current',
    media: 'video', audioQuality: 'auto', subtitles: 'off', subLangs: 'zh.*,en', metadata: false, notify: false, theme: 'auto',
    sponsorblock: 'off', danmaku: 'off', biliSubtitles: 'off',
    embedThumbnail: false, splitChapters: false, downloadArchive: false, limitRate: 'off', concurrentFragments: 'auto'
  });
  const AUDIO_QUALITY = ['auto', '320', '256', '192', '128'];
  const SPONSORBLOCK = ['off', 'sponsor', 'all'];
  const DANMAKU = ['off', 'ass'];
  const BILI_SUBTITLES = ['off', 'srt'];
  const ITEMS_SPEC = /^[0-9]{1,5}(-[0-9]{0,5})?(,[0-9]{1,5}(-[0-9]{0,5})?){0,19}$/;
  const UPDATE_HINT = '请更新本机组件后使用';

  // Same grammar as the background and native hosts: SS, MM:SS or HH:MM:SS; the end may be "inf".
  function sectionSeconds(token, end) {
    if (end && token === 'inf') return Infinity;
    const parts = token.split(':');
    if (parts.length === 1) return /^\d{1,5}$/.test(parts[0]) ? Number(parts[0]) : NaN;
    if (parts.length === 2 && /^\d{1,2}$/.test(parts[0]) && /^\d{1,2}$/.test(parts[1]) && Number(parts[1]) < 60) return Number(parts[0]) * 60 + Number(parts[1]);
    if (parts.length === 3 && parts.every(part => /^\d{1,2}$/.test(part)) && Number(parts[1]) < 60 && Number(parts[2]) < 60) return Number(parts[0]) * 3600 + Number(parts[1]) * 60 + Number(parts[2]);
    return NaN;
  }

  function normalizeSection(value) {
    const text = String(value || '').replace(/\s+/g, '');
    if (!text) return '';
    const match = text.length <= 32 ? /^([0-9:]+)-([0-9:]+|inf)$/.exec(text) : null;
    if (!match) return null;
    const start = sectionSeconds(match[1], false);
    const end = sectionSeconds(match[2], true);
    if (!Number.isFinite(start) || Number.isNaN(end) || start >= end || (end !== Infinity && end > 86400)) return null;
    return text;
  }

  function normalizeItems(value) {
    const text = String(value || '').replace(/\s+/g, '');
    if (!text) return '';
    return text.length <= 120 && ITEMS_SPEC.test(text) ? text : null;
  }
  const QUALITY = [['best', '最佳可用画质'], ['1080', '最高 1080p'], ['720', '最高 720p'], ['480', '最高 480p']];
  const BILI_QUALITY = [
    ['best', '最佳可用画质'], ['120', '最高 4K'], ['112', '最高 1080P+'],
    ['80', '最高 1080P'], ['64', '最高 720P'], ['32', '最高 480P'], ['16', '最高 360P']
  ];
  const BRAND = '<svg xmlns="http://www.w3.org/2000/svg" width="44" height="44" viewBox="0 0 64 64" fill="none" aria-hidden="true"><rect width="64" height="64" rx="18" fill="#15282C"/><path d="M15.5 20H25.6L35 44.5H25.2L15.5 20Z" fill="#F6F7F3"/><path d="M38.3 20H49L37.2 45H26.7L38.3 20Z" fill="#69DDC3"/><circle cx="49" cy="14" r="4" fill="#F28C7D"/></svg>';

  function icon(name, size = 18) {
    const svg = globalThis.SGIcons?.svg(name, size) || '';
    return `<span class="vy-icon" aria-hidden="true">${svg}</span>`;
  }

  function request(data) {
    return new Promise((resolve, reject) => {
      try {
        chrome.runtime.sendMessage(data, response => {
          const error = chrome.runtime.lastError;
          if (error) reject(new Error(error.message || '扩展连接已中断，请刷新页面后重试'));
          else if (response?.canceled) resolve(response);
          else if (!response?.ok) reject(new Error(response?.error || '本机操作未完成，请重试'));
          else resolve(response);
        });
      } catch (error) {
        reject(new Error(error?.message || '扩展已更新，请刷新页面后重试'));
      }
    });
  }

  function notificationPermission() {
    // Invoked directly in the checkbox change gesture, never during initialization.
    return new Promise(resolve => {
      if (!globalThis.chrome?.permissions?.request) { resolve(false); return; }
      try {
        chrome.permissions.request({ permissions: ['notifications'] }, granted => {
          const error = chrome.runtime.lastError;
          resolve(!error && granted === true);
        });
      } catch (_) { resolve(false); }
    });
  }

  function normalizedSettings(input = {}) {
    const result = {};
    for (const key of Object.keys(DEFAULTS)) {
      if (!Object.prototype.hasOwnProperty.call(input, key)) continue;
      const value = input[key];
      if (key === 'recentDirs') result[key] = Array.isArray(value) ? [...new Set(value.filter(path => typeof path === 'string' && path && path !== 'default'))].slice(0, 20) : [];
      else if (['metadata', 'notify', 'embedThumbnail', 'splitChapters', 'downloadArchive'].includes(key)) result[key] = value === true;
      else if (key === 'limitRate') result[key] = ['off', '512K', '1M', '5M', '10M'].includes(value) ? value : 'off';
      else if (key === 'concurrentFragments') result[key] = ['auto', '1', '4', '8', '16'].includes(value) ? value : 'auto';
      else if (key === 'media') result[key] = ['video', 'audio-original', 'audio-mp3', 'audio-m4a'].includes(value) ? value : 'video';
      else if (key === 'audioQuality') result[key] = AUDIO_QUALITY.includes(value) ? value : 'auto';
      else if (key === 'subtitles') result[key] = ['off', 'manual', 'auto'].includes(value) ? value : 'off';
      else if (key === 'subLangs') result[key] = ['zh.*,en', 'zh.*', 'en'].includes(value) ? value : 'zh.*,en';
      else if (key === 'theme') result[key] = ['auto', 'light', 'dark'].includes(value) ? value : 'auto';
      else if (key === 'part') result[key] = ['all', 'collection'].includes(value) ? value : 'current';
      else if (key === 'sponsorblock') result[key] = SPONSORBLOCK.includes(value) ? value : 'off';
      else if (key === 'danmaku') result[key] = DANMAKU.includes(value) ? value : 'off';
      else if (key === 'biliSubtitles') result[key] = BILI_SUBTITLES.includes(value) ? value : 'off';
      else if (typeof value === 'string' && value) result[key] = value;
    }
    return result;
  }

  const jobKey = job => `${job.platform || ''}:${job.id}`;
  const isActiveJob = job => ['queued', 'downloading'].includes(job.state);
  const isFinishedJob = job => ['completed', 'failed', 'canceled'].includes(job.state);
  const capsHost = platform => platform === 'bili' ? 'bili' : 'x';
  const labelFor = platform => platform === 'bili' ? 'bilibili' : (SG.platformMeta(platform).label || '视频');
  const errorText = error => error?.message || String(error || '操作未完成');

  function mount(container, options = {}) {
    if (!container) throw new TypeError('Veylo requires a DOM container');
    mounted.get(container)?.destroy();
    const doc = container.ownerDocument || globalThis.document;
    if (!doc?.createElement) throw new TypeError('Veylo requires a DOM container');
    const app = doc.createElement('section');
    app.className = 'vy-app';
    app.dataset.variant = options.variant === 'floating' ? 'floating' : 'popup';
    app.lang = 'zh-CN';
    app.setAttribute('aria-label', 'Veylo 视频助手');
    app.innerHTML = `
      <header class="vy-header">
        <span class="vy-brand-mark">${BRAND}</span>
        <div class="vy-wordmark"><h1>Veylo</h1><p lang="en">Video, your way.</p></div>
        <div class="vy-header-actions">
          <button id="vy-bridge-check" class="vy-icon-button vy-bridge" type="button" title="检查本机连接" aria-label="检查本机连接">${icon('link')}</button>
          <button id="vy-close" class="vy-icon-button" type="button" title="收起面板" aria-label="收起 Veylo 面板" ${options.onClose ? '' : 'hidden'}>${icon('x')}</button>
        </div>
      </header>
      <div class="vy-tabs" role="tablist" aria-label="工作区">
        <button id="vy-tab-download" class="vy-tab" type="button" role="tab" aria-selected="true" aria-controls="vy-download-panel" tabindex="0">${icon('download', 16)}<span>下载</span></button>
        <button id="vy-tab-history" class="vy-tab" type="button" role="tab" aria-selected="false" aria-controls="vy-history-panel" tabindex="-1">${icon('history', 16)}<span>任务记录</span><span id="vy-active-count" class="vy-count" hidden></span></button>
      </div>
      <div id="vy-workspace" class="vy-workspace">
        <section id="vy-download-panel" class="vy-download-panel" role="tabpanel" aria-labelledby="vy-tab-download" tabindex="0">
          <div class="vy-field-heading"><label for="vy-url">视频链接</label><span lang="en">YouTube · X · bilibili</span></div>
          <div class="vy-url-field">${icon('link', 16)}<input id="vy-url" type="text" inputmode="url" maxlength="4096" autocomplete="off" spellcheck="false" placeholder="粘贴链接，或打开视频页" aria-describedby="vy-detected"></div>
          <div id="vy-detected" class="vy-detected"><span id="vy-platform" class="vy-platform">等待链接</span><span id="vy-source-title" class="vy-source-title">好内容，随心留存</span><span id="vy-kind" class="vy-kind"></span></div>
          <p id="vy-handoff-note" class="vy-note vy-handoff-note" hidden>播放列表与频道请交给 FluentYTDL 解析，格式与保存位置在软件中确认。</p>
          <p id="vy-update-note" class="vy-option-note vy-update-note" hidden></p>
          <div id="vy-download-options">
            <div class="vy-folder-field">
              <label class="vy-field-label" for="vy-dir">保存位置</label>
              <div class="vy-folder-controls"><div class="vy-select-wrap vy-dir-wrap">${icon('folder-open', 16)}<select id="vy-dir" aria-label="保存位置"><option value="default">默认目录（软件设置）</option></select>${icon('chevron-down', 14)}</div><button id="vy-pick-folder" class="vy-text-button vy-folder-button" type="button" aria-label="选择文件夹（系统对话框）" title="选择文件夹（系统对话框）">${icon('folder-open', 14)}<span>选择</span></button></div>
            </div>
            <div id="vy-video-options" class="vy-options-grid">
              <div id="vy-quality-row" class="vy-field"><label id="vy-quality-label" class="vy-field-label" for="vy-quality">画质上限</label><div class="vy-select-wrap"><select id="vy-quality"></select>${icon('chevron-down', 14)}</div></div>
              <div id="vy-part-row" class="vy-field" hidden><label id="vy-part-label" class="vy-field-label" for="vy-part">分 P 范围</label><div class="vy-select-wrap"><select id="vy-part" aria-describedby="vy-update-note"><option value="current">当前分 P</option><option value="all">全部分 P</option></select>${icon('chevron-down', 14)}</div></div>
            </div>
            <div id="vy-extra-options" class="vy-extra-options">
              <div id="vy-section-row" class="vy-field" hidden><label class="vy-field-label" for="vy-section">片段（可选）</label><div class="vy-text-wrap"><input id="vy-section" type="text" maxlength="32" autocomplete="off" spellcheck="false" placeholder="如 1:30-2:45，留空为完整视频" aria-invalid="false" aria-describedby="vy-section-error vy-section-note vy-update-note"></div></div>
              <p id="vy-section-error" class="vy-option-note vy-field-error" hidden>请填写“开始-结束”，如 1:30-2:45、90-120 或 1:00-inf。</p>
              <p id="vy-section-note" class="vy-option-note" hidden></p>
              <div id="vy-items-row" class="vy-field" hidden><label class="vy-field-label" for="vy-items">范围（可选）</label><div class="vy-text-wrap"><input id="vy-items" type="text" maxlength="120" autocomplete="off" spellcheck="false" placeholder="如 1-10,15，留空为全部（最多 200 项）" aria-invalid="false" aria-describedby="vy-items-error"></div></div>
              <p id="vy-items-error" class="vy-option-note vy-field-error" hidden>请填写条目序号，如 1-10,15。</p>
            </div>
          </div>
          <details id="vy-more" class="vy-more">
            <summary>${icon('settings-2', 15)}<span>更多选项</span><span id="vy-more-value" class="vy-more-value">视频 · 默认设置</span>${icon('chevron-down', 14)}</summary>
            <div class="vy-more-body">
              <div id="vy-media-row" class="vy-option-row"><label for="vy-media">${icon('music-2', 15)}输出格式</label><div class="vy-select-wrap"><select id="vy-media" aria-describedby="vy-audio-original-note"><option value="video">视频 + 音频</option><option value="audio-original">仅音频 · 原音轨（推荐）</option><option value="audio-mp3">仅音频 · 转换为 MP3</option><option value="audio-m4a">仅音频 · 转换为 M4A</option></select>${icon('chevron-down', 14)}</div></div>
              <p id="vy-audio-original-note" class="vy-option-note" hidden>选择最佳可用原音轨，保留源编码、不重新编码、不强制码率，输出格式随源；不保证源本身无损，也不保证文件大小。</p>
              <div id="vy-audio-quality-row" class="vy-option-row" hidden><label for="vy-audio-quality">音频音质</label><div class="vy-select-wrap"><select id="vy-audio-quality" aria-describedby="vy-audio-quality-note"><option value="auto">内核默认</option><option value="320">320 kbps</option><option value="256">256 kbps</option><option value="192">192 kbps</option><option value="128">128 kbps</option></select>${icon('chevron-down', 14)}</div></div>
              <p id="vy-audio-quality-note" class="vy-option-note" hidden>MP3 / M4A 为转换格式，不保证保留源编码；内核默认沿用原转换策略，固定值为目标编码码率。提高码率可能增大文件，但不能提升源音质，也不代表无损；不确定时选原音轨。</p>
              <div id="vy-subtitles-row" class="vy-option-row" hidden><label for="vy-subtitles">${icon('subtitles', 15)}字幕</label><div class="vy-select-wrap"><select id="vy-subtitles"><option value="off">不下载字幕</option><option value="manual">人工字幕</option><option value="auto">人工 + 自动字幕</option></select>${icon('chevron-down', 14)}</div></div>
              <div id="vy-langs-row" class="vy-option-row" hidden><label for="vy-sub-langs">字幕语言</label><div class="vy-select-wrap"><select id="vy-sub-langs"><option value="zh.*,en">中文 + English</option><option value="zh.*">中文</option><option value="en">English</option></select>${icon('chevron-down', 14)}</div></div>
              <p id="vy-subtitles-note" class="vy-option-note" hidden>仅 YouTube 视频支持；以原视频提供的字幕为准。</p>
              <div id="vy-sponsorblock-row" class="vy-option-row" hidden><label for="vy-sponsorblock">跳过片段</label><div class="vy-select-wrap"><select id="vy-sponsorblock" aria-describedby="vy-sponsorblock-note vy-update-note"><option value="off">不跳过</option><option value="sponsor">移除赞助片段</option><option value="all">移除赞助与非正片片段</option></select>${icon('chevron-down', 14)}</div></div>
              <p id="vy-sponsorblock-note" class="vy-option-note" hidden></p>
              <p id="vy-sponsorblock-help" class="vy-option-note" hidden>SponsorBlock 使用社区标记移除赞助、片头片尾等片段；没有标记时保留原内容。</p>
              <div id="vy-practical-options" hidden>
                <label class="vy-toggle-row"><span><span class="vy-option-title">嵌入封面</span><span class="vy-option-description">把平台封面写入视频或 MP3 / M4A 文件。</span></span><input id="vy-embed-thumbnail" type="checkbox" role="switch"></label>
                <label class="vy-toggle-row"><span><span class="vy-option-title">按章节拆分</span><span class="vy-option-description">按源视频章节另存多个文件，并保留完整文件；没有章节时保留完整文件。</span></span><input id="vy-split-chapters" type="checkbox" role="switch"></label>
                <label class="vy-toggle-row"><span><span class="vy-option-title">防止重复下载</span><span class="vy-option-description">按视频 ID、保存目录和输出选项记忆成功下载；清除任务记录后仍生效。</span></span><input id="vy-download-archive" type="checkbox" role="switch"></label>
                <div class="vy-option-row"><label for="vy-limit-rate">下载限速</label><div class="vy-select-wrap"><select id="vy-limit-rate"><option value="off">不限速</option><option value="512K">512 KiB/s</option><option value="1M">1 MiB/s</option><option value="5M">5 MiB/s</option><option value="10M">10 MiB/s</option></select>${icon('chevron-down', 14)}</div></div>
                <div class="vy-option-row"><label for="vy-concurrent-fragments">并发分片</label><div class="vy-select-wrap"><select id="vy-concurrent-fragments"><option value="auto">自动（跟随本机）</option><option value="1">1（单连接）</option><option value="4">4</option><option value="8">8</option><option value="16">16</option></select>${icon('chevron-down', 14)}</div></div>
                <p id="vy-practical-note" class="vy-option-note">这些选项用于 YouTube / X。分片并发仅对支持的 DASH / HLS 源生效；限速按下载连接计算。</p>
              </div>
              <div id="vy-danmaku-row" class="vy-option-row" hidden><label for="vy-danmaku">弹幕</label><div class="vy-select-wrap"><select id="vy-danmaku" aria-describedby="vy-bili-extras-note vy-update-note"><option value="off">不下载弹幕</option><option value="ass">ASS 弹幕</option></select>${icon('chevron-down', 14)}</div></div>
              <div id="vy-bili-subtitles-row" class="vy-option-row" hidden><label for="vy-bili-subtitles">${icon('subtitles', 15)}字幕</label><div class="vy-select-wrap"><select id="vy-bili-subtitles" aria-describedby="vy-bili-extras-note vy-update-note"><option value="off">不下载字幕</option><option value="srt">SRT 字幕</option></select>${icon('chevron-down', 14)}</div></div>
              <p id="vy-bili-extras-note" class="vy-option-note" hidden>弹幕与字幕保存在视频旁；获取失败不影响视频。</p>
              <label id="vy-metadata-row" class="vy-toggle-row" hidden><span><span class="vy-option-title">保存媒体信息</span><span class="vy-option-description">YouTube / X 的标题与来源信息，写入能力以本机内核为准。</span></span><input id="vy-metadata" type="checkbox" role="switch"></label>
              <label class="vy-toggle-row"><span><span class="vy-option-title">完成时通知我</span><span class="vy-option-description">可选的系统通知；开启时申请权限，不连接新的远端服务。</span></span><input id="vy-notify" type="checkbox" role="switch"></label>
              <div class="vy-option-row"><label for="vy-theme">外观</label><div class="vy-select-wrap"><select id="vy-theme"><option value="auto">跟随系统</option><option value="light">浅色</option><option value="dark">深色</option></select>${icon('chevron-down', 14)}</div></div>
              <p class="vy-option-note">画质是上限。直接下载使用本机 FluentYTDL / Bili23 内核；“送入软件”通过剪贴板交给软件解析，需开启软件的剪贴板识别。</p>
              <p class="vy-option-note">私人使用的非官方助手；凭据由本机桥接处理，界面不读取或展示凭据。</p>
            </div>
          </details>
          <div class="vy-actions"><button id="vy-send" class="vy-primary" type="button" disabled><span id="vy-send-icon">${icon('download', 18)}</span><span id="vy-send-text">下载视频</span><span class="vy-primary-arrow">${icon('arrow-up-right', 16)}</span></button></div>
          <div class="vy-secondary-actions"><button id="vy-handoff" class="vy-text-button" type="button" hidden>${icon('external-link', 14)}<span id="vy-handoff-text">送入 FluentYTDL</span></button><button id="vy-copy" class="vy-text-button" type="button" disabled>${icon('copy', 14)}<span>复制链接</span></button></div>
        </section>
        <section id="vy-history-panel" class="vy-history-panel" role="tabpanel" aria-labelledby="vy-tab-history" tabindex="0" hidden>
          <div class="vy-history-heading"><span id="vy-history-summary">本机任务记录</span><div class="vy-history-tools"><button id="vy-history-clear" class="vy-text-button" type="button" hidden>清除已结束</button><button id="vy-history-refresh" class="vy-icon-button" type="button" title="刷新任务记录" aria-label="刷新任务记录">${icon('refresh-cw', 16)}</button></div></div>
          <div class="vy-history-filters">
            <input id="vy-history-search" type="search" maxlength="220" placeholder="搜索标题或链接" aria-label="搜索任务标题或链接">
            <select id="vy-history-platform" aria-label="按平台筛选"><option value="all">全部平台</option><option value="youtube">YouTube</option><option value="x">X</option><option value="bili">B 站</option></select>
            <select id="vy-history-state" aria-label="按状态筛选"><option value="all">全部状态</option><option value="active">进行中</option><option value="completed">已完成</option><option value="failed">失败</option><option value="canceled">已取消</option></select>
          </div>
          <div id="vy-clear-confirm" class="vy-inline-confirm" role="group" aria-label="确认清除已结束的任务记录" hidden><p>仅清除已结束记录，不删除文件。进行中的任务会保留。</p><div><button id="vy-clear-cancel" class="vy-small-button" type="button">取消</button><button id="vy-clear-accept" class="vy-small-button vy-danger-button" type="button">清除记录</button></div></div>
          <p id="vy-history-error" class="vy-history-error" role="status" hidden></p>
          <div id="vy-jobs" class="vy-jobs" aria-label="下载任务"></div>
          <div id="vy-history-empty" class="vy-empty"><span class="vy-empty-symbol">${icon('history', 26)}</span><h2 id="vy-empty-title">还没有任务</h2><p id="vy-empty-description">喜欢的内容，从一个链接开始。<br>任务记录只保存在本机。</p><button id="vy-empty-download" class="vy-small-button" type="button">添加一个下载${icon('arrow-up-right', 14)}</button></div>
        </section>
      </div>
      <div id="vy-status" class="vy-status" role="status" aria-live="polite" aria-atomic="true" hidden><span id="vy-status-icon">${icon('info', 16)}</span><span id="vy-status-text"></span></div>
      <footer class="vy-footer"><span>好内容，随心留存</span><span class="vy-shortcut" title="Alt + Shift + V 打开 Veylo 面板">${icon('keyboard', 13)}<span>Alt + Shift + V</span></span></footer>`;
    container.replaceChildren(app);
    const $ = id => app.querySelector(`#vy-${id}`);
    const state = {
      settings: { ...DEFAULTS, recentDirs: [] }, revisions: Object.fromEntries(Object.keys(DEFAULTS).map(key => [key, 0])),
      pendingSettings: Object.fromEntries(Object.keys(DEFAULTS).map(key => [key, 0])),
      url: options.url || '', title: options.title || '', titleUrl: SG.cleanUrl(options.url || ''),
      info: null, settingsReady: false, active: options.active !== false, destroyed: false,
      busy: false, picking: false, checking: false, tab: 'download', userEdited: false,
      jobs: [], optimisticJobs: new Map(), historyLoaded: false, historyError: '', historyFlight: null,
      timer: null, notifyToken: 0, themeOverride: options.theme || null,
      jobBusy: new Set(), clearing: false, historySignature: null,
      // Host capabilities from ping, fetched lazily once per host per panel open.
      caps: {}, capsFlight: {}, form: {}, formUrl: null, section: '', items: '', cancelConfirm: null
    };
    const listeners = [];
    function on(element, name, listener) {
      element.addEventListener(name, listener);
      listeners.push(() => element.removeEventListener(name, listener));
    }

    function showStatus(text = '', tone = 'idle') {
      if (state.destroyed) return;
      $('status').hidden = !text;
      $('status').dataset.tone = tone;
      $('status-text').textContent = text;
      $('status-icon').innerHTML = icon(tone === 'err' ? 'circle-alert' : tone === 'ok' ? 'circle-check' : tone === 'run' ? 'loader-circle' : 'info', 16);
    }

    function setOptions(select, entries, selected) {
      const signature = entries.map(entry => entry.join('\u0001')).join('\u0002');
      if (select.dataset.options !== signature) {
        const nodes = entries.map(([value, label]) => {
          const option = doc.createElement('option');
          option.value = value;
          option.textContent = label;
          option.title = label;
          return option;
        });
        select.replaceChildren(...nodes);
        select.dataset.options = signature;
      }
      select.value = entries.some(([value]) => value === selected) ? selected : entries[0]?.[0] || '';
    }

    function renderDirectories() {
      const { dir, recentDirs } = state.settings;
      const paths = [...new Set([dir, ...recentDirs].filter(path => path && path !== 'default'))];
      setOptions($('dir'), [['default', '默认目录（软件设置）'], ...paths.map(path => [path, path])], dir);
      const title = dir === 'default' ? '使用本机软件设置的默认保存目录' : dir;
      $('dir').title = title;
      $('dir').setAttribute('aria-description', title);
    }

    function storeCaps(host, result) {
      const features = new Set(Array.isArray(result?.features) ? result.features.filter(feature => typeof feature === 'string' && !['live', 'login-status'].includes(feature)) : []);
      state.caps[host] = { status: 'ready', features };
    }

    function ensureCaps(host) {
      if (state.destroyed) return Promise.resolve();
      if (state.caps[host]) return state.capsFlight[host] || Promise.resolve();
      state.caps[host] = { status: 'pending', features: new Set() };
      const flight = request({ type: 'sg-bridge-ping', host }).then(result => {
        if (!state.destroyed && state.capsFlight[host] === flight) storeCaps(host, result);
      }).catch(() => {
        if (!state.destroyed && state.capsFlight[host] === flight) state.caps[host] = { status: 'error', features: new Set() };
      }).finally(() => {
        if (state.capsFlight[host] !== flight) return;
        delete state.capsFlight[host];
        if (!state.destroyed) { evaluate(); renderHistory(); }
      });
      state.capsFlight[host] = flight;
      return flight;
    }

    const supports = (host, feature) => state.caps[host]?.status === 'ready' && state.caps[host].features.has(feature);
    const capsKnown = host => !!state.caps[host] && state.caps[host].status !== 'pending';

    function gate(element, unsupported) {
      if (unsupported) element.title = UPDATE_HINT;
      else if (element.title === UPDATE_HINT) element.removeAttribute('title');
    }

    function resetForm(url) {
      state.formUrl = url;
      state.section = '';
      state.items = '';
      $('section').value = '';
      $('items').value = '';
    }

    function renderSettings() {
      renderDirectories();
      $('media').value = state.settings.media;
      $('sponsorblock').value = state.settings.sponsorblock;
      $('danmaku').value = state.settings.danmaku;
      $('bili-subtitles').value = state.settings.biliSubtitles;
      $('audio-quality').value = state.settings.audioQuality;
      $('subtitles').value = state.settings.subtitles;
      $('sub-langs').value = state.settings.subLangs;
      $('metadata').checked = state.settings.metadata;
      $('embed-thumbnail').checked = state.settings.embedThumbnail;
      $('split-chapters').checked = state.settings.splitChapters;
      $('download-archive').checked = state.settings.downloadArchive;
      $('limit-rate').value = state.settings.limitRate;
      $('concurrent-fragments').value = state.settings.concurrentFragments;
      $('notify').checked = state.settings.notify;
      $('theme').value = state.settings.theme;
      app.dataset.theme = state.themeOverride || state.settings.theme;
      evaluate();
    }

    function mergeRemoteSettings(remote, snapshot, authoritative = []) {
      const clean = normalizedSettings(remote);
      for (const [key, value] of Object.entries(clean)) {
        if (state.revisions[key] === snapshot[key] && (!state.pendingSettings[key] || authoritative.includes(key))) state.settings[key] = value;
      }
      if (!state.destroyed) renderSettings();
    }

    function localSettings(patch) {
      const clean = normalizedSettings(patch);
      for (const [key, value] of Object.entries(clean)) {
        state.settings[key] = value;
        state.revisions[key] += 1;
      }
      renderSettings();
      return clean;
    }

    function persistSettings(patch, alreadyLocal = false) {
      const clean = alreadyLocal ? normalizedSettings(patch) : localSettings(patch);
      const snapshot = { ...state.revisions };
      const keys = Object.keys(clean);
      for (const key of keys) state.pendingSettings[key] += 1;
      // The service worker serializes storage mutations. Dispatch now so popup closure cannot drop queued patches.
      const operation = request({ type: 'sg-settings-set', settings: clean });
      operation.then(result => {
        if (!state.destroyed) mergeRemoteSettings(result.settings || {}, snapshot, keys);
      }).catch(error => {
        if (!state.destroyed) showStatus(`设置未保存：${errorText(error)}。本次选择仍可用于下载。`, 'err');
      }).finally(() => {
        for (const key of keys) state.pendingSettings[key] = Math.max(0, state.pendingSettings[key] - 1);
      });
      return operation;
    }

    async function loadSettings() {
      const snapshot = { ...state.revisions };
      const initiallyBlocked = Object.keys(state.pendingSettings).filter(key => state.pendingSettings[key] > 0);
      try {
        const result = await request({ type: 'sg-settings-get' });
        if (!state.destroyed) {
          const safe = { ...(result.settings || {}) };
          for (const key of initiallyBlocked) delete safe[key];
          mergeRemoteSettings(safe, snapshot);
        }
      } catch (error) {
        showStatus(`未能读取已保存设置：${errorText(error)}。请确认保存位置后重试。`, 'err');
      } finally {
        state.settingsReady = true;
        if (!state.destroyed) evaluate();
      }
    }

    function evaluate() {
      if (state.destroyed) return;
      state.info = SG.classify(state.url.trim());
      const info = state.info;
      if ((info?.url || '') !== state.formUrl) resetForm(info?.url || '');
      const host = info ? capsHost(info.platform) : null;
      if (host) void ensureCaps(host);
      const has = feature => !!host && supports(host, feature);
      const known = !!host && capsKnown(host);
      const audio = state.settings.media !== 'video';
      const original = state.settings.media === 'audio-original';
      const ytPlaylist = info?.platform === 'youtube' && info.kind === 'playlist';
      const playlistDirect = ytPlaylist && has('playlist');
      // Channels, and playlists on hosts without direct list support, are handoff-only.
      const collection = info?.platform === 'youtube' && info.kind !== 'video' && !playlistDirect;
      const direct = !!info && !collection;
      const isBili = info?.platform === 'bili';
      const ytVideo = info?.platform === 'youtube' && info.kind === 'video';
      const ytDirect = ytVideo || playlistDirect;
      const ready = state.settingsReady;
      const disabled = state.busy || state.picking || !ready;
      for (const name of ['quality', 'part', 'media', 'subtitles', 'sub-langs', 'metadata']) $(name).disabled = collection || !ready;

      // Per-download fields: time-range section and playlist items.
      const sectionVisible = ytVideo || info?.platform === 'x';
      const sectionBlocked = !sectionVisible ? '' : original ? '原音轨不重新编码，无法截取片段；请改选视频或 MP3 / M4A。' : '';
      const sectionEnabled = sectionVisible && has('sections') && !sectionBlocked && ready;
      const section = normalizeSection(state.section);
      const sectionInvalid = sectionEnabled && section === null;
      $('section-row').hidden = !sectionVisible;
      $('section').disabled = !sectionEnabled;
      gate($('section'), sectionVisible && known && !has('sections'));
      $('section').setAttribute('aria-invalid', String(sectionInvalid));
      $('section-error').hidden = !sectionInvalid;
      $('section-note').hidden = !sectionBlocked;
      $('section-note').textContent = sectionBlocked;
      const items = normalizeItems(state.items);
      const itemsInvalid = playlistDirect && items === null;
      $('items-row').hidden = !playlistDirect;
      $('items').disabled = !playlistDirect || !ready;
      $('items').setAttribute('aria-invalid', String(itemsInvalid));
      $('items-error').hidden = !itemsInvalid;

      // Persisted extras: SponsorBlock (YouTube), danmaku and subtitles (Bilibili).
      const sponsorVisible = ytDirect;
      const sponsorBlocked = !sponsorVisible ? '' : original ? '原音轨不重新编码，无法移除片段；请改选 MP3 / M4A 或关闭跳过片段。' : '';
      const sponsorEnabled = sponsorVisible && has('sponsorblock') && !sponsorBlocked;
      $('sponsorblock-row').hidden = !sponsorVisible;
      $('sponsorblock').disabled = !sponsorEnabled || !ready;
      gate($('sponsorblock'), sponsorVisible && known && !has('sponsorblock'));
      $('sponsorblock-note').hidden = !sponsorBlocked;
      $('sponsorblock-note').textContent = sponsorBlocked;
      $('sponsorblock-help').hidden = !sponsorVisible;
      const practicalVisible = direct && !isBili;
      $('practical-options').hidden = !practicalVisible;
      for (const [name, feature, blocked] of [
        ['embed-thumbnail', 'thumbnail', original], ['split-chapters', 'chapters', original || !!state.section],
        ['download-archive', 'archive', false], ['limit-rate', 'transfer', false], ['concurrent-fragments', 'transfer', false]
      ]) {
        $(name).disabled = !practicalVisible || !has(feature) || blocked || !ready;
        gate($(name), practicalVisible && known && !has(feature));
      }
      $('practical-note').textContent = original ? '原音轨不支持嵌入封面与章节拆分；防重复和传输设置仍可使用。'
        : state.section ? '时间片段不支持同时按章节拆分。'
        : '这些选项用于 YouTube / X。分片并发仅对支持的 DASH / HLS 源生效；限速按下载连接计算。';
      $('danmaku-row').hidden = !isBili;
      $('bili-subtitles-row').hidden = !isBili;
      $('bili-extras-note').hidden = !isBili;
      $('danmaku').disabled = !isBili || !has('danmaku') || !ready;
      $('bili-subtitles').disabled = !isBili || !has('subtitles') || !ready;
      gate($('danmaku'), isBili && known && !has('danmaku'));
      gate($('bili-subtitles'), isBili && known && !has('subtitles'));

      // Bilibili range: videos offer parts (+ collection); bangumi/cheese offer current / all episodes.
      const partEntries = !isBili ? [['current', '当前分 P'], ['all', '全部分 P']] : info.kind === 'video'
        ? [['current', '当前分 P'], ['all', '全部分 P'], ...(has('collection') ? [['collection', '整个合集']] : [])]
        : [['current', '当前剧集'], ...(has('collection') ? [['collection', '全部剧集']] : [])];
      setOptions($('part'), partEntries, state.settings.part);
      const part = partEntries.some(([value]) => value === state.settings.part) ? state.settings.part : 'current';
      $('part-label').textContent = isBili && info.kind !== 'video' ? '剧集范围' : '分 P 范围';
      if (partEntries.length === 1) $('part').disabled = true;
      gate($('part'), isBili && known && !has('collection'));

      const missing = [];
      if (known) {
        if (sponsorVisible && !has('sponsorblock')) missing.push('跳过片段');
        if (sectionVisible && !has('sections')) missing.push('片段');
        if (ytPlaylist && !has('playlist')) missing.push('列表下载');
        if (isBili && !has('danmaku')) missing.push('弹幕');
        if (isBili && !has('subtitles')) missing.push('字幕');
        if (isBili && !has('collection')) missing.push(info.kind === 'video' ? '整个合集' : '全部剧集');
        if (practicalVisible && ['thumbnail', 'chapters', 'archive', 'transfer'].some(feature => !has(feature))) missing.push('封面 / 章节 / 防重复 / 传输设置');
      }
      $('update-note').hidden = !missing.length;
      $('update-note').textContent = state.caps[host]?.status === 'error'
        ? `本机组件未响应，${missing.join('、')}暂不可用。`
        : `${UPDATE_HINT}：${missing.join('、')}。`;

      state.form = {
        handoffOnly: collection, playlistDirect, part, valid: !sectionInvalid && !itemsInvalid,
        sponsorblock: sponsorEnabled ? state.settings.sponsorblock : 'off',
        section: sectionEnabled && section ? section : '',
        items: playlistDirect && items ? items : '',
        danmaku: isBili && has('danmaku') ? state.settings.danmaku : 'off',
        biliSubtitles: isBili && has('subtitles') ? state.settings.biliSubtitles : 'off'
      };

      $('send').disabled = !info || disabled || (!collection && !state.form.valid);
      $('send').setAttribute('aria-busy', String(state.busy));
      $('copy').disabled = !info || state.busy;
      $('handoff').disabled = !info || disabled;
      $('pick-folder').disabled = state.picking || !ready;
      $('dir').disabled = collection || state.picking || !ready;
      $('handoff').hidden = !info || info.platform === 'x' || collection;
      $('handoff-text').textContent = isBili ? '送入 Bili23' : '送入 FluentYTDL';
      $('send-text').textContent = state.busy ? '正在提交…' : collection ? '送入 FluentYTDL' : playlistDirect ? '下载列表' : audio ? '下载音频' : '下载视频';
      $('send-icon').innerHTML = icon(state.busy ? 'loader-circle' : collection ? 'external-link' : 'download', 18);
      $('handoff-note').hidden = !collection;
      $('download-options').hidden = collection;
      $('quality-row').hidden = !direct || audio;
      // Collection links have no per-download configuration; Bilibili keeps its range row.
      $('part-row').hidden = collection || !isBili;
      $('video-options').hidden = $('quality-row').hidden && $('part-row').hidden;
      $('video-options').dataset.columns = !$('quality-row').hidden && !$('part-row').hidden ? '2' : '1';
      setOptions($('quality'), isBili ? BILI_QUALITY : QUALITY, isBili ? state.settings.qualityBili : state.settings.quality);
      $('quality-label').textContent = '画质上限';
      $('media-row').hidden = collection;
      $('audio-original-note').hidden = !direct || !original;
      $('audio-quality-row').hidden = !direct || !audio || original;
      $('audio-quality-note').hidden = !direct || !audio || original;
      $('audio-quality').disabled = !direct || !audio || original || !ready;
      $('subtitles-row').hidden = !ytDirect || audio;
      $('langs-row').hidden = !ytDirect || audio || state.settings.subtitles === 'off';
      $('subtitles-note').hidden = !ytDirect || audio;
      $('metadata-row').hidden = !direct || !['youtube', 'x'].includes(info?.platform);
      const extras = [];
      if (ytDirect && !audio && state.settings.subtitles !== 'off') extras.push('字幕');
      if (state.form.sponsorblock !== 'off') extras.push('跳过片段');
      if (state.form.danmaku !== 'off') extras.push('弹幕');
      if (state.form.biliSubtitles !== 'off') extras.push('字幕');
      if (state.settings.metadata && direct && !isBili) extras.push('媒体信息');
      if (state.settings.notify) extras.push('通知');
      $('more-value').textContent = collection ? '通知与外观' : `${original ? '原音轨' : audio ? state.settings.media === 'audio-mp3' ? 'MP3 音频' : 'M4A 音频' : '视频'} · ${extras.length ? extras.join(' / ') : '默认设置'}`;
      $('detected').dataset.valid = info ? 'true' : state.url ? 'false' : 'empty';
      $('platform').textContent = info ? labelFor(info.platform) : state.url ? '暂不支持' : '等待链接';
      $('platform').dataset.platform = info?.platform || '';
      const knownTitle = info && state.titleUrl === info.url ? state.title : '';
      $('source-title').textContent = info ? knownTitle || info.url.replace(/^https:\/\/(www\.)?/, '') : state.url ? '请使用受支持的视频、推文或播放页链接' : '好内容，随心留存';
      $('source-title').title = info ? knownTitle ? `${knownTitle}\n${info.url}` : info.url : '';
      $('kind').textContent = info ? isBili && info.page > 1 ? `分 P ${info.page}` : SG.kindLabel(info.kind) : '';
      $('url').setAttribute('aria-invalid', String(!!state.url && !info));
    }

    function setTab(tab, focus = false) {
      if (state.destroyed) return;
      state.tab = tab === 'history' ? 'history' : 'download';
      for (const name of ['download', 'history']) {
        const selected = name === state.tab;
        $(`tab-${name}`).setAttribute('aria-selected', String(selected));
        $(`tab-${name}`).tabIndex = selected ? 0 : -1;
        $(`${name}-panel`).hidden = !selected;
      }
      if (state.tab !== 'history') $('clear-confirm').hidden = true;
      $('workspace').scrollTop = 0;
      if (focus) $(`tab-${state.tab}`).focus();
    }

    function createElement(tag, className, text) {
      const element = doc.createElement(tag);
      element.className = className;
      if (text !== undefined) element.textContent = text;
      return element;
    }

    function jobButton(label, iconName, action, job, extraClass = '') {
      const button = createElement('button', `vy-text-button ${extraClass}`.trim());
      button.type = 'button';
      button.dataset.action = action;
      button.dataset.job = jobKey(job);
      button.disabled = state.jobBusy.has(jobKey(job));
      button.innerHTML = icon(iconName, 14);
      button.appendChild(createElement('span', '', label));
      return button;
    }

    // Finite requests are capped at 200; empty or open-ended ranges have no known count yet.
    function itemCount(spec) {
      if (typeof spec !== 'string' || !spec || spec.split(',').some(part => part.endsWith('-'))) return null;
      return Math.min(200, spec.split(',').reduce((total, part) => {
        const [from, to] = part.split('-');
        if (to === undefined) return total + 1;
        return total + Math.max(0, Number(to) - Number(from) + 1);
      }, 0));
    }

    function jobTags(job) {
      const request = job.request || {};
      const tags = [];
      if (request.sponsorblock === 'sponsor' || request.sponsorblock === 'all') tags.push('跳过片段');
      if (typeof request.section === 'string' && request.section) tags.push(`片段 ${request.section.replace('-', '–').replace(/–inf$/, '–结尾')}`);
      if (request.danmaku === 'ass' || request.danmaku === 'xml') tags.push('弹幕');
      if (request.biliSubtitles === 'srt') tags.push('字幕');
      if (request.playlist === true) {
        const actual = Number.isInteger(job.fileCount) && job.fileCount >= 0 ? job.fileCount : null;
        const count = isFinishedJob(job) && actual !== null ? actual : (itemCount(request.items) ?? actual);
        tags.push(count !== null ? `列表 ${count} 项` : '列表');
      }
      if (['collection'].includes(request.part)) tags.push('合集');
      if (request.live === 'from-start') tags.push('直播录制');
      return tags;
    }

    function renderJob(job) {
      const view = SG.jobView(job);
      const card = createElement('article', 'vy-job');
      card.dataset.job = jobKey(job);
      card.dataset.state = job.state || 'queued';
      const top = createElement('div', 'vy-job-top');
      const platform = createElement('span', 'vy-job-platform', labelFor(job.platform));
      platform.dataset.platform = job.platform;
      const audioQuality = AUDIO_QUALITY.includes(job.request?.audioQuality) ? job.request.audioQuality : 'auto';
      const tags = jobTags(job);
      const base = job.media === 'audio-original' ? '原音轨 · 不转码' : job.media?.startsWith('audio-') ? `${job.media === 'audio-mp3' ? 'MP3' : 'M4A'} · ${audioQuality === 'auto' ? '内核默认' : `${audioQuality} kbps`}` : '视频';
      const media = createElement('span', 'vy-job-media', tags.length ? `${base} · ${tags.join(' · ')}` : base);
      const canceling = !!job.cancelRequested && isActiveJob(job);
      const status = createElement('span', 'vy-job-state', canceling ? '取消中' : ({ queued: '排队中', downloading: '下载中', completed: '已完成', failed: '未完成', canceled: '已取消' })[job.state] || '等待更新');
      status.dataset.tone = view.tone;
      top.append(platform, media, status);
      const title = createElement('h3', 'vy-job-title', job.title || SG.cleanUrl(job.url || '') || '下载任务');
      title.title = job.title || job.url || '下载任务';
      const path = createElement('div', 'vy-job-path');
      path.innerHTML = icon('folder-open', 13);
      const pathText = createElement('span', '', job.downloadDir || '使用本机软件的默认目录');
      pathText.title = job.downloadDir || '任务启动后由本机内核确认实际保存位置';
      path.appendChild(pathText);
      card.append(top, title, path);
      if (isActiveJob(job)) {
        const finite = typeof job.percent === 'number' && Number.isFinite(job.percent);
        const percent = finite ? Math.max(0, Math.min(100, Math.round(job.percent))) : 0;
        const progressText = createElement('div', 'vy-job-progress-label');
        const details = createElement('span', 'vy-job-progress-details');
        if (!canceling && job.state === 'downloading' && !job.connectionError
            && Number.isFinite(job.speedBytesPerSecond) && job.speedBytesPerSecond >= 0) {
          const units = ['B/s', 'KB/s', 'MB/s', 'GB/s'];
          const unit = Math.min(units.length - 1, Math.floor(Math.log(Math.max(1, job.speedBytesPerSecond)) / Math.log(1024)));
          const speed = createElement('span', 'vy-job-speed', `${(job.speedBytesPerSecond / 1024 ** unit).toFixed(unit ? 1 : 0)} ${units[unit]}`);
          speed.setAttribute('aria-label', `下载速率 ${speed.textContent}`);
          details.appendChild(speed);
        }
        details.appendChild(createElement('span', '', canceling ? '取消中' : job.state === 'queued' ? '排队中' : finite ? `${percent}%` : '准备中'));
        progressText.append(createElement('span', '', canceling ? '正在取消，等待本机内核停止' : job.state === 'queued' ? '等待本机内核开始' : finite ? '正在保存内容' : '正在连接与准备文件'), details);
        const progress = createElement('div', 'vy-progress');
        progress.setAttribute('role', 'progressbar');
        progress.setAttribute('aria-label', `${job.title || labelFor(job.platform)}下载进度`);
        progress.setAttribute('aria-valuemin', '0');
        progress.setAttribute('aria-valuemax', '100');
        if (finite || job.state === 'queued') progress.setAttribute('aria-valuenow', String(percent));
        else progress.dataset.indeterminate = 'true';
        const bar = createElement('span', 'vy-progress-fill');
        bar.style.width = `${percent}%`;
        progress.appendChild(bar);
        card.append(progressText, progress);
      } else if (job.state === 'failed') {
        const error = createElement('p', 'vy-job-error', job.error || view.text || '下载未完成，请检查本机内核后重试');
        error.title = error.textContent;
        card.appendChild(error);
      }
      const confirm = isActiveJob(job) && state.cancelConfirm === jobKey(job) ? (() => {
        const box = createElement('div', 'vy-inline-confirm');
        box.setAttribute('role', 'group');
        box.setAttribute('aria-label', `确认取消下载：${job.title || labelFor(job.platform)}`);
        box.appendChild(createElement('p', '', '取消后已下载的部分将被删除，已完成的文件保留。'));
        const buttons = createElement('div', '');
        buttons.append(jobButton('继续下载', 'download', 'cancel-keep', job), jobButton('取消下载', 'x', 'cancel-accept', job, 'vy-danger-button'));
        box.appendChild(buttons);
        return box;
      })() : null;
      if (confirm) card.appendChild(confirm);
      const actions = createElement('div', 'vy-job-actions');
      const date = new Date(job.createdAt || job.updatedAt || '');
      const time = createElement('time', 'vy-job-time', Number.isNaN(date.getTime()) ? '本机任务' : new Intl.DateTimeFormat('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }).format(date));
      if (!Number.isNaN(date.getTime())) time.dateTime = date.toISOString();
      actions.appendChild(time);
      if (isActiveJob(job) && !canceling) {
        const host = capsHost(job.platform);
        const cancel = jobButton('取消', 'x', 'cancel', job);
        cancel.disabled = state.jobBusy.has(jobKey(job)) || (capsKnown(host) && !supports(host, 'cancel'));
        if (capsKnown(host) && !supports(host, 'cancel')) cancel.title = UPDATE_HINT;
        actions.appendChild(cancel);
      }
      const retired = job.request?.live === 'from-start' || job.request?.danmaku === 'xml';
      if (retired && ['failed', 'canceled'].includes(job.state)) card.appendChild(createElement('p', 'vy-option-note', '此旧任务使用已停用功能，请重新添加普通下载。'));
      if (!retired && job.state === 'failed') actions.appendChild(jobButton('重试', 'refresh-cw', 'retry', job));
      if (!retired && job.state === 'canceled') actions.appendChild(jobButton('重新下载', 'refresh-cw', 'retry', job));
      if (job.downloadDir) {
        const folder = jobButton('打开位置', 'folder-open', 'folder', job);
        folder.title = job.downloadDir;
        actions.appendChild(folder);
      }
      const mediaFiles = (job.files || []).map((path, index) => ({ path, index })).filter(item => /\.(mp4|mkv|webm|mov|ts|mp3|m4a|opus|ogg|flac|aac|mka|wav|ac3|eac3)$/i.test(item.path));
      if (mediaFiles.length && !isActiveJob(job)) {
        const open = jobButton('打开文件', 'external-link', 'file', job);
        open.dataset.fileIndex = String(mediaFiles[0].index);
        open.dataset.filePath = mediaFiles[0].path;
        open.disabled = state.jobBusy.has(jobKey(job)) || !supports(capsHost(job.platform), 'open-file');
        if (!supports(capsHost(job.platform), 'open-file')) open.title = UPDATE_HINT;
        if (mediaFiles.length > 1) {
          const select = createElement('select', 'vy-job-file');
          select.setAttribute('aria-label', '选择要打开的文件');
          for (const item of mediaFiles) {
            const option = createElement('option', '', item.path.split(/[\\/]/).pop());
            option.value = String(item.index); select.appendChild(option);
          }
          select.value = open.dataset.fileIndex;
          // The card is replaced as a whole; this listener is collected with the card.
          select.addEventListener('change', () => { open.dataset.fileIndex = select.value; open.dataset.filePath = job.files[Number(select.value)]; });
          actions.appendChild(select);
        }
        actions.appendChild(open);
      }
      card.appendChild(actions);
      return card;
    }

    function renderHistory() {
      if (state.destroyed) return;
      // Cancel availability is per host; fetch lazily once per mount for each platform present in history.
      for (const host of new Set(state.jobs.map(job => capsHost(job.platform)))) void ensureCaps(host);
      const activeCount = state.jobs.filter(isActiveJob).length;
      const query = $('history-search').value.trim().toLocaleLowerCase();
      const platform = $('history-platform').value;
      const filterState = $('history-state').value;
      const visibleJobs = state.jobs.filter(job => (!query || `${job.title || ''} ${job.url || ''}`.toLocaleLowerCase().includes(query))
        && (platform === 'all' || job.platform === platform)
        && (filterState === 'all' || (filterState === 'active' ? isActiveJob(job) : job.state === filterState)));
      $('active-count').hidden = activeCount === 0;
      $('active-count').textContent = String(activeCount);
      $('active-count').setAttribute('aria-label', `${activeCount} 项进行中的任务`);
      $('history-summary').textContent = state.jobs.length ? `${state.jobs.length} 项记录${activeCount ? ` · ${activeCount} 项进行中` : ''}` : '本机任务记录';
      $('history-clear').hidden = !state.jobs.some(isFinishedJob);
      $('history-clear').disabled = state.clearing;
      $('clear-accept').disabled = state.clearing;
      $('clear-cancel').disabled = state.clearing;
      $('history-error').hidden = !state.historyError;
      $('history-error').textContent = state.historyError;
      $('history-empty').hidden = visibleJobs.length > 0;
      $('empty-title').textContent = !state.historyLoaded && state.historyFlight ? '正在读取任务' : state.historyError && !state.jobs.length ? '记录暂时不可用' : '还没有任务';
      $('empty-description').textContent = !state.historyLoaded && state.historyFlight ? '正在读取本机保存的任务记录…' : state.historyError ? '可点击右上角刷新重试，不影响已开始的下载。' : '喜欢的内容，从一个链接开始。\n任务记录只保存在本机。';
      if (state.jobs.length && !visibleJobs.length) { $('empty-title').textContent = '没有匹配的任务'; $('empty-description').textContent = '可更换关键词、平台或状态筛选。'; }
      if (query || platform !== 'all' || filterState !== 'all') $('history-summary').textContent = `${visibleJobs.length} / ${state.jobs.length} 项记录`;
      const signature = JSON.stringify([visibleJobs, [...state.jobBusy], state.cancelConfirm,
        Object.fromEntries(Object.entries(state.caps).map(([host, caps]) => [host, [caps.status, [...(caps.features || [])]]]))]);
      if (signature !== state.historySignature) {
        const focused = app.getRootNode().activeElement || doc.activeElement;
        const restore = focused?.dataset?.job && focused?.dataset?.action ? { job: focused.dataset.job, action: focused.dataset.action } : null;
        $('jobs').replaceChildren(...visibleJobs.map(renderJob));
        state.historySignature = signature;
        if (restore) {
          const target = [...$('jobs').querySelectorAll('button')].find(button => button.dataset.job === restore.job && button.dataset.action === restore.action);
          if (target) target.focus({ preventScroll: true });
          else $('history-refresh').focus({ preventScroll: true });
        }
        const next = state.cancelConfirm ? $('jobs').querySelector('button[data-action="cancel-keep"]') : null;
        if (next) next.focus({ preventScroll: true });
      }
    }

    function acceptJobs(jobs) {
      const valid = (Array.isArray(jobs) ? jobs : []).filter(job => job && job.id && ['youtube', 'x', 'bili'].includes(job.platform));
      const remoteKeys = new Set(valid.map(jobKey));
      for (const [key, pending] of state.optimisticJobs) {
        if (remoteKeys.has(key) || Date.now() - pending.at > 60000) state.optimisticJobs.delete(key);
      }
      const pending = [...state.optimisticJobs.values()].map(entry => entry.job);
      state.jobs = [...pending, ...valid].slice(0, 60);
      state.historyLoaded = true;
      renderHistory();
    }

    function refreshHistory(live = true) {
      if (state.destroyed || state.clearing) return Promise.resolve();
      if (state.historyFlight) return state.historyFlight;
      $('history-refresh').disabled = true;
      $('history-refresh').setAttribute('aria-busy', 'true');
      const flight = request({ type: live ? 'sg-history-refresh' : 'sg-history-get' }).then(result => {
        if (state.destroyed) return;
        state.historyError = '';
        acceptJobs(result.jobs);
      }).catch(error => {
        if (state.destroyed) return;
        state.historyError = `读取任务失败：${errorText(error)}`;
        renderHistory();
      }).finally(() => {
        state.historyFlight = null;
        if (!state.destroyed) {
          $('history-refresh').disabled = false;
          $('history-refresh').setAttribute('aria-busy', 'false');
          renderHistory();
        }
      });
      state.historyFlight = flight;
      renderHistory();
      return flight;
    }

    async function refreshAfterAction() {
      if (state.historyFlight) await state.historyFlight;
      return refreshHistory(true);
    }

    function startPolling() {
      if (state.destroyed || !state.active || state.timer !== null) return;
      state.timer = globalThis.setInterval(() => {
        if (state.active && !state.destroyed) void refreshHistory(true);
      }, 3000);
    }

    function stopPolling() {
      if (state.timer !== null) globalThis.clearInterval(state.timer);
      state.timer = null;
    }

    function rememberAccepted(result, source, payload) {
      if (!result.jobId) return;
      const job = {
        id: result.jobId, platform: result.platform || source.platform, url: result.url || source.url,
        title: payload.title || '', downloadDir: result.downloadDir || (payload.dir !== 'default' ? payload.dir : ''),
        state: 'queued', percent: 0, media: payload.media || 'video', request: { ...payload }, createdAt: new Date().toISOString()
      };
      state.optimisticJobs.set(jobKey(job), { job, at: Date.now() });
      state.jobs = [job, ...state.jobs.filter(existing => jobKey(existing) !== jobKey(job))].slice(0, 60);
      renderHistory();
    }

    async function act(mode) {
      const info = state.info;
      if (!info || state.busy || state.destroyed) return;
      const form = state.form;
      const collection = form.handoffOnly === true;
      // Never send a known-to-fail direct collection request or an invalid per-download field.
      if (mode === 'direct' && (collection || !form.valid)) return;
      state.busy = true;
      evaluate();
      showStatus(mode === 'copy' ? '正在复制链接…' : '正在提交到本机…', 'run');
      try {
        if (mode === 'copy') {
          await request({ type: 'sg-copy', url: info.url });
          showStatus('链接已复制', 'ok');
          return;
        }
        if (!state.settingsReady || state.picking) return;
        const settings = { ...state.settings };
        const isAudio = settings.media !== 'video';
        const ytVideo = info.platform === 'youtube' && info.kind === 'video';
        const directMode = mode === 'direct' && !collection;
        const payload = {
          type: 'sg-deliver', url: info.url, mode,
          quality: collection || isAudio ? 'best' : info.platform === 'bili' ? settings.qualityBili : settings.quality,
          part: form.part, dir: settings.dir, media: collection || mode === 'handoff' ? 'video' : settings.media,
          audioQuality: directMode && ['audio-mp3', 'audio-m4a'].includes(settings.media) ? settings.audioQuality : 'auto',
          subtitles: (ytVideo || form.playlistDirect) && !isAudio ? settings.subtitles : 'off', subLangs: settings.subLangs,
          metadata: ['youtube', 'x'].includes(info.platform) && !collection && settings.metadata,
          sponsorblock: collection ? 'off' : form.sponsorblock, section: collection ? '' : form.section, live: 'off',
          liveMinutes: '240', playlist: directMode && form.playlistDirect === true, items: directMode && form.playlistDirect ? form.items : '',
          danmaku: form.danmaku, biliSubtitles: form.biliSubtitles,
          embedThumbnail: directMode && info.platform !== 'bili' && supports('x', 'thumbnail') && settings.media !== 'audio-original' && settings.embedThumbnail,
          splitChapters: directMode && info.platform !== 'bili' && supports('x', 'chapters') && settings.media !== 'audio-original' && !form.section && settings.splitChapters,
          downloadArchive: directMode && info.platform !== 'bili' && supports('x', 'archive') && settings.downloadArchive,
          limitRate: directMode && info.platform !== 'bili' && supports('x', 'transfer') ? settings.limitRate : 'off',
          concurrentFragments: directMode && info.platform !== 'bili' && supports('x', 'transfer') ? settings.concurrentFragments : 'auto'
        };
        if (state.title && state.titleUrl === info.url) payload.title = state.title;
        const result = await request(payload);
        if (state.destroyed) return;
        if (mode === 'handoff') {
          showStatus(info.platform === 'bili' ? `链接已复制${result.launched ? '，已启动 Bili23' : ''}；请在 Bili23 中确认解析与保存位置。` : '链接已复制；请在 FluentYTDL 中确认解析、格式与保存位置。', 'ok');
        } else {
          rememberAccepted(result, info, payload);
          showStatus('已加入本机任务，关闭面板后仍会继续。', 'ok');
          setTab('history', true);
          await refreshAfterAction();
        }
      } catch (error) { showStatus(errorText(error), 'err'); }
      finally {
        state.busy = false;
        if (!state.destroyed) evaluate();
      }
    }

    async function pickFolder() {
      if (state.picking || !state.settingsReady) return;
      state.picking = true;
      const snapshot = { ...state.revisions };
      evaluate();
      showStatus('请在系统窗口中选择文件夹。工具栏面板可能暂时关闭，选择会自动保存。', 'run');
      try {
        // The background only forwards initialDir on hosts that advertise pick-folder-initial.
        const host = state.info?.platform === 'bili' ? 'bili' : 'x';
        await ensureCaps(host);
        if (state.destroyed) return;
        const dir = state.settings.dir;
        const initialDir = supports(host, 'pick-folder-initial') && dir && dir !== 'default' ? dir : undefined;
        const result = await request({ type: 'sg-pick-folder', host, ...(initialDir ? { initialDir } : {}) });
        if (state.destroyed) return;
        if (result.canceled) { showStatus('已取消选择，保存位置未更改。'); return; }
        if (result.settings) mergeRemoteSettings(result.settings, snapshot);
        if (result.path && state.revisions.dir === snapshot.dir) {
          localSettings({ dir: result.path, recentDirs: result.recentDirs || result.settings?.recentDirs || state.settings.recentDirs });
        }
        showStatus('保存位置已更新', 'ok');
      } catch (error) { showStatus(errorText(error), 'err'); }
      finally {
        state.picking = false;
        if (!state.destroyed) { renderDirectories(); evaluate(); }
      }
    }

    async function checkBridge() {
      if (state.checking) return;
      state.checking = true;
      $('bridge-check').removeAttribute('data-state');
      $('bridge-check').disabled = true;
      $('bridge-check').setAttribute('aria-busy', 'true');
      showStatus('正在检查本机连接…', 'run');
      const caps = state.caps;
      const results = await Promise.all(['x', 'bili'].map(async host => {
        const label = host === 'x' ? 'YouTube / X' : 'bilibili';
        try {
          const result = await request({ type: 'sg-bridge-ping', host });
          if (!state.destroyed && state.caps === caps) storeCaps(host, result);
          return result.engineAvailable === false
            ? { bad: true, text: `${label}：桥接已连接，但未找到下载内核` }
            : { bad: false, text: `${label}：已连接${result.version ? ` · v${result.version}` : ''}` };
        } catch (error) { return { bad: true, text: `${label}：${errorText(error)}` }; }
      }));
      state.checking = false;
      if (state.destroyed) return;
      const bad = results.some(result => result.bad);
      $('bridge-check').disabled = false;
      $('bridge-check').setAttribute('aria-busy', 'false');
      if (state.caps !== caps) return;
      $('bridge-check').dataset.state = bad ? 'error' : 'success';
      showStatus(results.map(result => result.text).join('\n'), bad ? 'err' : 'ok');
      evaluate();
      renderHistory();
    }

    async function handleJobAction(event) {
      const button = event.target.closest('button[data-action]');
      if (!button || !$('jobs').contains(button)) return;
      const job = state.jobs.find(item => jobKey(item) === button.dataset.job);
      if (!job || state.jobBusy.has(jobKey(job))) return;
      const action = button.dataset.action;
      if (action === 'retry' && !['failed', 'canceled'].includes(job.state)) return;
      if (action === 'cancel' && !isActiveJob(job)) return;
      if (action === 'cancel') {
        state.cancelConfirm = jobKey(job);
        renderHistory();
        return;
      }
      if (action === 'cancel-keep') {
        state.cancelConfirm = null;
        renderHistory();
        const trigger = [...$('jobs').querySelectorAll('button[data-action="cancel"]')].find(item => item.dataset.job === jobKey(job));
        (trigger || $('history-refresh')).focus({ preventScroll: true });
        return;
      }
      state.cancelConfirm = null;
      state.jobBusy.add(jobKey(job));
      renderHistory();
      try {
        if (action === 'folder') {
          await request({ type: 'sg-job-open-folder', jobId: job.id, platform: job.platform });
          showStatus('已请求打开文件夹', 'ok');
        } else if (action === 'file') {
          await request({ type: 'sg-job-open-file', jobId: job.id, platform: job.platform, fileIndex: Number(button.dataset.fileIndex), filePath: button.dataset.filePath });
          showStatus('已请求打开媒体文件', 'ok');
        } else if (action === 'retry') {
          const result = await request({ type: 'sg-job-retry', jobId: job.id, platform: job.platform });
          if (state.destroyed) return;
          rememberAccepted(result, { platform: job.platform, url: job.url }, { ...(job.request || {}), title: job.title, dir: job.downloadDir || 'default', media: job.media || 'video', audioQuality: job.request?.audioQuality ?? 'auto' });
          showStatus('已重新加入本机队列', 'ok');
          await refreshAfterAction();
        } else if (action === 'cancel-accept') {
          await request({ type: 'sg-job-cancel', jobId: job.id, platform: job.platform });
          if (state.destroyed) return;
          job.cancelRequested = true;
          showStatus('已请求取消下载；正在等待本机内核停止。', 'ok');
          await refreshAfterAction();
        }
      } catch (error) { showStatus(errorText(error), 'err'); }
      finally { state.jobBusy.delete(jobKey(job)); renderHistory(); }
    }

    async function clearHistory() {
      if (state.clearing) return;
      state.clearing = true;
      renderHistory();
      try {
        if (state.historyFlight) await state.historyFlight;
        if (state.destroyed) return;
        const result = await request({ type: 'sg-history-clear' });
        if (state.destroyed) return;
        // The backend keeps active jobs and deletes records only, never files.
        state.optimisticJobs.forEach((entry, key) => { if (isFinishedJob(entry.job)) state.optimisticJobs.delete(key); });
        acceptJobs(result.jobs);
        $('clear-confirm').hidden = true;
        showStatus('已清除结束的记录。文件未删除，进行中的任务仍会继续。', 'ok');
        $('history-refresh').focus({ preventScroll: true });
      } catch (error) { showStatus(errorText(error), 'err'); }
      finally { state.clearing = false; renderHistory(); }
    }

    on($('url'), 'input', () => {
      state.url = $('url').value;
      state.userEdited = true;
      evaluate();
    });
    on($('send'), 'click', () => { void act(state.form.handoffOnly ? 'handoff' : 'direct'); });
    on($('handoff'), 'click', () => { void act('handoff'); });
    on($('copy'), 'click', () => { void act('copy'); });
    on($('pick-folder'), 'click', () => { void pickFolder(); });
    on($('bridge-check'), 'click', () => { void checkBridge(); });
    on($('close'), 'click', () => options.onClose?.());
    on($('quality'), 'change', () => { void persistSettings(state.info?.platform === 'bili' ? { qualityBili: $('quality').value } : { quality: $('quality').value }); });
    on($('part'), 'change', () => { void persistSettings({ part: $('part').value }); });
    on($('dir'), 'change', () => { void persistSettings({ dir: $('dir').value }); });
    on($('media'), 'change', () => { void persistSettings({ media: $('media').value }); });
    on($('audio-quality'), 'change', () => { void persistSettings({ audioQuality: $('audio-quality').value }); });
    on($('subtitles'), 'change', () => { void persistSettings({ subtitles: $('subtitles').value }); });
    on($('sponsorblock'), 'change', () => { void persistSettings({ sponsorblock: $('sponsorblock').value }); });
    on($('danmaku'), 'change', () => { void persistSettings({ danmaku: $('danmaku').value }); });
    on($('bili-subtitles'), 'change', () => { void persistSettings({ biliSubtitles: $('bili-subtitles').value }); });
    on($('section'), 'input', () => { state.section = $('section').value; evaluate(); });
    on($('items'), 'input', () => { state.items = $('items').value; evaluate(); });
    on($('sub-langs'), 'change', () => { void persistSettings({ subLangs: $('sub-langs').value }); });
    on($('metadata'), 'change', () => { void persistSettings({ metadata: $('metadata').checked }); });
    for (const [name, key] of [['embed-thumbnail', 'embedThumbnail'], ['split-chapters', 'splitChapters'], ['download-archive', 'downloadArchive']])
      on($(name), 'change', () => { void persistSettings({ [key]: $(name).checked }); });
    for (const [name, key] of [['limit-rate', 'limitRate'], ['concurrent-fragments', 'concurrentFragments']])
      on($(name), 'change', () => { void persistSettings({ [key]: $(name).value }); });
    on($('history-search'), 'input', renderHistory);
    on($('history-platform'), 'change', renderHistory);
    on($('history-state'), 'change', renderHistory);
    on($('theme'), 'change', () => { state.themeOverride = null; void persistSettings({ theme: $('theme').value }); });
    on($('notify'), 'change', async () => {
      const checked = $('notify').checked;
      const token = ++state.notifyToken;
      localSettings({ notify: checked });
      if (!checked) { void persistSettings({ notify: false }, true); return; }
      if (!globalThis.chrome?.permissions?.request) {
        localSettings({ notify: false });
        void persistSettings({ notify: false }, true);
        showStatus('请从工具栏打开 Veylo 后开启通知；不影响下载。');
        return;
      }
      state.pendingSettings.notify += 1;
      const granted = await notificationPermission();
      state.pendingSettings.notify = Math.max(0, state.pendingSettings.notify - 1);
      if (state.destroyed || token !== state.notifyToken) return;
      if (!granted) {
        localSettings({ notify: false });
        void persistSettings({ notify: false }, true);
        showStatus('未开启系统通知，不影响下载。可随时再次选择。');
        return;
      }
      const revision = state.revisions.notify;
      try {
        await persistSettings({ notify: true }, true);
        if (!state.destroyed && revision === state.revisions.notify && !state.settings.notify) showStatus('通知权限尚未生效，不影响下载。请再次尝试开启。');
      } catch (_) {
        if (!state.destroyed && revision === state.revisions.notify) localSettings({ notify: false });
      }
    });
    for (const name of ['download', 'history']) {
      on($(`tab-${name}`), 'click', () => setTab(name));
      on($(`tab-${name}`), 'keydown', event => {
        if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
        event.preventDefault();
        setTab(event.key === 'Home' ? 'download' : event.key === 'End' ? 'history' : state.tab === 'download' ? 'history' : 'download', true);
      });
    }
    on($('history-refresh'), 'click', () => { void refreshHistory(true); });
    on($('history-clear'), 'click', () => { $('clear-confirm').hidden = false; $('clear-cancel').focus(); });
    on($('clear-cancel'), 'click', () => { $('clear-confirm').hidden = true; $('history-clear').focus(); });
    on($('clear-accept'), 'click', () => { void clearHistory(); });
    on($('empty-download'), 'click', () => { setTab('download', true); $('url').focus(); });
    on($('jobs'), 'click', event => { void handleJobAction(event); });

    const controller = {
      element: app,
      ready: null,
      setContext(context = {}, behavior = {}) {
        if (state.destroyed || behavior.ifUnedited && state.userEdited) return false;
        if (context.url !== undefined) {
          state.url = String(context.url || '');
          $('url').value = state.url;
          state.userEdited = false;
        }
        state.title = String(context.title || '');
        state.titleUrl = SG.cleanUrl(state.url);
        evaluate();
        return true;
      },
      setActive(active) {
        const next = active === true;
        if (state.destroyed || state.active === next) return;
        state.active = next;
        if (next) {
          state.caps = {};
          state.capsFlight = {};
          evaluate();
          void loadSettings();
          void refreshHistory(true);
          startPolling();
        }
        else stopPolling();
      },
      setTheme(theme) {
        state.themeOverride = ['light', 'dark', 'auto'].includes(theme) ? theme : null;
        app.dataset.theme = state.themeOverride || state.settings.theme;
      },
      showTab(tab) { setTab(tab); },
      refreshHistory,
      destroy() {
        if (state.destroyed) return;
        state.destroyed = true;
        state.notifyToken += 1;
        stopPolling();
        for (const remove of listeners) remove();
        app.remove();
        mounted.delete(container);
      }
    };
    mounted.set(container, controller);
    $('url').value = state.url;
    renderSettings();
    renderHistory();
    controller.ready = Promise.all([loadSettings(), refreshHistory(false), state.capsFlight[capsHost((SG.classify(state.url.trim()) || {}).platform)] || Promise.resolve()]);
    startPolling();
    return controller;
  }

  return Object.freeze({ mount, request, brandSvg: BRAND });
})();
