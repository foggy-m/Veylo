var SG = (() => {
  const VIDEO_ID = /^[A-Za-z0-9_-]{11}$/;
  const LIST_ID = /^[A-Za-z0-9_-]{1,150}$/;

  function classify(raw) {
    if (typeof raw !== 'string' || raw.length > 4096) return null;
    try {
      const u = new URL(raw.trim());
      if (!['http:', 'https:'].includes(u.protocol) || u.username || u.password || u.port) return null;
      const host = u.hostname.toLowerCase();
      const youtube = ['youtube.com', 'www.youtube.com', 'm.youtube.com', 'youtube-nocookie.com', 'www.youtube-nocookie.com'].includes(host);
      if (youtube || host === 'youtu.be' || host === 'www.youtu.be') {
        let id = null;
        if (host.endsWith('youtu.be')) id = u.pathname.match(/^\/([A-Za-z0-9_-]{11})\/?$/)?.[1];
        else if (u.pathname === '/watch') id = u.searchParams.get('v');
        else id = u.pathname.match(/^\/(?:shorts|live|embed|v)\/([A-Za-z0-9_-]{11})\/?$/)?.[1];
        if (id && VIDEO_ID.test(id)) {
          const params = new URLSearchParams({ v: id });
          const list = u.searchParams.get('list');
          if (list && LIST_ID.test(list)) params.set('list', list);
          const index = u.searchParams.get('index');
          if (params.has('list') && /^\d{1,6}$/.test(index || '')) params.set('index', index);
          return { platform: 'youtube', kind: 'video', url: `https://www.youtube.com/watch?${params}` };
        }
        if (youtube && u.pathname === '/playlist' && LIST_ID.test(u.searchParams.get('list') || '')) {
          return { platform: 'youtube', kind: 'playlist', url: 'https://www.youtube.com/playlist?list=' + u.searchParams.get('list') };
        }
        if (youtube && /^\/(?:@[^/?#]+|(?:channel|c|user)\/[^/?#]+)(?:\/(?:videos|shorts|streams))?\/?$/.test(u.pathname)) {
          return { platform: 'youtube', kind: 'channel', url: 'https://www.youtube.com' + u.pathname };
        }
      }
      if (['x.com', 'www.x.com', 'twitter.com', 'www.twitter.com', 'mobile.twitter.com'].includes(host)) {
        const post = u.pathname.match(/^\/([A-Za-z0-9_]{1,50})\/status\/(\d{1,25})(?:\/(?:video|photo)\/\d+)?\/?$/);
        const internal = u.pathname.match(/^\/i\/web\/status\/(\d{1,25})\/?$/);
        if (post) return { platform: 'x', kind: 'video', url: `https://x.com/${post[1]}/status/${post[2]}` };
        if (internal) return { platform: 'x', kind: 'video', url: `https://x.com/i/web/status/${internal[1]}` };
      }
      if (['bilibili.com', 'www.bilibili.com', 'm.bilibili.com'].includes(host)) {
        const video = u.pathname.match(/^\/video\/(BV[0-9A-Za-z]{10}|av(\d{1,12}))\/?$/i);
        if (video) {
          const bvid = /^bv/i.test(video[1]) ? 'BV' + video[1].slice(2) : null;
          const page = /^\d{1,4}$/.test(u.searchParams.get('p') || '') ? parseInt(u.searchParams.get('p'), 10) : 1;
          const query = page > 1 ? '?p=' + page : '';
          return {
            platform: 'bili', kind: 'video', page,
            url: 'https://www.bilibili.com/video/' + (bvid || ('av' + video[2])) + query
          };
        }
        const bangumi = u.pathname.match(/^\/bangumi\/play\/(ep|ss|md)(\d{1,12})\/?$/i);
        if (bangumi) {
          return {
            platform: 'bili', kind: 'bangumi',
            url: 'https://www.bilibili.com/bangumi/play/' + bangumi[1].toLowerCase() + bangumi[2]
          };
        }
        const cheese = u.pathname.match(/^\/cheese\/play\/(ep|ss)(\d{1,12})\/?$/i);
        if (cheese) {
          return {
            platform: 'bili', kind: 'cheese',
            url: 'https://www.bilibili.com/cheese/play/' + cheese[1].toLowerCase() + cheese[2]
          };
        }
      }
      return null;
    } catch (_) {
      return null;
    }
  }

  function cleanUrl(raw) {
    return classify(raw)?.url || '';
  }

  const PLATFORM = {
    youtube: { label: 'YouTube', tone: 'yt' },
    x: { label: 'X', tone: 'x' },
    bili: { label: 'B 站', tone: 'bili' }
  };
  const KIND = { video: '视频', playlist: '播放列表', channel: '频道', bangumi: '番剧', cheese: '课程' };

  function platformMeta(platform) {
    return PLATFORM[platform] || { label: '', tone: '' };
  }

  function kindLabel(kind) {
    return KIND[kind] || '';
  }

  function jobText(job) {
    return jobView(job).text;
  }

  function jobView(job) {
    if (!job) return { text: '', tone: 'idle', percent: null };
    if (job.state === 'completed') {
      return {
        text: job.downloadDir ? `下载完成 ✓ 保存到：${job.downloadDir}` : '下载完成 ✓',
        tone: 'ok', percent: 100
      };
    }
    if (job.state === 'canceled') return { text: '已取消下载', tone: 'idle', percent: null };
    if (job.state === 'failed') {
      return { text: job.error ? `下载失败：${job.error}` : '下载失败，请检查代理、登录状态或视频是否可访问', tone: 'err', percent: null };
    }
    const percent = Number.isFinite(job.percent) ? Math.round(job.percent) : null;
    if (job.state === 'queued') return {
      text: job.downloadDir ? `等待下载 · 保存到：${job.downloadDir}` : '等待下载',
      tone: 'run', percent: 0
    };
    const where = job.downloadDir ? ` · 保存到：${job.downloadDir}` : ' · 保存到：';
    return {
      text: percent === null ? `正在下载${where}` : `正在下载 ${percent}%${where}`,
      tone: 'run', percent: percent === null ? 0 : percent
    };
  }

  return { classify, cleanUrl, jobText, jobView, platformMeta, kindLabel };
})();

if (typeof module !== 'undefined' && module.exports) module.exports = { SG };
