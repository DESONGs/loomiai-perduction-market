import { fmtBytes } from './utils.js';

const TASK_DEFAULTS = {
  user_id: 'local',
  tenant_id: 'default',
  allowed_axes: 'CONFIDENCE_THRESHOLD,BET_SIZING,MAX_BET_FRACTION,PROMPT_FACTORS',
  max_iterations: '10',
  sample_size: '200',
  per_eval_token_budget: '150000',
  total_token_budget: '0',
  retention_hours: '168',
  env_refs: '',
  secret_refs: '',
  real_execution: false,
  probe_model: false,
};

const TASK_PRESETS = {
  smoke: { label: '最小试跑', max_iterations: '1', sample_size: '5', per_eval_token_budget: '50000', total_token_budget: '120000' },
  qa: { label: '快速 QA', max_iterations: '3', sample_size: '20', per_eval_token_budget: '20000', total_token_budget: '120000' },
  full: { label: '完整迭代', max_iterations: '10', sample_size: '200', per_eval_token_budget: '150000', total_token_budget: '0' },
};

export function createTaskFormModule({ state, api, onTaskCreated, onStopSelected, onCleanup, onAuthChanged }) {
  const form = document.getElementById('taskForm');
  const messageEl = document.getElementById('taskMessage');
  const datasetInput = document.getElementById('taskDataset');
  const datasetPicker = document.getElementById('taskDatasetPicker');
  const datasetMeta = document.getElementById('taskDatasetMeta');
  const clearDatasetBtn = document.getElementById('taskDatasetClearBtn');
  const authTokenInput = document.getElementById('authTokenInput');

  function setMessage(message, isError = false) {
    messageEl.textContent = message;
    messageEl.className = `task-message${isError ? ' error' : ''}`;
  }

  function applyDefaults() {
    document.getElementById('taskUserId').value = TASK_DEFAULTS.user_id;
    document.getElementById('taskTenantId').value = TASK_DEFAULTS.tenant_id;
    document.getElementById('taskAllowedAxes').value = TASK_DEFAULTS.allowed_axes;
    document.getElementById('taskMaxIterations').value = TASK_DEFAULTS.max_iterations;
    document.getElementById('taskSampleSize').value = TASK_DEFAULTS.sample_size;
    document.getElementById('taskPerEvalBudget').value = TASK_DEFAULTS.per_eval_token_budget;
    document.getElementById('taskTotalBudget').value = TASK_DEFAULTS.total_token_budget;
    document.getElementById('taskRetentionHours').value = TASK_DEFAULTS.retention_hours;
    document.getElementById('taskEnvRefs').value = TASK_DEFAULTS.env_refs;
    document.getElementById('taskSecretRefs').value = TASK_DEFAULTS.secret_refs;
    document.getElementById('taskRealExecution').checked = TASK_DEFAULTS.real_execution;
    document.getElementById('taskProbeModel').checked = TASK_DEFAULTS.probe_model;
  }

  function updateDatasetSelectionUI() {
    const file = datasetInput.files && datasetInput.files[0];
    clearDatasetBtn.disabled = !file;
    datasetPicker.classList.toggle('has-file', !!file);
    if (!file) {
      datasetMeta.innerHTML = '<span class="file-pill">尚未选择文件</span><span class="file-pill muted">支持 .json / .csv</span>';
      return;
    }
    const suffix = file.name.includes('.') ? file.name.split('.').pop().toUpperCase() : 'FILE';
    datasetMeta.innerHTML = `
      <span class="file-pill strong">${file.name}</span>
      <span class="file-pill">${suffix}</span>
      <span class="file-pill">${fmtBytes(file.size)}</span>
    `;
  }

  function resetForm() {
    form.reset();
    applyDefaults();
    updateDatasetSelectionUI();
  }

  function applyPreset(name) {
    const preset = TASK_PRESETS[name];
    if (!preset) {
      return;
    }
    document.getElementById('taskMaxIterations').value = preset.max_iterations;
    document.getElementById('taskSampleSize').value = preset.sample_size;
    document.getElementById('taskPerEvalBudget').value = preset.per_eval_token_budget;
    document.getElementById('taskTotalBudget').value = preset.total_token_budget;
    setMessage(`已切换为 ${preset.label} 预设。`);
  }

  async function submitTask(event) {
    event.preventDefault();
    const submitBtn = document.getElementById('taskSubmitBtn');
    const data = new FormData(form);
    if (!data.get('dataset') || !data.get('dataset').name) {
      setMessage('请先选择数据文件。', true);
      return;
    }
    submitBtn.disabled = true;
    setMessage('任务提交中...');
    try {
      const payload = await api.createTask(data);
      setMessage(`任务已提交: ${payload.run_id}`);
      resetForm();
      onTaskCreated(payload.run_id);
    } catch (error) {
      setMessage(error.message || '任务提交失败', true);
    } finally {
      submitBtn.disabled = false;
    }
  }

  async function preflightTask() {
    const preflightBtn = document.getElementById('taskPreflightBtn');
    const data = new FormData(form);
    if (!data.get('dataset') || !data.get('dataset').name) {
      setMessage('预检查需要先选择数据文件。', true);
      return;
    }
    preflightBtn.disabled = true;
    setMessage('任务预检查中...');
    try {
      const payload = await api.preflightTask(data);
      const probe = payload.probe;
      const probeLabel = probe && probe.ok ? ` | Probe ${probe.provider || 'model'} ${probe.model_name || ''} ${probe.latency_ms || 0}ms` : '';
      setMessage(`预检查通过: dataset=${payload.dataset?.num_records || 0} 条${probeLabel}`);
    } catch (error) {
      setMessage(error.message || '预检查失败', true);
    } finally {
      preflightBtn.disabled = false;
    }
  }

  async function stopSelectedRun() {
    if (!state.selectedRunId) {
      setMessage('当前没有可停止的任务。', true);
      return;
    }
    setMessage(`停止任务 ${state.selectedRunId} ...`);
    try {
      await api.stopRun(state.selectedRunId);
      setMessage(`任务已停止: ${state.selectedRunId}`);
      onStopSelected();
    } catch (error) {
      setMessage(error.message || '停止任务失败', true);
    }
  }

  async function cleanupExpiredRuns() {
    setMessage('清理过期任务中...');
    try {
      const payload = await api.cleanupRuns();
      setMessage(`清理完成: ${payload.count || 0} 个任务`);
      onCleanup();
    } catch (error) {
      setMessage(error.message || '清理失败', true);
    }
  }

  function saveAuthToken() {
    state.authToken = (authTokenInput.value || '').trim();
    if (state.authToken) {
      localStorage.setItem('pm_dashboard_auth_token', state.authToken);
    } else {
      localStorage.removeItem('pm_dashboard_auth_token');
    }
    setMessage(state.authToken ? '已保存 API Token' : '已清空 API Token');
    onAuthChanged();
  }

  form.addEventListener('submit', submitTask);
  datasetInput.addEventListener('change', updateDatasetSelectionUI);
  clearDatasetBtn.addEventListener('click', () => {
    datasetInput.value = '';
    updateDatasetSelectionUI();
  });
  document.getElementById('taskPreflightBtn').addEventListener('click', preflightTask);
  document.getElementById('taskStopBtn').addEventListener('click', stopSelectedRun);
  document.getElementById('taskCleanupBtn').addEventListener('click', cleanupExpiredRuns);
  document.querySelectorAll('[data-preset]').forEach((button) => {
    button.addEventListener('click', () => applyPreset(button.dataset.preset));
  });
  document.getElementById('authTokenBtn').addEventListener('click', saveAuthToken);

  authTokenInput.value = state.authToken;
  applyDefaults();
  updateDatasetSelectionUI();
  setMessage('等待任务提交。');

  return {
    setMessage,
    resetForm,
  };
}
