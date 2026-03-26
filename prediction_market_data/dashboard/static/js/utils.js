export const STATUS_LABELS = {
  idle: '空闲',
  created: '已创建',
  queued: '排队中',
  running: '运行中',
  finished: '已完成',
  failed: '失败',
  stopped: '已停止',
  unknown: '未知',
  accepted: '已接受',
  search_reject: 'Search 拒绝',
  validation_reject: 'Validation 拒绝',
  provisional: 'Provisional',
  holdout_reject: 'Holdout 拒绝',
  discard: '丢弃',
};

export const RUN_STATUS_PRIORITY = {
  running: 0,
  queued: 1,
  created: 2,
  failed: 3,
  stopped: 4,
  finished: 5,
};

export function decisionLabel(status) {
  return STATUS_LABELS[status] || status || '未知';
}

export function esc(value) {
  if (value === null || value === undefined) {
    return '';
  }
  const div = document.createElement('div');
  div.textContent = String(value);
  return div.innerHTML;
}

export function fmt(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) {
    return '—';
  }
  if (number >= 1e6) {
    return `${(number / 1e6).toFixed(1)}M`;
  }
  if (number >= 1e3) {
    return `${(number / 1e3).toFixed(1)}K`;
  }
  return number.toString();
}

export function fmtCount(value) {
  return value === null || value === undefined || value === '' ? '—' : fmt(value);
}

export function fmtMaybe(value, digits = 4) {
  return value === null || value === undefined || value === '' ? '—' : Number(value).toFixed(digits);
}

export function fmtDate(value) {
  if (!value) {
    return '—';
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return String(value);
  }
  return date.toLocaleString('zh-CN', { hour12: false });
}

export function fmtBytes(bytes) {
  const value = Number(bytes || 0);
  if (!Number.isFinite(value) || value <= 0) {
    return '0 B';
  }
  if (value >= 1024 * 1024) {
    return `${(value / (1024 * 1024)).toFixed(1)} MB`;
  }
  if (value >= 1024) {
    return `${(value / 1024).toFixed(1)} KB`;
  }
  return `${Math.round(value)} B`;
}

export function formatObjectInline(value) {
  if (value === null || value === undefined || value === '') {
    return '—';
  }
  if (Array.isArray(value)) {
    return value.length ? value.join(', ') : '—';
  }
  if (typeof value === 'object') {
    return Object.entries(value)
      .map(([key, item]) => `${key}: ${typeof item === 'object' ? JSON.stringify(item) : item}`)
      .join(' | ');
  }
  return String(value);
}

export function summarizeConfig(config) {
  if (!config) {
    return '—';
  }
  const parts = [];
  if (config.CONFIDENCE_THRESHOLD !== undefined && config.CONFIDENCE_THRESHOLD !== null) {
    parts.push(`T=${config.CONFIDENCE_THRESHOLD}`);
  }
  if (config.BET_SIZING) {
    parts.push(`Sizing=${config.BET_SIZING}`);
  }
  if (config.MAX_BET_FRACTION !== undefined && config.MAX_BET_FRACTION !== null) {
    parts.push(`MaxBet=${config.MAX_BET_FRACTION}`);
  }
  if (config.PROMPT_PROFILE) {
    parts.push(`Prompt=${config.PROMPT_PROFILE}`);
  } else if (Array.isArray(config.PROMPT_FACTORS)) {
    parts.push(`Prompt=${config.PROMPT_FACTORS.length ? config.PROMPT_FACTORS.join('+') : 'baseline'}`);
  }
  return parts.join(' | ') || '—';
}

export function sortRuns(items) {
  return [...items].sort((a, b) => {
    const aPriority = RUN_STATUS_PRIORITY[a.status] ?? 99;
    const bPriority = RUN_STATUS_PRIORITY[b.status] ?? 99;
    if (aPriority !== bPriority) {
      return aPriority - bPriority;
    }
    return String(b.updated_at || b.started_at || b.created_at || '').localeCompare(
      String(a.updated_at || a.started_at || a.created_at || ''),
    );
  });
}

export function statusColorClass(status) {
  return {
    accepted: 'v-green',
    search_reject: 'v-yellow',
    validation_reject: 'v-red',
    provisional: 'v-blue',
    holdout_reject: 'v-yellow',
    discard: 'v-red',
    failed: 'v-yellow',
    queued: 'v-blue',
    running: 'v-blue',
    finished: 'v-green',
    stopped: 'v-red',
    created: 'v-yellow',
  }[status] || 'v-blue';
}

export function updateUrlRunId(runId) {
  const url = new URL(window.location.href);
  if (runId) {
    url.searchParams.set('run_id', runId);
  } else {
    url.searchParams.delete('run_id');
  }
  window.history.replaceState({}, '', url);
}

export function readRequestedRunId() {
  return new URL(window.location.href).searchParams.get('run_id') || '';
}
