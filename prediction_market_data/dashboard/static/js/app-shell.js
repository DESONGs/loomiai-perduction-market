import { createApiClient } from './api.js';
import { createDashboardCharts } from './charts.js';
import { createLiveStreamModule } from './live-stream.js';
import { createRunDetailModule } from './run-detail.js';
import { createRunListModule } from './run-list.js';
import { createTaskFormModule } from './task-form.js';

const state = {
  authToken: localStorage.getItem('pm_dashboard_auth_token') || '',
  availableRuns: [],
  selectedRunId: '',
  selectedRunMeta: null,
  latestArtifacts: [],
  latestResults: [],
  latestIterations: [],
  latestOrchestrator: null,
  latestRunSummary: null,
};

const api = createApiClient({ getAuthToken: () => state.authToken });
const charts = createDashboardCharts();
const detail = createRunDetailModule({ state, api, charts });
let loadingSelectedRun = Promise.resolve();

async function loadSelectedRun(runId = state.selectedRunId) {
  const currentRunId = runId || state.selectedRunId;
  if (!currentRunId) {
    detail.resetSelection();
    liveStream.stop();
    return;
  }
  await detail.load(currentRunId);
  if (currentRunId === state.selectedRunId) {
    liveStream.start(currentRunId);
  }
}

function refreshSelectedRun(runId = state.selectedRunId) {
  loadingSelectedRun = loadSelectedRun(runId).catch(() => {});
  return loadingSelectedRun;
}

const runList = createRunListModule({
  state,
  api,
  onRunSelected(runId) {
    refreshSelectedRun(runId);
  },
});

const liveStream = createLiveStreamModule({ state, api, charts, detail });

const taskForm = createTaskFormModule({
  state,
  api,
  onTaskCreated(runId) {
    runList.load(runId).then(() => refreshSelectedRun(runId));
  },
  onStopSelected() {
    runList.load(state.selectedRunId).then(() => refreshSelectedRun(state.selectedRunId));
  },
  onCleanup() {
    runList.load(state.selectedRunId).then(() => refreshSelectedRun(state.selectedRunId));
  },
  onAuthChanged() {
    runList.load(state.selectedRunId).then(() => refreshSelectedRun(state.selectedRunId));
  },
});

function createScheduler() {
  const jobs = [];
  let timer = null;

  function add(name, intervalMs, task) {
    jobs.push({ name, intervalMs, task, running: false, nextAt: 0 });
  }

  async function tick() {
    const now = Date.now();
    for (const job of jobs) {
      if (job.running || now < job.nextAt) {
        continue;
      }
      job.running = true;
      job.nextAt = now + job.intervalMs;
      Promise.resolve(job.task())
        .catch(() => {})
        .finally(() => {
          job.running = false;
        });
    }
  }

  function start() {
    if (timer) {
      return;
    }
    timer = window.setInterval(tick, 500);
    tick();
  }

  return { add, start };
}

async function boot() {
  detail.resetSelection();
  await runList.load();
  await refreshSelectedRun();

  const scheduler = createScheduler();
  scheduler.add('runs', 10000, () => runList.load(state.selectedRunId));
  scheduler.add('selected-run', 4000, () => refreshSelectedRun(state.selectedRunId));
  scheduler.start();

  window.addEventListener('beforeunload', () => {
    liveStream.stop();
  });
}

boot().catch((error) => {
  taskForm.setMessage(error.message || 'Dashboard 初始化失败', true);
});
