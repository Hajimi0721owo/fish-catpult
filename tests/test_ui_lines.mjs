import assert from 'node:assert/strict';
import {pick} from '../src/bili_fav_relay/setup/ui/lines.js';

const pending = {state: null, step: -1, job: {}, busy: false, since: 0, flash: null};
assert.match(pick(pending).text, /正在读取设置/);

const message = '设置会话已失效，请从终端重新打开完整设置链接';
const failed = pick({...pending, flash: {tone: 'error', text: message}});
assert.equal(failed.text, message);
assert.equal(failed.alert, true);
assert.equal(failed.expr, 'sad');
console.log('Startup errors remain visible before configuration is loaded.');

const retryAt = Date.now() / 1000 + 600;
const at = new Date(retryAt * 1000).toLocaleTimeString('zh-CN', {hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false});
const ready = {
  ...pending, step: 4,
  state: {mode: 'auto', interval_seconds: 10, runtime: {active: true}, control: {},
    source_access: {cooling: true, error: 'favorite_changed', next_poll_at: retryAt}},
};
const changed = pick(ready);
assert.match(changed.text, /分页结果前后不一致/);
assert.ok(changed.text.includes(at));
assert.match(changed.text, /自动重试/);
assert.doesNotMatch(changed.text, /限流|B 站要歇/);
assert.equal(changed.alert, true);

const manualWaiting = {status: 'waiting', next_action_at: retryAt, message: '等待收藏夹请求冷却'};
const manual = {...ready.state, mode: 'manual', control: {sync: manualWaiting}};
assert.match(pick({...ready, state: manual}).text, /自动重试/);
const stopped = pick({...ready, state: {...manual, runtime: {active: false}}});
assert.doesNotMatch(stopped.text, /自动重试/);
assert.match(stopped.text, /启动后台/);
assert.doesNotMatch(pick({...ready, state: {...manual, control: {}}}).text, /自动重试/);

const normal = {...manual, source_access: {cooling: true, error: '', next_poll_at: retryAt}};
assert.match(pick({...ready, state: normal}).text, /收藏夹读取间隔还没到/);
const delivery = {...normal, control: {sync: {...manualWaiting, message: '等待下一条视频投递'}}};
assert.match(pick({...ready, state: delivery}).text, /视频的投递间隔/);
assert.doesNotMatch(pick({...ready, state: delivery}).text, /B 站|收藏夹读取/);

const unknown = {...ready.state, source_access: {...ready.state.source_access, error: 'secret-marker-not-in-ui'}};
assert.doesNotMatch(pick({...ready, state: unknown}).text, /secret-marker/);
assert.match(pick({...ready, state: {...ready.state, control: {source_paused: true}}}).text, /同步先暂停/);
const recovered = {...ready.state, source_access: {cooling: true, error: '', next_poll_at: retryAt}};
assert.doesNotMatch(pick({...ready, state: recovered}).text, /分页结果前后不一致/);
console.log('Source failure backoff, normal polling and video delivery waits stay distinct.');

const permission = '请到目标 QQ 群开启机器人的主动发言权限';
const paused = pick({...pending, state: {control: {delivery_paused: true, delivery_problem: {message: permission}}}, step: 4});
assert.equal(paused.text, permission);
assert.equal(paused.alert, true);
console.log('Paused delivery shows the safe permission recovery hint.');

// 停止请求已接受但进程尚未退出，不能仍许诺下一轮自动处理。
for (const state of [ready.state, manual, normal, delivery, recovered]) {
  const stopping = {...state, runtime: {active: true, status: 'stopping'}};
  const line = pick({...ready, state: stopping});
  assert.match(line.text, /后台正在停止/);
  assert.doesNotMatch(line.text, /自动重试|后台会继续处理|开工啦/);
  assert.match(line.text, /重新启动后台/);
}
const permissionAndWait = {...ready.state, runtime: {active: true, status: 'stopping'},
  control: {delivery_paused: true, delivery_problem: {message: permission}}};
assert.equal(pick({...ready, state: permissionAndWait}).text, permission);
assert.match(pick(ready).text, /自动重试/);
console.log('Stopping never promises another attempt; permission recovery keeps priority.');
