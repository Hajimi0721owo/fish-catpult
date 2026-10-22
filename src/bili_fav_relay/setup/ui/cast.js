// 两位主持：哈基米（运营）和大肥鱼。像素图来自 ds-pixel 大档原图，只换表情片，不重画。
const NAMES = {hachi: '哈基米', dsf: '大肥鱼'};
const EXPRS = {hachi: ['calm', 'blink', 'happy'], dsf: ['calm', 'blink', 'happy', 'surprised', 'sad', 'shy', 'pout']};
const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
const actor = (who) => document.getElementById(`actor-${who}`);
const faces = {hachi: 'calm', dsf: 'calm'};
let line = '', typing = 0;

for (const [who, list] of Object.entries(EXPRS)) list.forEach(e => { new Image().src = `/art/${who}-${e}.png`; });

function face(who, expr) {
  const name = EXPRS[who].includes(expr) ? expr : 'calm';
  faces[who] = name;
  const img = actor(who);
  if (img && !img.src.endsWith(`/${who}-${name}.png`)) img.src = `/art/${who}-${name}.png`;
}

// 只在平静表情时眨眼；两人错开，节奏不规则一点更像活的。
function blinkLoop(who, delay) {
  setTimeout(() => {
    if (faces[who] === 'calm' && !document.hidden) {
      actor(who).src = `/art/${who}-blink.png`;
      setTimeout(() => { if (faces[who] === 'calm') actor(who).src = `/art/${who}-calm.png`; }, 140);
    }
    blinkLoop(who, 2400 + Math.random() * 2600);
  }, delay);
}
blinkLoop('hachi', 1800);
blinkLoop('dsf', 3100);

/** 让一位角色说一句话：{who, expr, text, other, alert}；同一句不重复打字。 */
export function say({who = 'dsf', expr = 'calm', text = '', other = 'calm', alert = false}) {
  const partner = who === 'dsf' ? 'hachi' : 'dsf';
  face(who, expr); face(partner, other);
  actor(who).classList.remove('quiet'); actor(partner).classList.add('quiet');
  const box = document.getElementById('dialog');
  box.classList.toggle('dsf', who === 'dsf');
  box.classList.toggle('alert', alert);
  document.getElementById('dialog-name').textContent = NAMES[who];
  const key = `${who}|${text}`;
  if (key === line) return;
  const sameSpeakerUpdate = line.startsWith(`${who}|`) && text.startsWith(line.slice(who.length + 1, who.length + 7));
  line = key;
  document.getElementById('dialog-live').textContent = `${NAMES[who]}：${text}`;
  type(who, text, sameSpeakerUpdate);
}

function type(who, text, instant) {
  const out = document.getElementById('dialog-text'), box = document.getElementById('dialog');
  const run = ++typing;
  if (reduced || instant) { out.textContent = text; box.classList.remove('typing'); return; }
  let i = 0;
  box.classList.add('typing'); actor(who).classList.add('talking');
  const step = () => {
    if (run !== typing) return actor(who).classList.remove('talking');
    i = Math.min(text.length, i + 2);
    out.textContent = text.slice(0, i);
    if (i < text.length) setTimeout(step, 28);
    else { box.classList.remove('typing'); actor(who).classList.remove('talking'); }
  };
  document.querySelectorAll('.actor').forEach(node => node.classList.remove('talking'));
  step();
}
