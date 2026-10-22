// 台词：按"出错 > 处理中 > 等口令 > 刚完成 > 当前步骤"挑一句。梗只用她们自己的（深度思考、喵、大白饭），都要善意。
const INTRO = [
  () => ({who: 'dsf', expr: 'happy', text: '欢迎来到后台！先告诉我要分享哪个收藏夹。以前收藏的我只记在小本本上，不会补发哦。'}),
  (s) => s.bot
    ? {who: 'hachi', expr: 'happy', text: `机器人「${s.bot.name}」已经连好啦。要换一个，就重新填一次喵。`}
    : {who: 'hachi', text: '视频要靠你自己的 QQ 机器人送进群。密钥只存在这台电脑里，连我们都看不到喵。'},
  (s) => s.binding
    ? {who: 'hachi', expr: 'happy', text: `「${s.binding.alias}」已经认识机器人了，去下一步吧喵！`}
    : {who: 'hachi', text: '来对暗号！点“生成口令”，再去群里 @机器人 把口令发出来，我会一直竖着耳朵听。'},
  (s) => s.baseline
    ? {who: 'dsf', expr: 'happy', text: `${s.seen} 个旧收藏都记好啦，一个都不会补发。选好节奏，就可以开工了！`}
    : {who: 'dsf', text: '最后一步！先让我把已有的收藏记下来——只看列表，不下载、不发视频。'},
  (s) => s.runtime.active
    ? {who: 'hachi', expr: 'happy', other: 'happy', text: '开工啦！之后往收藏夹里放新视频，我们就把它送进群里喵～'}
    : {who: 'dsf', expr: 'pout', text: '后台还没在跑哦。点“启动后台程序”，我们就上班！电脑重启后也要再点一次。'},
];

// 每一步完成时，由负责这一步的角色用自己的口气说结果。
const DONE = {
  folder: (s) => ({who: 'dsf', text: `找到${s.folder_title ? `「${s.folder_title}」` : '收藏夹'}啦！一共 ${s.folder_count ?? '?'} 个内容，下一步连接机器人。`}),
  credentials: (s) => ({who: 'hachi', text: `机器人「${s.bot?.name || 'QQ 机器人'}」验证通过！密钥已经锁进这台电脑的保险箱喵。`}),
  bind: (s) => ({who: 'hachi', text: `暗号对上了！以后视频都送去「${s.binding?.alias || '这个群'}」喵～`}),
  baseline: (s) => ({who: 'dsf', text: `${s.seen} 个旧收藏都记在小本本上了，一个都不会补发！`}),
};

function clock(value) {
  return new Date(value * 1000).toLocaleTimeString('zh-CN', {hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false});
}

function sourceFailureWait(state) {
  const access = state.source_access || {};
  if (!access.cooling || !access.error || !Number.isFinite(access.next_poll_at)) return null;
  const reasons = {
    favorite_changed: '这轮收藏夹分页结果前后不一致，暂未更新收藏记录。',
    favorite_incomplete: '这轮收藏夹没有读取完整，暂未更新收藏记录。',
    favorite_paging: '这轮收藏夹分页返回异常，暂未更新收藏记录。',
    network: '这轮未能连上 B 站，收藏夹读取没有完成。',
  };
  const sync = state.control?.sync;
  const stopping = state.runtime?.status === 'stopping';
  const willRetry = state.runtime?.active && !stopping && (state.mode === 'auto' || ['pending', 'waiting', 'running'].includes(sync?.status));
  const at = clock(access.next_poll_at);
  const next = stopping ? `后台正在停止；${at} 后如需重试，请重新启动后台并点击“立即同步”。`
    : willRetry ? `程序将在 ${at} 后自动重试。` : state.runtime?.active
    ? `${at} 后可点击“立即同步”再次读取。` : `${at} 后可再次读取；请先启动后台并点击“立即同步”。`;
  return {who: 'dsf', expr: 'surprised', alert: true,
    text: (reasons[access.error] || '这轮收藏夹读取未完成。') + next};
}

export function pick({state, step, job, busy, since, flash}) {
  if (flash?.tone === 'error') return {who: 'dsf', expr: 'sad', alert: true, text: flash.text};
  if (!state) return {who: 'dsf', text: '正在读取设置……'};
  if (job.action === 'bind' && busy && job.code) {
    return {who: 'hachi', expr: 'calm', other: 'surprised', text: `我在听！去群里 @机器人 发送「${job.code}」就好喵。`};
  }
  if (busy) {
    const seconds = Math.max(1, Math.round((Date.now() - since) / 1000));
    const doing = job.message && job.message !== '正在处理' ? job.message : '正在处理';
    return {who: 'dsf', expr: 'calm', other: 'calm', text: `${doing}……（已深度思考 ${seconds} 秒）`};
  }
  if (flash?.tone === 'ok') {
    const line = DONE[flash.action]?.(state) || {who: 'dsf', text: flash.text};
    return {expr: 'happy', other: 'happy', ...line};
  }
  if (step === 4) {
    const control = state.control || {};
    if (control.delivery_paused && control.delivery_problem?.message) {
      return {who: 'dsf', expr: 'sad', alert: true, text: control.delivery_problem.message};
    }
    if (control.source_paused || control.delivery_paused) {
      return {who: 'dsf', expr: 'sad', alert: true, text: '同步先暂停了。请让 agent 看一下 status 里的原因，处理好再恢复；没确认送到的视频不会自动重发。'};
    }
    const sourceWait = sourceFailureWait(state);
    if (sourceWait) return sourceWait;
    if (state.runtime?.status === 'stopping') {
      return {who: 'dsf', text: '后台正在停止，当前处理结束后退出。需要继续同步时，请重新启动后台。'};
    }
    if (state.runtime.last_result?.status === 'failed') {
      return {who: 'dsf', expr: 'surprised', alert: true, text: '后台在跑，但上一轮同步没成功。让 agent 看一下 status 里的具体原因吧。'};
    }
    const sync = control.sync;
    if (sync?.status === 'waiting' && sync.next_action_at) {
      const delivery = sync.message === '等待下一条视频投递';
      const reason = delivery ? '下一条视频的投递间隔还没到' : '收藏夹读取间隔还没到';
      const next = state.runtime?.active ? '后台会继续处理' : '请先启动后台继续处理';
      return {who: 'dsf', expr: 'shy', text: `${reason}，${clock(sync.next_action_at)} 后${next}。`};
    }
  }
  if (step === 3) {
    const missing = Object.entries(state.tools || {}).filter(([, ok]) => !ok).map(([name]) => name);
    if (missing.length) return {who: 'dsf', expr: 'surprised', alert: true, text: `这台电脑还缺 ${missing.join('、')}，视频没法处理。请让 agent 装一下 FFmpeg，装好这里会自己更新。`};
  }
  return (INTRO[step] || INTRO[0])(state);
}
