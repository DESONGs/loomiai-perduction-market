import {
  decisionLabel,
  esc,
  fmt,
  fmtBytes,
  fmtCount,
  fmtDate,
  fmtMaybe,
  formatObjectInline,
  statusColorClass,
  summarizeConfig,
} from './utils.js';

function renderList(items) {
  if (!items || !items.length) {
    return '<li>—</li>';
  }
  return items.map((item) => `<li>${esc(item)}</li>`).join('');
}

function latestAccepted(items) {
  return (items || []).find((item) => item.status === 'accepted');
}

function latestNonBaseline(items) {
  return (items || []).find((item) => (item.iteration || 0) > 0);
}

function phase(item, name) {
  return ((item || {}).phase_results || {})[name] || null;
}

function gatePath(item) {
  if (!item) {
    return '—';
  }
  const phases = item.phase_results || {};
  const order = ['search', 'validation', 'holdout'].filter((name) => phases[name]).join(' -> ');
  return order || '—';
}

function sortedBreakdownEntries(obj, kind) {
  const entries = Object.entries(obj || {});
  if (kind === 'liquidity') {
    const rank = { high: 0, mid: 1, low: 2 };
    return entries.sort((a, b) => (rank[a[0]] ?? 99) - (rank[b[0]] ?? 99) || a[0].localeCompare(b[0]));
  }
  return entries.sort((a, b) => {
    const markets = (b[1]?.num_markets || 0) - (a[1]?.num_markets || 0);
    if (markets !== 0) return markets;
    const pnl = (b[1]?.total_pnl || 0) - (a[1]?.total_pnl || 0);
    if (pnl !== 0) return pnl;
    return String(a[0]).localeCompare(String(b[0]));
  });
}

function renderBreakdownBox(title, breakdown, kind) {
  const rows = sortedBreakdownEntries(breakdown, kind);
  if (!rows.length) {
    return `<div class="breakdown-box"><h6>${esc(title)}</h6><div class="breakdown-empty">暂无细分归因数据。</div></div>`;
  }
  return `
    <div class="breakdown-box">
      <h6>${esc(title)}</h6>
      <div class="breakdown-table">
        <div class="breakdown-row header">
          <div>Bucket</div>
          <div>Markets</div>
          <div>Trades</div>
          <div>Acc</div>
          <div>PnL</div>
        </div>
        ${rows
          .map(
            ([key, value]) => `
            <div class="breakdown-row">
              <div class="breakdown-key">${esc(key)}</div>
              <div>${esc(fmtCount(value?.num_markets))}</div>
              <div>${esc(fmtCount(value?.num_trades))}</div>
              <div>${esc(fmtMaybe(value?.accuracy, 4))}</div>
              <div>${esc(fmtMaybe(value?.total_pnl, 2))}</div>
            </div>`,
          )
          .join('')}
      </div>
    </div>`;
}

function renderPhaseResults(item) {
  const phases = item.phase_results || {};
  const order = ['search', 'validation', 'holdout'];
  const cards = order
    .filter((name) => phases[name])
    .map((name) => {
      const phaseData = phases[name];
      return `
        <div class="phase-card">
          <h6>${esc(name)}</h6>
          <div class="phase-row"><div>Fitness Median</div><span>${esc(fmtMaybe(phaseData.fitness_median, 4))}</span></div>
          <div class="phase-row"><div>Fitness Std</div><span>${esc(fmtMaybe(phaseData.fitness_std, 4))}</span></div>
          <div class="phase-row"><div>Search Rank</div><span>${esc(fmtMaybe(phaseData.search_rank_score, 4))}</span></div>
          <div class="phase-row"><div>PnL Mean</div><span>${esc(fmtMaybe(phaseData.total_pnl_mean, 2))}</span></div>
          <div class="phase-row"><div>Max Drawdown</div><span>${esc(fmtMaybe(phaseData.max_drawdown_max, 4))}</span></div>
          <div class="phase-row"><div>Trades Mean</div><span>${esc(fmtMaybe(phaseData.num_trades_mean, 2))}</span></div>
          <div class="phase-row"><div>Tokens</div><span>${esc(fmtCount(phaseData.total_tokens))}</span></div>
          <div class="phase-row"><div>Prompt / Completion</div><span>${esc(fmtCount(phaseData.prompt_tokens))} / ${esc(fmtCount(phaseData.completion_tokens))}</span></div>
          <div class="phase-row"><div>Pool / Sample / Repeats</div><span>${esc(String(phaseData.pool_size || 0))} / ${esc(
            String(phaseData.sample_size || 0),
          )} / ${esc(String(phaseData.repeats || 0))}</span></div>
        </div>`;
    });
  return cards.length ? `<div class="phase-grid">${cards.join('')}</div>` : '<div class="iter-note">暂无 phase 评估结果。</div>';
}

