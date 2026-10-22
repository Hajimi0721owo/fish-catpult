// 设置卡模板；平台返回的标题、昵称和进度均由 app.js 用 textContent 填入。
export const steps = ['选收藏夹', '连接机器人', '绑定 QQ 群', '开始转发'];
export const stepOf = {folder: 0, credentials: 1, bind: 2, baseline: 3, activate: 3, ready: 4};
const back = '<button type="button" class="secondary" data-action="back">上一步</button>';
const head = (n, label) => `<p class="card-head">STEP ${n} · ${label}<span>${n} / 4</span></p>`;

const interval = (id) => `<div class="interval-input"><input id="${id}" type="number" min="1" max="1440" step="1" value="15" required aria-label="同步间隔数值"><select id="${id}-unit" aria-label="同步间隔单位"><option value="1">秒</option><option value="60" selected>分钟</option></select></div>`;
const intervalHint = '最短 10 秒，最长 24 小时。短间隔更容易遇到访问限制，建议先用 15 分钟。';

const folder = `${head(1, '选收藏夹')}<div class="card-body">
  <h2 id="page-title" tabindex="-1">想分享哪个收藏夹？</h2>
  <p class="intro">选一个<strong>公开</strong>的收藏夹。之后往里面收藏的视频，会转发到 QQ 群。</p>
  <div id="saved-folder" class="success-line" hidden></div>
  <div id="source-wait" class="inset warning" hidden><p id="source-wait-text"></p><button type="button" id="resume-source" data-action="resume_source" hidden>恢复访问</button></div>
  <form id="folder-form"><label for="folder">收藏夹链接</label>
    <input id="folder" name="folder" type="text" required autocomplete="off" placeholder="粘贴链接，或手机 App 分享的整段文字">
    <details class="help"><summary>怎么复制收藏夹链接？</summary>
      <ul><li><b>电脑：</b>B 站个人空间 → 收藏 → 点开要分享的收藏夹，复制地址栏（含 <code>fid=</code>）。</li>
      <li><b>手机：</b>我的 → 收藏 → 进入收藏夹 → 右上角分享 → 复制链接，整段粘贴进来。</li>
      <li><b>设为公开：</b>在收藏夹的“编辑”里打开“公开”。私密收藏夹读不到。</li></ul></details>
    <div class="inset"><strong>只转发以后的新收藏</strong><p>已经在收藏夹里的视频只会被记下，不会补发到群里。</p></div>
    <div class="actions"><button type="submit">检查并继续</button></div>
  </form></div>`;

const bot = `${head(2, '连接机器人')}<div class="card-body">
  <h2 id="page-title" tabindex="-1">连接你的 QQ 机器人</h2>
  <p class="intro">视频由你自己的 QQ 官方机器人送到群里。已经有机器人就直接填。</p>
  <div id="saved-bot" class="success-line" hidden></div>
  <details class="help guide" id="bot-guide"><summary>第一次用？跟着这 5 步创建机器人（约 10 分钟）</summary>
    <ol><li>登录 <a href="https://q.qq.com/" target="_blank" rel="noopener noreferrer">QQ 开放平台 ↗</a>，创建一个 QQ 机器人。</li>
      <li>在机器人后台的<b>开发设置</b>里复制 <b>AppID</b>，生成 <b>AppSecret</b>（只显示一次，先存好）。</li>
      <li>同一页的 <b>IP 白名单</b>里加上这台电脑的公网 IP（浏览器搜“我的 IP”可以查到）。</li>
      <li>机器人还没上线时，到<b>沙箱配置</b>把目标 QQ 群加进去。</li>
      <li>在目标群的群设置里添加这个机器人。</li></ol>
    <p class="hint">菜单名称以平台页面为准。能否主动发视频，以绑定和第一次转发的实际结果为准。</p></details>
  <form id="credentials-form"><label for="app-id">AppID</label>
    <input id="app-id" name="app_id" inputmode="numeric" pattern="[0-9]+" required autocomplete="off" placeholder="一串数字，例如 102xxxxxx">
    <label for="secret">AppSecret</label>
    <input id="secret" name="secret" type="password" required autocomplete="new-password" maxlength="512" placeholder="粘贴密钥，保存后不再显示">
    <p class="hint">🔒 密钥只存进这台电脑的系统凭据库，不用发给 agent 或任何人。系统可能会弹窗请你允许。</p>
    <div class="actions">${back}<button type="button" id="skip-credentials" class="secondary" data-action="next" hidden>沿用已保存的</button><button type="submit">验证并保存</button></div>
  </form></div>`;

