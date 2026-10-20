(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const controls = ['platform', 'theme', 'jobs', 'directory', 'permission', 'engine', 'hosts'];
  const frames = [$('demo-popup'), $('demo-content')];
  async function update() {
    const config = Object.fromEntries(controls.map(key => [key, $(`demo-${key}`).value]));
    for (const frame of frames) {
      const preview = frame.contentWindow.VeyloDemoFrame;
      if (preview) await preview.update(config);
    }
    const metrics = frames[0].contentWindow.VeyloDemoFrame?.measure();
    if (metrics) $('demo-metrics').textContent = `${metrics.width} × ${metrics.height} CSS px · ${metrics.documentScroll ? '预览文档有滚动' : '预览文档无滚动'}${metrics.workspaceScroll ? ' · 内容区可滚动' : ''} · 所有数据均为合成`;
  }
  for (const key of controls) $(`demo-${key}`).addEventListener('change', () => { void update(); });
  window.addEventListener('message', event => {
    if (event.origin !== location.origin || event.data?.type !== 'veylo-demo-ready') return;
    void update();
  });
  $('demo-tests').addEventListener('click', async () => {
    const frame = frames[0].contentWindow;
    if (!frame.SGUi || !frame.VeyloDemoFrame) return;
    $('demo-tests').disabled = true;
    const lines = [];
    $('demo-results').textContent = '正在运行合成 DOM 自测…';
    try {
      const result = await VeyloDOMTests.run(frame, line => { lines.push(line); $('demo-results').textContent = lines.join('\n'); });
      lines.push(`\n${result.passed} passed / ${result.failed} failed`);
      const metrics = frames[0].contentWindow.VeyloDemoFrame.measure();
      lines.push(`真实布局：${metrics.width} × ${metrics.height} CSS px`);
      const shadowMetrics = frames[1].contentWindow.VeyloDemoFrame.measure();
      lines.push(`Shadow CSS 隔离：${shadowMetrics.shadow && !shadowMetrics.buttonFont.includes('Times New Roman') ? 'PASS' : 'FAIL'}；按钮字体 ${shadowMetrics.buttonFont}`);
      $('demo-results').textContent = lines.join('\n');
    } catch (error) { lines.push(`FAIL ${error.message}`); $('demo-results').textContent = lines.join('\n'); }
    finally { $('demo-tests').disabled = false; }
  });
})();
