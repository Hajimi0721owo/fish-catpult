import {steps, stepOf, views} from './views.js';
import {say} from './cast.js';
import {pick} from './lines.js';

const $ = (id) => document.getElementById(id);
const all = (selector) => document.querySelectorAll(selector);
const fragment = new URLSearchParams(location.hash.slice(1));
const initialToken = fragment.get('token');
if (initialToken) {
  sessionStorage.setItem('setup-token', initialToken);
  history.replaceState(null, '', '/');
}
const token = sessionStorage.getItem('setup-token') || '';
// 地址栏只补上 #token 时不会重跑模块；重载后沿用启动解析与服务端鉴权。
window.addEventListener('hashchange', () => {
  if (new URLSearchParams(location.hash.slice(1)).get('token')) location.reload();
});
let state = null, current = -1, handledJob = '', busy = false, submitting = false, since = 0, flash = null;
function text(id, value) { if ($(id)) $(id).textContent = value; }
function show(id, visible) { if ($(id)) $(id).hidden = !visible; }
/** 出错 / 完成的提示：角色说出来；窄屏时卡片里也留一条，免得滚动后看不到。 */
function notice(message, tone = 'error', action) {
  flash = message ? {text: message, tone, action, at: Date.now()} : null;
  text('notice', message); show('notice', !!message); $('notice').className = tone;
  if (!state && message && tone === 'error') {
    const heading = document.createElement('h2'); heading.id = 'page-title'; heading.textContent = '无法读取设置';
    const help = document.createElement('p');
    help.textContent = '请确认设置程序仍在运行，并使用启动时提供的完整链接重新打开此页面。';
    $('content').replaceChildren(heading, help);
  }
  speak();
}
function clock(value) { return new Date(value * 1000).toLocaleTimeString('zh-CN', {hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false}); }
function intervalLabel() {
  const seconds = state.interval_seconds;
  return seconds % 60 === 0 ? `${seconds / 60} 分钟` : `${seconds} 秒`;
}
function intervalLimits(id, enabled = true) {
  const input = $(id), unit = $(`${id}-unit`);
  input.disabled = unit.disabled = !enabled;
  input.min = unit.value === '60' ? 1 : 10;
  input.max = unit.value === '60' ? 1440 : 86400;
}
function setIntervalInput(id) {
  const minutes = state.interval_seconds % 60 === 0;
  $(`${id}-unit`).value = minutes ? '60' : '1';
  $(id).value = state.interval_seconds / (minutes ? 60 : 1);
  intervalLimits(id);
}
function intervalPayload(id, mode) {
  return mode === 'auto' ? {seconds: Number($(id).value) * Number($(`${id}-unit`).value)} : {};
}
function folderName() {
  if (!state.folder) return '';
  const title = state.folder_title ? `「${state.folder_title}」` : `收藏夹 ${state.folder}`;
  return Number.isInteger(state.folder_count) ? `${title} · ${state.folder_count} 个内容` : title;
}
async function api(path, body) {
  const response = await fetch(path, {method: body ? 'POST' : 'GET', cache: 'no-store',
    headers: {'X-Setup-Token': token, ...(body ? {'Content-Type': 'application/json'} : {})},
    ...(body ? {body: JSON.stringify(body)} : {})});
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || '操作未完成，请重试');
  return result;
}
function speak() {
  if (flash?.tone === 'ok' && Date.now() - flash.at > 7000) flash = null;
  say(pick({state, step: current, job: state?.job || {}, busy, since, flash}));
}
function details() {
  return [folderName() || '还没选', state.bot?.name || '还没连接', state.binding?.alias || '还没绑定',
    state.send_enabled ? (state.mode === 'manual' ? '手动同步' : `每 ${intervalLabel()}`) : '还没开始'];
}
function renderSteps() {
  const active = Math.min(current, 3), done = stepOf[state.next_step], info = details();
  $('steps').replaceChildren(...steps.map((label, i) => {
    const button = document.createElement('button');
    button.type = 'button'; button.dataset.step = i;
    const finished = i < done || (i === 3 && current === 4);
    button.className = [i === active ? 'selected' : '', finished ? 'done' : ''].join(' ').trim();
    if (i === active) button.setAttribute('aria-current', 'step');
    button.setAttribute('aria-label', `第 ${i + 1} 步：${label}${finished ? '（已完成）' : ''}`);
    const box = document.createElement('span'); box.className = 'box';
    const name = document.createElement('b'); name.textContent = label;
    const small = document.createElement('small'); small.textContent = info[i];
    button.append(box, name, small);
    return button;
  }));
  text('hud-progress', `进度 ${Math.min(done, 4)}/4`);
  const live = done >= 4 && state.runtime.active;
  const paused = state.control?.source_paused || state.control?.delivery_paused;
  text('hud-live', live ? (paused ? '转发已暂停' : 'LIVE · 转发中') : done >= 4 ? '已下播' : '设置中');
  $('hud-live').parentElement.classList.toggle('off', done >= 4 && (!live || paused));
}
function navigate(step, focus = true) {
  current = step;
  $('content').innerHTML = views[step];
  if (step === 0) $('folder').value = state.folder || '';
  if (step === 1 && !state.bot) $('bot-guide').open = true;
  if (step === 3) setIntervalInput('interval');
  if (step === 4) {
    $('schedule-mode').value = state.mode;
    setIntervalInput('schedule-interval');
  }
  update();
  if (focus) $('page-title').focus({preventScroll: matchMedia('(min-width: 981px)').matches});
}
function update() {
  if (!state) return;
  const job = state.job || {};
  const wasBusy = busy;
  busy = submitting || job.phase === 'working';
  if (busy && !wasBusy) since = Date.now();
  all('form button[type=submit]').forEach(button => { button.disabled = busy; });
  renderSteps();
  all('[data-step]').forEach(button => { button.disabled = Number(button.dataset.step) > Math.min(stepOf[state.next_step], 3) || busy; });
  all('.route-summary').forEach(node => { node.textContent = `${folderName()} → ${state.binding?.alias || '尚未绑定群'}`; });
  all('.group-name').forEach(node => { node.textContent = state.binding?.alias || '已绑定的群'; });
  [updateFolder, updateBot, updateGroup, updateActivate, updateReady][current]?.(job);
  updateAccess();
  speak();
}
function updateAccess() {
  if (!$('source-wait')) return;
  const access = state.source_access || {}, relevant = !state.baseline;
  show('source-wait', relevant && (access.cooling || access.paused));
  const wait = access.cooling ? `下次可读取时间：${clock(access.next_poll_at)}。` : '';
  text('source-wait-text', access.paused ? `收藏夹访问已暂停。${wait}确认原因已处理后，点击恢复访问。` : `刚刚读取过收藏夹，请稍等。${wait}`);
  show('resume-source', relevant && access.paused);
  if ($('resume-source')) $('resume-source').disabled = busy || access.cooling;
  if (current === 0 && relevant) $('folder-form').querySelector('button[type=submit]').disabled = busy || access.cooling;
  if (current === 3 && relevant) $('baseline-button').disabled = busy || access.cooling || access.paused;
}
function updateFolder() {
  show('saved-folder', !!state.folder);
  text('saved-folder', `已确认 ${folderName()}`);
}
function updateBot() {
  show('saved-bot', !!state.bot); show('skip-credentials', !!state.bot);
  text('saved-bot', `${state.bot?.name || ''} · ${state.bot?.source || ''}${state.bot?.verified ? ' · 身份已验证' : ''}`);
}
function updateGroup(job) {
  const waiting = job.action === 'bind' && busy && !!job.code;
  show('bound-group', !!state.binding); text('bound-group', `已绑定「${state.binding?.alias || ''}」`);
  show('skip-binding', !!state.binding); show('bind-button', !state.binding);
  text('bind-button', waiting ? '等待群里的口令…' : job.action === 'bind' && job.phase === 'failed' ? '重新生成口令' : '生成口令');
  show('binding-code', waiting);
  text('code-text', job.code || '');
  const left = Math.max(0, Math.round((job.expires_at || 0) - Date.now() / 1000));
  text('code-expiry', left ? `${Math.floor(left / 60)}:${String(left % 60).padStart(2, '0')} 后过期` : '口令已过期');
}
function updateActivate() {
  const {baseline, seen} = state;
  text('baseline-title', baseline ? `已记下 ${seen} 个已有收藏` : '先记下已有的收藏');
  text('baseline-hint', baseline ? '这些不会补发，之后的新收藏才转发。' : '只读取列表，不下载、不发视频。');
  $('baseline-dot').className = baseline ? 'check done' : 'check';
  show('baseline-button', !baseline);
  $('baseline-button').disabled = busy;
  const missing = Object.entries(state.tools).filter(([, ready]) => !ready).map(([name]) => name);
  show('dependency-warning', !!missing.length);
  text('dependency-warning', `这台电脑还缺 ${missing.join('、')}。请让 agent 安装 FFmpeg，装好后这里会自动更新。`);
  const auto = document.querySelector('input[name=mode]:checked')?.value === 'auto';
  show('interval-field', auto); intervalLimits('interval', auto);
  $('activate-button').disabled = busy || !baseline || !state.binding || !state.bot || !!missing.length;
}
function updateReady() {
  const running = state.runtime.active, stopping = state.runtime.status === 'stopping';
  text('runtime-value', stopping ? '正在停止…' : running ? '后台运行中' : '后台已停止');
  $('runtime-pill').className = `pill ${stopping ? 'pending' : running ? 'on' : 'off'}`;
  const paused = state.control?.source_paused || state.control?.delivery_paused;
  text('page-title', running ? (paused ? '设置完成，转发已暂停' : '设置完成，正在为你转发') : '设置完成，后台还没运行');
  text('mode-value', state.mode === 'manual' ? '手动同步' : `每 ${intervalLabel()}自动同步`);
  const problem = state.control?.delivery_problem;
  show('delivery-warning', !!problem?.message); text('delivery-reason', problem?.message || '');
  const counts = state.counts || {};
  text('sent-count', counts.sent || 0); text('queued-count', counts.queued || 0); text('seen-count', state.seen || 0);
  const sync = state.control?.sync;
  const labels = {pending: '已排队', running: '正在同步', waiting: '等待冷却', completed: '本次已完成', cancelled: '本次已取消', blocked: '需要处理', failed: '同步未完成'};
  text('sync-value', sync ? `${labels[sync.status] || sync.status}${sync.next_action_at ? ` · ${clock(sync.next_action_at)} 后继续` : ''} · 已发 ${sync.counts?.sent || 0}/${sync.total || 0}` : '暂无同步任务');
  show('start-button', !running); show('stop-button', running && !stopping); show('sync-button', running);
  show('cancel-button', sync && ['pending', 'running', 'waiting'].includes(sync.status));
  $('sync-button').disabled = busy || !running || state.control?.source_paused || state.control?.delivery_paused;
  const auto = $('schedule-mode').value === 'auto';
  all('.inline-fields .every, .inline-fields .interval-input, #schedule-hint').forEach(node => { node.hidden = !auto; });
  intervalLimits('schedule-interval', auto);
}
async function refresh() {
  try {
    state = await api('/api/status');
    const job = state.job || {};
    if (current < 0) { notice(''); navigate(stepOf[state.next_step], false); }
    if (['done', 'failed'].includes(job.phase) && job.id !== handledJob) {
      handledJob = job.id;
      if (job.phase === 'failed') notice(job.message);
      else {
        const next = job.result?.next_step;
        if (next && stepOf[next] !== current) navigate(stepOf[next]);
        if (job.result?.status === 'cooldown') notice(`B 站读取冷却中，请在 ${clock(job.result.next_poll_at)} 后再点一次。`);
        else notice(['folder', 'credentials', 'bind', 'baseline', 'resume_source'].includes(job.action) ? job.message : '', 'ok', job.action);
      }
    }
    update();
  } catch (error) { notice(error.message); }
}
async function submit(action, payload = {}) {
  if (busy) return;
  notice(''); submitting = true; update();
  try {
    await api('/api/action', {action, payload});
    if ($('secret')) $('secret').value = '';
  } catch (error) { notice(error.message); }
  finally { submitting = false; await refresh(); }
}
document.addEventListener('submit', (event) => {
  event.preventDefault();
  const id = event.target.id;
  if (id === 'folder-form') submit('folder', {folder: $('folder').value});
  if (id === 'credentials-form') submit('credentials', {app_id: $('app-id').value, secret: $('secret').value});
  if (id === 'bind-form') submit('bind', {alias: $('alias').value});
  if (id === 'activate-form') {
    const mode = new FormData(event.target).get('mode');
    submit('activate', {mode, ...intervalPayload('interval', mode)});
  }
  if (id === 'schedule-form') {
    const mode = $('schedule-mode').value;
    submit('control', {operation: 'schedule', mode, ...intervalPayload('schedule-interval', mode)});
  }
});
document.addEventListener('change', (event) => {
  if (event.target.name === 'mode' || event.target.id === 'schedule-mode' || event.target.id.endsWith('-unit')) update();
});
document.addEventListener('click', async (event) => {
  const button = event.target.closest('button');
  if (!button || button.disabled) return;
  if (button.dataset.step !== undefined) { notice(''); return navigate(Number(button.dataset.step)); }
  const action = button.dataset.action;
  if (action === 'back') { notice(''); return navigate(Math.max(0, current - 1)); }
  if (action === 'next') { notice(''); return navigate(current + 1); }
  if (action === 'baseline') return submit('baseline');
  if (action === 'resume_source') return submit('resume_source');
  if (['sync', 'cancel', 'start', 'stop'].includes(action)) return submit('control', {operation: action});
  if (action === 'copy-code') {
    try { await navigator.clipboard.writeText($('code-text').textContent); button.textContent = '已复制'; }
    catch { notice('复制没有成功，请手动选中口令复制。'); }
  }
});
async function poll() { await refresh(); setTimeout(poll, 2000); }
setInterval(() => { if (state && (busy || current === 2)) update(); }, 1000);
poll();
