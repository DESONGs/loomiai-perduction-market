import { decisionLabel, esc, fmt } from './utils.js';

export function createLiveStreamModule({ state, api, charts, detail }) {
  let eventSource = null;
  let reconnectTimer = null;
  let activeRunId = '';
  let hasContent = false;

  function stop() {
    activeRunId = '';
    hasContent = false;
    if (reconnectTimer) {
      window.clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
    if (eventSource) {
      eventSource.close();
      eventSource = null;
    }
    detail.resetStreamPanel();
  }

  function renderLiveUpdate(event) {
    const { body, count, dot } = detail.streamElements;
    if (event.type === 'reset') {
      body.innerHTML = '';
      hasContent = false;
      return;
    }
    if (event.type === 'start') {
      hasContent = false;
      body.innerHTML = '<div class="empty">实验已启动，等待增量事件...</div>';
      charts.reset();
      detail.showParams(event.params || null);
      detail.applyTokenSnapshot({
        status: 'running',
        total_tokens: 0,
        prompt_tokens: 0,
        completion_tokens: 0,
        api_calls: 0,
        api_errors: 0,
        progress: '',
        budget_limit: event.total_budget_limit || event.budget_limit || 500000,
      });
      dot.textContent = '● 已连接';
      return;
    }
    if (event.type === 'finish') {
      dot.textContent = '已完成';
      detail.applyTokenSnapshot({
        status: 'finished',
        total_tokens: event.token_summary?.total_tokens || 0,
        prompt_tokens: 0,
        completion_tokens: 0,
        api_calls: event.token_summary?.api_calls || 0,
        api_errors: event.token_summary?.api_errors || 0,
        progress: count.textContent || '',
      });
      return;
    }
    if (event.type !== 'inference') {
      return;
    }

    if (!hasContent) {
      body.innerHTML = '';
      hasContent = true;
    }
    count.textContent = event.progress || '';
    detail.applyTokenSnapshot({
      status: 'running',
      total_tokens: event.cumulative_tokens || 0,
      prompt_tokens: event.prompt_tokens || 0,
      completion_tokens: event.completion_tokens || 0,
      api_calls: event.api_calls || 0,
      api_errors: event.api_errors || 0,
      progress: event.progress || '',
    });

    document.getElementById('sBankroll').textContent = `$${Math.round(event.bankroll || 10000).toLocaleString()}`;
    const pnl = event.running_pnl || 0;
    document.getElementById('sPnl').textContent = `${pnl >= 0 ? '+$' : '-$'}${Math.abs(pnl).toFixed(0)}`;
    document.getElementById('sPnl').className = `val ${pnl >= 0 ? 'v-green' : 'v-red'}`;
    document.getElementById('sPnlPct').textContent = `${((pnl / 10000) * 100).toFixed(1)}%`;
    document.getElementById('sWinRate').textContent = `${((event.win_rate || 0) * 100).toFixed(0)}%`;
    document.getElementById('sWinLoss').textContent = `${event.wins || 0}胜 / ${event.losses || 0}负`;
    if (event.final_resolution === 'accepted' || event.bet_action !== 'skip') {
      charts.addPnlPoint(event.index, event.bankroll || 10000);
    }

    const card = document.createElement('div');
    const borderClass =
      event.final_resolution === 'accepted'
        ? 'pred-correct'
        : event.final_resolution === 'failed'
          ? 'pred-wrong'
          : 'pred-skip';
    const pnlClass = (event.bet_pnl || 0) >= 0 ? 'pnl-pos' : 'pnl-neg';
    const actionLabel = { buy: '接受', skip: '拒绝', sell: '卖出' }[event.bet_action] || event.bet_action;
    card.className = `card ${borderClass}`;
    card.innerHTML = `
      <div class="card-top"><span class="card-q">${esc(event.question)}</span><span class="card-tag">${esc(event.progress || '')}</span></div>
      <div class="card-result">
        <div>决策: <span>${esc(decisionLabel(event.final_resolution || 'unknown'))}</span></div>
        <div>执行动作: <span>${esc(actionLabel)}</span></div>
        <div>阶段摘要: <span>${esc(event.thinking || '—')}</span></div>
        <div>验证盈亏: <span class="${pnlClass}">${
          event.bet_action === 'skip' ? '—' : `${event.bet_pnl >= 0 ? '+' : ''}${Number(event.bet_pnl || 0).toFixed(1)}`
        }</span></div>
      </div>
      ${event.thinking ? `<div class="card-thinking"><b>Phase 指标：</b><br>${esc(event.thinking)}</div>` : ''}
      ${event.reasoning ? `<div class="card-conclusion"><b>状态：</b> ${esc(decisionLabel(event.reasoning))}</div>` : ''}
      ${event.raw_response ? `<div class="card-raw" title="点击展开原始输出">原始输出: ${esc(event.raw_response)}</div>` : ''}
      <div class="card-footer"><span>样本总量: ${Math.round(event.volume || 0).toLocaleString()}</span><span>Token: ${fmt(
        event.call_tokens || 0,
      )} | 累计 ${fmt(event.cumulative_tokens || 0)}</span><span>Prompt / Completion: ${fmt(event.prompt_tokens || 0)} / ${fmt(
        event.completion_tokens || 0,
      )}</span></div>`;
    const raw = card.querySelector('.card-raw');
    if (raw) {
      raw.addEventListener('click', () => raw.classList.toggle('open'));
    }
    body.prepend(card);
    while (body.children.length > 120) {
      body.removeChild(body.lastChild);
    }
    dot.textContent = '● 已连接';
  }

  function scheduleReconnect(runId) {
    if (reconnectTimer) {
      window.clearTimeout(reconnectTimer);
    }
    reconnectTimer = window.setTimeout(() => {
      if (runId === state.selectedRunId) {
        start(runId);
      }
    }, 3000);
  }

  function start(runId) {
    if (!runId) {
      stop();
      return;
    }
    if (eventSource && activeRunId === runId) {
      return;
    }
    stop();
    activeRunId = runId;
    const source = api.openStream(runId);
    if (!source) {
      return;
    }
    eventSource = source;
    detail.streamElements.dot.textContent = '连接中...';
    source.onmessage = (message) => {
      try {
        const payload = JSON.parse(message.data);
        renderLiveUpdate(payload);
      } catch (_error) {
        detail.streamElements.dot.textContent = '流解析失败';
      }
    };
    source.onerror = () => {
      detail.streamElements.dot.textContent = '实时流重连中...';
      if (eventSource) {
        eventSource.close();
        eventSource = null;
      }
      scheduleReconnect(runId);
    };
  }

  return {
    start,
    stop,
  };
}