function renderPhaseBreakdowns(item) {
  const phases = item.phase_results || {};
  const blocks = ['validation', 'search', 'holdout']
    .filter((name) => phases[name])
    .map((name) => {
      const phaseData = phases[name] || {};
      const category = phaseData.category_breakdown || {};
      const liquidity = phaseData.liquidity_breakdown || {};
      if (!Object.keys(category).length && !Object.keys(liquidity).length) {
        return '';
      }
      return `
        <div class="breakdown-phase">
          <h6>${esc(name)} Breakdown</h6>
          <div class="breakdown-grid">
            ${renderBreakdownBox('Category', category, 'category')}
            ${renderBreakdownBox('Liquidity', liquidity, 'liquidity')}
          </div>
        </div>`;
    })
    .filter(Boolean);
  return blocks.length
    ? `<div class="iter-block"><h5>Phase 归因</h5>${blocks.join('')}</div>`
    : '';
}

function renderConfigDiff(items) {
  if (!items || !items.length) {
    return '<div class="iter-note">本轮没有参数变更。</div>';
  }
  return `<div class="iter-change-list">${items
    .map(
      (item) =>
        `<div class="iter-change"><b>${esc(item.field)}</b><br>Before: ${esc(String(item.before))}<br>After: ${esc(
          String(item.after),
        )}</div>`,
    )
    .join('')}</div>`;
}

function renderPromptChange(item) {
  const prompt = item.prompt_change || {};
  const beforeFactors = (prompt.before_factors || []).join(', ') || 'baseline';
  const afterFactors = (prompt.after_factors || []).join(', ') || 'baseline';
  return `
    <div class="iter-block">
      <h5>提示词调整</h5>
      <div class="iter-note">${esc(prompt.summary || 'No prompt changes in this iteration.')}</div>
      <div class="iter-note" style="margin-top:6px">${esc(
        prompt.details || '当前实验默认只调整策略参数，不修改 system/user prompt。',
      )}</div>
      <div class="chip-row">
        <div class="chip">Before Factors: ${esc(beforeFactors)}</div>
        <div class="chip">After Factors: ${esc(afterFactors)}</div>
      </div>
      <div class="prompt-grid">
        <div class="prompt-box"><div class="iter-label">System Prompt Before</div><pre>${esc(prompt.before_system || '—')}</pre></div>
        <div class="prompt-box"><div class="iter-label">System Prompt After</div><pre>${esc(prompt.after_system || '—')}</pre></div>
        <div class="prompt-box"><div class="iter-label">User Prompt Before</div><pre>${esc(prompt.before_user || '—')}</pre></div>
        <div class="prompt-box"><div class="iter-label">User Prompt After</div><pre>${esc(prompt.after_user || '—')}</pre></div>
      </div>
    </div>`;
}

function renderReadableReasoning(text) {
  if (!text) {
    return '<div class="iter-note">暂无调整依据说明。</div>';
  }
  const parts = text
    .split('\n')
    .map((item) => item.trim())
    .filter(Boolean);
  return `<div class="iter-paragraphs">${parts.map((part) => `<div class="iter-paragraph">${esc(part)}</div>`).join('')}</div>`;
}

function renderSearchShortlist(item) {
  const rows = item.search_shortlist || [];
  if (!rows.length) {
    return '';
  }
  return `
    <div class="iter-block">
      <h5>Search Shortlist</h5>
      <div class="iter-change-list">
        ${rows
          .map(
            (row) =>
              `<div class="iter-change"><b>Slot ${esc(String(row.candidate_slot || '?'))}</b><br>Status: ${esc(
                decisionLabel(row.status || 'unknown'),
              )}<br>Axis: ${esc(row.change_axis || 'unknown')} | Intent: ${esc(row.search_intent || 'unknown')} | Step: ${esc(
                row.step_size || 'n/a',
              )}<br>Rank Score: ${esc(fmtMaybe(row.search_rank_score, 4))}<br>Config: ${esc(
                summarizeConfig(row.candidate_config || {}),
              )}</div>`,
          )
          .join('')}
      </div>
    </div>`;
}

