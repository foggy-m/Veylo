// offscreen 文档：MV3 service worker 没有 DOM，剪贴板写入必须在这里执行。
// 职责单一：只把后台传来的字符串写进系统剪贴板，不读取、不渲染任何页面内容。
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg && msg.type === 'sg-clipboard-write') {
    (async () => {
      try {
        await navigator.clipboard.writeText(String(msg.text || ''));
        sendResponse({ ok: true });
      } catch (e) {
        // 退回到 textarea + execCommand，兼容部分 Chromium 分支
        let ta;
        try {
          ta = document.createElement('textarea');
          ta.value = String(msg.text || '');
          ta.style.position = 'fixed';
          ta.style.opacity = '0';
          document.body.appendChild(ta);
          ta.select();
          const ok = document.execCommand('copy');
          sendResponse({ ok: !!ok });
        } catch (e2) {
          sendResponse({ ok: false, error: String((e && e.message) || e) });
        } finally {
          if (ta?.parentNode) ta.parentNode.removeChild(ta);
        }
      }
    })();
    return true;
  }
  return false;
});
