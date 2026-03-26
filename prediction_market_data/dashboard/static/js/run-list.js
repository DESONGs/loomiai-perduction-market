import { decisionLabel, esc, fmtDate, readRequestedRunId, sortRuns, updateUrlRunId } from './utils.js';

function runCardSummary(item) {
  const constraints = item.constraints || {};
  const summary = item.summary || item.data?.summary || {};
  const parts = [];
  if (constraints.max_iterations) parts.push(`${constraints.max_iterations} 轮`);
  if (constraints.sample_size) parts.push(`样本 ${constraints.sample_size}`);
  if (summary.num_records) parts.push(`数据 ${summary.num_records}`);
  if (item.worker_id) parts.push(`worker ${item.worker_id}`);
  return parts.join(' · ') || '暂无配置摘要';
}

export function createRunListModule({ state, api, onRunSelected }) {
  const select = document.getElementById('runSelect');
  const summaryEl = document.getElementById('runListSummary');
  const panel = document.getElementById('runListPanel');
  const currentRunText = document.getElementById('currentRunText');

  function applySelection(runId) {
    state.selectedRunId = runId || '';
    state.selectedRunMeta = state.availableRuns.find((item) => item.run_id === state.selectedRunId) || null;
    updateUrlRunId(state.selectedRunId);
    if (select && select.value !== state.selectedRunId) {
      select.value = state.selectedRunId;
    }
    currentRunText.textContent = `当前任务: ${
      state.selectedRunMeta ? `${state.selectedRunMeta.run_id} / ${state.selectedRunMeta.status}` : '—'
    }`;
  }

  function render() {
    if (!state.availableRuns.length) {
      summaryEl.textContent = '暂无任务。提交后会自动出现在这里。';
      select.innerHTML = '<option value="">暂无任务</option>';
      panel.innerHTML = '<div class="empty">暂无任务</div>';
      applySelection('');
      return;
    }

    const requestedRunId = readRequestedRunId() || state.selectedRunId || '';
    const chosen = (state.availableRuns.find((item) => item.run_id === requestedRunId) || state.availableRuns[0]).run_id;
    applySelection(chosen);

    const counts = state.availableRuns.reduce((acc, item) => {
      acc[item.status] = (acc[item.status] || 0) + 1;
      return acc;
    }, {});
    summaryEl.textContent = `共 ${state.availableRuns.length} 个任务 · 运行中 ${counts.running || 0} · 排队中 ${counts.queued || 0} · 已完成 ${counts.finished || 0}`;
    select.innerHTML = state.availableRuns
      .map(
        (item) =>
          `<option value="${esc(item.run_id)}">${esc(item.run_name || item.run_id)} · ${esc(
            decisionLabel(item.status || 'unknown'),
          )} · ${esc(item.run_id)}</option>`,
      )
      .join('');
    select.value = state.selectedRunId;
    panel.innerHTML = state.availableRuns
      .map(
        (item) => `
        <div class="run-card ${item.run_id === state.selectedRunId ? 'active' : ''}" data-run-id="${esc(item.run_id)}">
          <div class="run-card-top">
            <div>
              <div class="run-card-name">${esc(item.run_name || item.run_id)}</div>
              <div class="run-card-id">${esc(item.run_id)}</div>
            </div>
            <div class="run-status-chip ${esc(item.status || 'created')}">${esc(decisionLabel(item.status || 'unknown'))}</div>
          </div>
          <div class="run-card-summary">${esc(runCardSummary(item))}</div>
          <div class="run-chip-row">
            <div class="run-chip">用户 ${esc(item.user_id || '—')}</div>
            <div class="run-chip">租户 ${esc(item.tenant_id || '—')}</div>
            <div class="run-chip">创建 ${esc(fmtDate(item.created_at))}</div>
          </div>
          <div class="run-card-meta">开始 ${esc(fmtDate(item.started_at))} · 更新 ${esc(fmtDate(item.updated_at))}</div>
        </div>`,
      )
      .join('');

    panel.querySelectorAll('[data-run-id]').forEach((node) => {
      node.addEventListener('click', () => {
        const runId = node.getAttribute('data-run-id') || '';
        if (!runId || runId === state.selectedRunId) {
          return;
        }
        applySelection(runId);
        render();
        onRunSelected(runId);
      });
    });
  }

  async function load(preferredRunId = '') {
    const data = await api.listRuns();
    state.availableRuns = sortRuns(Array.isArray(data) ? data : []);
    if (preferredRunId) {
      state.selectedRunId = preferredRunId;
    }
    render();
    return state.selectedRunId;
  }

  select.addEventListener('change', () => {
    applySelection(select.value);
    render();
    onRunSelected(state.selectedRunId);
  });

  document.getElementById('runListRefreshBtn').addEventListener('click', () => {
    load().then((runId) => {
      if (runId) {
        onRunSelected(runId);
      }
    });
  });

  return {
    load,
    render,
    applySelection,
  };
}