export function createRunDetailModule({ state, api, charts }) {
  const taskDetailPanel = document.getElementById('taskDetailPanel');
  const artifactPanel = document.getElementById('artifactPanel');
  const paramsPanel = document.getElementById('paramsPanel');
  const orchestratorPanel = document.getElementById('orchestratorPanel');
  const resultsPanel = document.getElementById('expList');
  const comparisonPanel = document.getElementById('comparisonPanel');
  const iterationPanel = document.getElementById('iterationPanel');
  const iterationSummaryBar = document.getElementById('iterationSummaryBar');
  const streamBody = document.getElementById('streamBody');
  const streamCount = document.getElementById('streamCount');
  const streamDot = document.getElementById('streamDot');

  function resetStreamPanel() {
    streamBody.innerHTML = '<div class="empty">等待实验启动...</div>';
    streamCount.textContent = '';
    streamDot.textContent = '';
  }

  function renderTaskDetail() {
    const item = state.selectedRunMeta;
    if (!item) {
      taskDetailPanel.innerHTML = '<div class="empty">等待任务选择...</div>';
      return;
    }
    const summary = item.summary || {};
    const constraints = item.constraints || {};
    const runtime = item.runtime || {};
    const retentionPolicy = item.retention_policy || {};
    const summaryBox = state.latestRunSummary
      ? `
      <div class="summary-box">
        <h5>Agent Summary</h5>
        <div>${esc(state.latestRunSummary.headline || '—')}</div>
        <div>Best Result: ${esc(state.latestRunSummary.best_result?.commit || '—')} / ${esc(
          decisionLabel(state.latestRunSummary.best_result?.status || 'unknown'),
        )}</div>
        <div>Latest Iteration: #${esc(String(state.latestRunSummary.latest_iteration?.iteration ?? '—'))} ${esc(
          state.latestRunSummary.latest_iteration?.patch_summary || '—',
        )}</div>
      </div>`
      : '';
    taskDetailPanel.innerHTML = `
      <div class="task-meta">
        <div>Run ID<span>${esc(item.run_id || '—')}</span></div>
        <div>状态<span>${esc(decisionLabel(item.status || 'unknown'))}</span></div>
        <div>任务名<span>${esc(item.run_name || '—')}</span></div>
        <div>用户<span>${esc(item.user_id || '—')}</span></div>
        <div>租户<span>${esc(item.tenant_id || '—')}</span></div>
        <div>Worker<span>${esc(item.worker_id || '—')}</span></div>
        <div>创建时间<span>${esc(fmtDate(item.created_at))}</span></div>
        <div>开始时间<span>${esc(fmtDate(item.started_at))}</span></div>
        <div>结束时间<span>${esc(fmtDate(item.finished_at))}</span></div>
        <div>适配器<span>${esc(item.dataset?.adapter || item.data?.adapter || '—')}</span></div>
        <div>输入格式<span>${esc(item.dataset?.input_format || item.data?.input_format || '—')}</span></div>
        <div>记录数<span>${esc(fmtCount(summary.num_records))}</span></div>
        <div>允许搜索轴<span>${esc((constraints.allowed_axes || []).join(', ') || '—')}</span></div>
        <div>迭代 / 样本<span>${esc(String(constraints.max_iterations || '—'))} / ${esc(
          String(constraints.sample_size || '—'),
        )}</span></div>
        <div>Env Refs<span>${esc((runtime.env_refs || []).join(', ') || '—')}</span></div>
        <div>Secret Refs<span>${esc((runtime.secret_refs || []).join(', ') || '—')}</span></div>
        <div>真实执行<span>${esc(String(!!runtime.real_execution))}</span></div>
        <div>保留时长 / 保留锁<span>${esc(String(retentionPolicy.retention_hours || runtime.retention_hours || '—'))}h / ${esc(
          String(!!(retentionPolicy.preserve_run ?? runtime.preserve_run)),
        )}</span></div>
      </div>
      ${item.error ? `<div class="task-message error" style="margin-top:10px">${esc(item.error)}</div>` : ''}
      ${summaryBox}`;
  }

  function renderArtifacts() {
    if (!state.selectedRunId || !state.latestArtifacts.length) {
      artifactPanel.innerHTML = '<div class="empty">暂无产物</div>';
      return;
    }
    artifactPanel.innerHTML = `<div class="artifact-list">${state.latestArtifacts
      .map((item) => {
        const encodedPath = item.path.split('/').map(encodeURIComponent).join('/');
        const href = api.apiUrl(`/api/runs/${encodeURIComponent(state.selectedRunId)}/download/${encodedPath}`);
        return `<div class="artifact-item"><a href="${esc(href)}" target="_blank" rel="noreferrer">${esc(
          item.path,
        )}</a><span>${esc(fmtBytes(item.size_bytes))}</span></div>`;
      })
      .join('')}</div>`;
  }

  function showParams(params) {
    if (!params) {
      paramsPanel.innerHTML = '<div class="empty">等待实验启动...</div>';
      return;
    }
    const rows = [
      ['模型', params.model],
      ['温度', params.temperature],
      ['最大Token', params.max_tokens],
      ['数据拆分', formatObjectInline(params.dataset_split)],
      ['Phase 设置', formatObjectInline(params.phase_settings)],
      ['Accept Gate', formatObjectInline(params.accept_thresholds)],
      ['置信度阈值', params.confidence_threshold],
      ['下注策略', params.bet_sizing],
      ['最大仓位比例', params.max_bet_fraction],
    ].filter(([, value]) => value !== undefined && value !== null && value !== '');
    paramsPanel.innerHTML =
      rows.map(([key, value]) => `<div class="param-row"><span class="k">${key}</span><span class="v">${esc(formatObjectInline(value))}</span></div>`).join('') +
      (params.system_prompt_preview ? `<div class="prompt-preview"><b>系统提示词：</b><br>${esc(params.system_prompt_preview)}</div>` : '') +
      (params.user_prompt_preview ? `<div class="prompt-preview"><b>用户提示词：</b><br>${esc(params.user_prompt_preview)}</div>` : '');
  }

  function renderChampionChallengerSummary() {
    const champion = latestAccepted(state.latestIterations) || null;
    const challenger = latestNonBaseline(state.latestIterations) || null;
    const validation = phase(champion, 'validation') || phase(champion, 'search');
    const holdout = phase(champion, 'holdout');
    const challengerValidation = phase(challenger, 'validation');
    const challengerSearch = phase(challenger, 'search');
    const championHtml = champion
      ? `
        <div class="mini-list">
          <div><b>#${champion.iteration}</b> ${esc(champion.patch_summary || 'champion')}</div>
          <div>${esc(summarizeConfig(champion.config_after || champion.config_before || {}))}</div>
          <div>Validation ${esc(validation ? fmtMaybe(validation.fitness_median, 4) : '—')} | Holdout ${esc(
            holdout ? fmtMaybe(holdout.fitness_median, 4) : '—',
          )}</div>
        </div>`
      : '<div>等待 champion 产生...</div>';
    const challengerHtml = challenger
      ? `
        <div class="mini-list">
          <div><b>#${challenger.iteration}</b> ${esc(challenger.patch_summary || 'challenger')}</div>
          <div>Axis ${esc(challenger.change_axis || 'unknown')} | Intent ${esc(challenger.search_intent || 'unknown')}</div>
          <div>Search ${esc(challengerSearch ? fmtMaybe(challengerSearch.fitness_median, 4) : '—')} | Validation ${esc(
            challengerValidation ? fmtMaybe(challengerValidation.fitness_median, 4) : '—',
          )}</div>
        </div>`
      : '<div>等待 challenger 产生...</div>';
    const outcomeHtml = challenger
      ? `
        <div class="mini-list">
          <div><b>${esc(decisionLabel(challenger.status || 'unknown'))}</b></div>
          <div>Gate Path: ${esc(gatePath(challenger))}</div>
          <div>${esc(challenger.decision_logic || '—')}</div>
        </div>`
      : '<div>等待 gate 结果...</div>';
    iterationSummaryBar.innerHTML = `
      <div class="iter-summary-card"><h4>Champion</h4><div>${championHtml}</div></div>
      <div class="iter-summary-card"><h4>Challenger</h4><div>${challengerHtml}</div></div>
      <div class="iter-summary-card"><h4>Gate Outcome</h4><div>${outcomeHtml}</div></div>`;
  }

  function renderComparisonBoard() {
    if (!state.latestResults.length) {
      comparisonPanel.innerHTML = `
        <div class="compare-card"><h4>Best Validation</h4><div class="big">等待实验结果...</div></div>
        <div class="compare-card"><h4>Status Mix</h4><div class="big">等待实验结果...</div></div>
        <div class="compare-card"><h4>Recent Runs</h4><div class="big">等待实验结果...</div></div>`;
      return;
    }
    const accepted = state.latestResults
      .filter((row) => row.status === 'accepted')
      .sort((a, b) => (b.validation_fitness ?? b.fitness ?? -Infinity) - (a.validation_fitness ?? a.fitness ?? -Infinity));
    const best = accepted[0] || state.latestResults[0];
    const efficiency = [...state.latestResults].sort((a, b) => {
      const aScore = (a.tokens || 0) > 0 ? (a.fitness || 0) / (a.tokens || 1) : -Infinity;
      const bScore = (b.tokens || 0) > 0 ? (b.fitness || 0) / (b.tokens || 1) : -Infinity;
      return bScore - aScore;
    })[0];
    const counts = state.latestResults.reduce((acc, row) => {
      acc[row.status] = (acc[row.status] || 0) + 1;
      return acc;
    }, {});
    const recent = state.latestResults.slice(-5).reverse();
    comparisonPanel.innerHTML = `
      <div class="compare-card">
        <h4>Best Validation</h4>
        <div class="big">${esc(best.commit || '—')} ${esc(decisionLabel(best.status || 'unknown'))}</div>
        <div class="small">Validation ${esc(fmtMaybe(best.validation_fitness ?? best.fitness, 4))} | Search ${esc(
          fmtMaybe(best.search_fitness, 4),
        )} | Holdout ${esc(fmtMaybe(best.holdout_fitness, 4))}</div>
        <div class="small">PnL ${esc(best.total_pnl === null || best.total_pnl === undefined ? '—' : String(best.total_pnl))} | Tokens ${esc(
          fmtCount(best.tokens),
        )}</div>
        <div class="small">Efficiency Winner: ${esc(
          efficiency ? `${efficiency.commit} (${fmtMaybe(efficiency.fitness, 4)} / ${fmtCount(efficiency.tokens)} tok)` : '—',
        )}</div>
      </div>
      <div class="compare-card">
        <h4>Status Mix</h4>
        <div class="compare-list">
          ${['accepted', 'search_reject', 'validation_reject', 'holdout_reject', 'failed']
            .map(
              (key) =>
                `<div class="compare-line"><div>${esc(decisionLabel(key))}</div><span>${esc(String(counts[key] || 0))}</span></div>`,
            )
            .join('')}
        </div>
      </div>
      <div class="compare-card">
        <h4>Recent Runs</h4>
        <div class="compare-list">
          ${recent
            .map(
              (row) =>
                `<div class="compare-line"><div>${esc(row.commit || '—')} ${esc(decisionLabel(row.status || 'unknown'))}</div><span>${esc(
                  fmtMaybe(row.validation_fitness ?? row.fitness, 4),
                )}</span></div>`,
            )
            .join('')}
        </div>
      </div>`;
  }

  function renderResults() {
    if (!state.latestResults.length) {
      resultsPanel.innerHTML = '<div class="empty">当前任务暂无实验记录</div>';
      renderComparisonBoard();
      charts.setTokenSeries([], []);
      return;
    }
    const tokenLabels = [];
    const tokenValues = [];
    resultsPanel.innerHTML = '';
    state.latestResults.forEach((item, index) => {
      const fitness = Number(item.fitness || 0);
      const tokens = Number(item.tokens || 0);
      const status = item.status || 'unknown';
      const fitLine = [
        item.search_fitness !== null && item.search_fitness !== undefined ? `S ${fmtMaybe(item.search_fitness, 4)}` : null,
        item.validation_fitness !== null && item.validation_fitness !== undefined ? `V ${fmtMaybe(item.validation_fitness, 4)}` : null,
        item.holdout_fitness !== null && item.holdout_fitness !== undefined ? `H ${fmtMaybe(item.holdout_fitness, 4)}` : null,
      ]
        .filter(Boolean)
        .join(' | ');
      const node = document.createElement('div');
      node.className = 'exp-item';
      node.innerHTML = `
        <div class="exp-head"><span class="exp-id">#${index + 1} ${esc(item.commit || '')}</span><span class="exp-fit ${statusColorClass(
          status,
        )}">${esc(decisionLabel(status))} ${fitness.toFixed(4)}</span></div>
        <div class="exp-desc">${esc(item.description || '')}</div>
        <div class="exp-meta">${esc(fitLine || 'fitness unavailable')} | 验证盈亏: ${
          item.total_pnl === null || item.total_pnl === undefined ? '—' : item.total_pnl
        } | ${tokens.toLocaleString()} token</div>`;
      resultsPanel.appendChild(node);
      tokenLabels.push(`实验${index + 1}`);
      tokenValues.push(tokens);
    });
    charts.setTokenSeries(tokenLabels, tokenValues);
    renderComparisonBoard();
  }

  function renderOrchestrator() {
    const data = state.latestOrchestrator;
    if (!data || (!data.goal && !(data.workers || []).length)) {
      orchestratorPanel.innerHTML = '<div class="empty">等待 orchestrator 启动...</div>';
      return;
    }
    const main = data.main_agent || {};
    const workers = data.workers || [];
    orchestratorPanel.innerHTML = `
      <div class="orch-goal">${esc(data.goal || '')}</div>
      <div class="orch-grid">
        <div class="orch-box"><h4>Completed</h4><ul>${renderList(main.completed)}</ul></div>
        <div class="orch-box"><h4>In Progress</h4><ul>${renderList(main.in_progress)}</ul></div>
        <div class="orch-box"><h4>Constraints</h4><ul>${renderList(main.constraints)}</ul></div>
        <div class="orch-box"><h4>Feedback</h4><ul>${renderList(main.feedback)}</ul></div>
        <div class="orch-box"><h4>Dispatch Logic</h4><ul>${renderList([main.next_dispatch_reasoning || ''])}</ul></div>
        <div class="orch-box"><h4>Notes</h4><ul>${renderList(main.notes)}</ul></div>
      </div>
      <div class="worker-list">
        ${workers
          .map(
            (worker) => `
            <div class="worker-card">
              <div class="worker-head">
                <div>
                  <div class="worker-name">${esc(worker.agent_id || '')}</div>
                  <div class="worker-role">${esc(worker.model || '')} / ${esc(worker.role || '')}</div>
                </div>
                <div class="worker-status ${esc(worker.status || 'pending')}">${esc(worker.status || 'pending')}</div>
              </div>
              <div class="worker-summary">${esc(worker.summary || '')}</div>
              <div class="worker-meta">Deliverables: ${esc((worker.deliverables || []).join(' | ') || '—')}</div>
              <div class="worker-meta">Tools: ${esc((worker.tool_summary || []).join(' | ') || '—')}</div>
              <div class="worker-meta">Context: ${esc(worker.context_path || '—')}</div>
              <div class="worker-meta">Next: ${esc(worker.next_action || '—')}</div>
            </div>`,
          )
          .join('')}
      </div>`;
    renderChampionChallengerSummary();
  }

  function renderIterations() {
    if (!state.latestIterations.length) {
      iterationSummaryBar.innerHTML = `
        <div class="iter-summary-card"><h4>Champion</h4><div>等待实验迭代...</div></div>
        <div class="iter-summary-card"><h4>Challenger</h4><div>等待实验迭代...</div></div>
        <div class="iter-summary-card"><h4>Gate Outcome</h4><div>等待实验迭代...</div></div>`;
      iterationPanel.innerHTML = '<div class="empty">等待实验迭代...</div>';
      return;
    }
    renderChampionChallengerSummary();
    const ordered = [...state.latestIterations].sort((a, b) => (b.iteration || 0) - (a.iteration || 0));
    iterationPanel.innerHTML = ordered
      .map(
        (item) => `
        <div class="iter-card">
          <div class="iter-head">
            <div class="iter-title">#${item.iteration} ${esc(item.patch_summary || item.kind || 'iteration')}</div>
            <div class="iter-status ${esc(item.status || '')}">${esc(decisionLabel(item.status || 'unknown'))}</div>
          </div>
          <div class="chip-row">
            <div class="chip">Decision: ${esc(decisionLabel(item.status || 'unknown'))}</div>
            <div class="chip">Axis: ${esc(item.change_axis || 'unknown')}</div>
            <div class="chip">Intent: ${esc(item.search_intent || 'unknown')}</div>
            <div class="chip">Step: ${esc(item.step_size || 'n/a')}</div>
            ${item.current_mode ? `<div class="chip">Mode After: ${esc(item.current_mode)}</div>` : ''}
            ${item.search_reject_streak !== undefined ? `<div class="chip">search_reject_streak=${esc(String(item.search_reject_streak))}</div>` : ''}
            ${
              item.validated_no_edge_streak !== undefined
                ? `<div class="chip">validated_no_edge=${esc(String(item.validated_no_edge_streak))}</div>`
                : ''
            }
          </div>
          <div class="iter-block"><h5>Phase 评估</h5>${renderPhaseResults(item)}</div>
          ${renderPhaseBreakdowns(item)}
          ${renderSearchShortlist(item)}
          <div class="iter-block">
            <h5>本轮到底改了什么</h5>
            ${renderConfigDiff(item.config_diff)}
            <div class="iter-note" style="margin-top:10px">自我调整范围: ${esc(
              (item.adjustment_scope || ['CONFIDENCE_THRESHOLD', 'BET_SIZING']).join(', '),
            )}</div>
          </div>
          ${renderPromptChange(item)}
          <div class="iter-block"><h5>调整依据</h5>${renderReadableReasoning(item.reasoning || '')}</div>
          <div class="iter-block"><h5>决策逻辑</h5><div class="iter-note">${esc(item.decision_logic || '')}</div></div>
          ${item.patch_excerpt ? `<div class="iter-block"><h5>Patch 摘录</h5><div class="iter-patch">${esc(item.patch_excerpt)}</div></div>` : ''}
        </div>`,
      )
      .join('');
  }

  function applyTokenSnapshot(snapshot) {
    const status = snapshot.status || 'idle';
    const badge = document.getElementById('statusBadge');
    badge.textContent = decisionLabel(status);
    badge.className = `badge ${status === 'running' ? 'b-run' : status === 'finished' ? 'b-done' : 'b-idle'}`;
    document.getElementById('progressText').textContent = snapshot.progress || '';
    const totalTokens = snapshot.total_tokens || 0;
    const limit = snapshot.budget_limit || snapshot.total_budget_limit || 500000;
    document.getElementById('gaugeFill').style.width = `${Math.min(100, totalTokens / Math.max(limit, 1) * 100)}%`;
    document.getElementById('tokenNum').textContent = `${fmt(totalTokens)} / ${fmt(limit)}`;
    document.getElementById('callNum').textContent = snapshot.api_calls || 0;
    document.getElementById('errNum').textContent = snapshot.api_errors || 0;
    document.getElementById('sPromptTok').textContent = fmt(snapshot.prompt_tokens || 0);
    document.getElementById('sCompTok').textContent = fmt(snapshot.completion_tokens || 0);
  }

  function resetSelection() {
    state.latestArtifacts = [];
    state.latestResults = [];
    state.latestIterations = [];
    state.latestOrchestrator = null;
    state.latestRunSummary = null;
    renderTaskDetail();
    renderArtifacts();
    showParams(null);
    renderOrchestrator();
    renderResults();
    renderIterations();
    applyTokenSnapshot({ status: 'idle', total_tokens: 0, budget_limit: 500000, api_calls: 0, api_errors: 0 });
    resetStreamPanel();
  }

  async function load(runId) {
    if (!runId) {
      resetSelection();
      return;
    }
    const [artifacts, summary, results, orchestrator, iterations, tokens] = await Promise.allSettled([
      api.getRunArtifacts(runId),
      api.getRunSummary(runId),
      api.getRunResults(runId),
      api.getRunOrchestrator(runId),
      api.getRunIterations(runId),
      api.getRunTokens(runId),
    ]);
    state.latestArtifacts = artifacts.status === 'fulfilled' && Array.isArray(artifacts.value) ? artifacts.value : [];
    state.latestRunSummary = summary.status === 'fulfilled' && !summary.value.error ? summary.value : null;
    state.latestResults = results.status === 'fulfilled' && Array.isArray(results.value) ? results.value : [];
    state.latestOrchestrator = orchestrator.status === 'fulfilled' ? orchestrator.value : null;
    state.latestIterations = iterations.status === 'fulfilled' && Array.isArray(iterations.value) ? iterations.value : [];
    renderTaskDetail();
    renderArtifacts();
    renderResults();
    renderOrchestrator();
    renderIterations();
    if (tokens.status === 'fulfilled') {
      applyTokenSnapshot(tokens.value || {});
    }
  }

  return {
    load,
    showParams,
    applyTokenSnapshot,
    resetSelection,
    resetStreamPanel,
    streamElements: { body: streamBody, count: streamCount, dot: streamDot },
  };
}