const group = `${head(3, '绑定 QQ 群')}<div class="card-body">
  <h2 id="page-title" tabindex="-1">在群里对个暗号</h2>
  <p class="intro">点生成口令，然后在目标群里 @机器人 发送它。收到后程序就知道该发到哪个群。</p>
  <div id="bound-group" class="success-line" hidden></div>
  <form id="bind-form"><label for="alias">给这个群起个备注</label><input id="alias" name="alias" maxlength="60" required value="视频分享群" autocomplete="off">
    <p class="hint">只用来在这里辨认，不会改群名。</p>
    <div id="binding-code" class="code-box" hidden>
      <ol class="code-steps"><li>打开目标 QQ 群</li><li>输入 @ 选中机器人</li><li>发送下面这句</li></ol>
      <strong id="code-text"></strong>
      <p><span id="code-expiry"></span> · 收到后自动进入下一步</p>
      <button type="button" class="secondary" data-action="copy-code">复制口令</button>
    </div>
    <p class="hint">绑定不会往群里发任何消息或视频。</p>
    <div class="actions">${back}<button type="submit" id="bind-button">生成口令</button><button type="button" id="skip-binding" data-action="next" hidden>下一步</button></div>
  </form></div>`;

const activate = `${head(4, '开始转发')}<div class="card-body">
  <h2 id="page-title" tabindex="-1">最后，选个同步节奏</h2>
  <p class="intro route-summary"></p>
  <div class="baseline-row"><span class="check" id="baseline-dot"></span><div><strong id="baseline-title">先记下已有的收藏</strong><p id="baseline-hint">只读取列表，不下载、不发视频。</p></div><button type="button" id="baseline-button" data-action="baseline">记下已有收藏</button></div>
  <div id="dependency-warning" class="inset warning" hidden></div>
  <div id="source-wait" class="inset warning" hidden><p id="source-wait-text"></p><button type="button" id="resume-source" data-action="resume_source" hidden>恢复访问</button></div>
  <form id="activate-form"><fieldset class="mode-cards"><legend>同步方式</legend>
      <label class="mode-card"><input type="radio" name="mode" value="manual" checked><span><strong>手动同步</strong><small>点“立即同步”或对 agent 说一声，才去看新收藏。适合先试试。</small></span></label>
      <label class="mode-card"><input type="radio" name="mode" value="auto"><span><strong>自动同步</strong><small>后台定时看一眼，有新收藏就转发。</small></span></label></fieldset>
    <div id="interval-field"><label for="interval">多久看一次</label>${interval('interval')}<p class="hint">${intervalHint}</p></div>
    <p class="hint">点下面的按钮后，新收藏才会真的发往「<span class="group-name"></span>」。关掉页面也会继续运行；电脑重启后需要再启动一次。</p>
    <div class="actions">${back}<button type="submit" id="activate-button" disabled>开始转发</button></div>
  </form></div>`;

const ready = `<p class="card-head">后台 · 转发中控台<span>设置完成</span></p><div class="card-body">
  <div class="status-line"><span class="pill" id="runtime-pill"><i></i><span id="runtime-value"></span></span></div>
  <h2 id="page-title" tabindex="-1">设置完成</h2>
  <p class="intro route-summary"></p>
  <div class="stats"><div><b id="sent-count">0</b><small>已转发</small></div><div><b id="queued-count">0</b><small>等待发送</small></div><div><b id="seen-count">0</b><small>历史收藏</small></div></div>
  <dl class="summary"><div><dt>同步方式</dt><dd id="mode-value"></dd></div><div><dt>本次进度</dt><dd id="sync-value">暂无同步任务</dd></div></dl>
  <div class="actions dashboard-actions"><button type="button" data-action="sync" id="sync-button">立即同步</button><button type="button" data-action="start" id="start-button" hidden>启动后台程序</button><button type="button" class="text-button" data-action="cancel" id="cancel-button" hidden>取消本次</button><button type="button" class="text-button quiet" data-action="stop" id="stop-button">停止后台程序</button></div>
  <form id="schedule-form" class="schedule-form"><label for="schedule-mode">修改同步方式</label><div class="inline-fields"><select id="schedule-mode"><option value="manual">手动同步</option><option value="auto">自动同步</option></select><span class="every">每</span>${interval('schedule-interval')}<button type="submit" class="secondary">保存</button></div><p id="schedule-hint" class="hint">${intervalHint}</p></form>
  <div class="tips"><div><strong>验证一下</strong><p>往收藏夹里新收藏一个短视频，再点“立即同步”。两轮读取至少隔 10 秒，遇到访问限制时会暂停。</p></div>
    <div><strong>也可以直接对 agent 说</strong><p>“现在同步新收藏”<br>“改成每 10 分钟同步”<br>“电脑重启了，启动收藏转发”</p></div></div></div>`;

export const views = [folder, bot, group, activate, ready];
