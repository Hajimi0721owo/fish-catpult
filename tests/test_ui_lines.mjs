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
