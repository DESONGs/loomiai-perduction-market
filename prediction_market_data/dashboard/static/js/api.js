export function createApiClient({ getAuthToken }) {
  function authToken() {
    return (getAuthToken?.() || '').trim();
  }

  function apiUrl(path) {
    const url = new URL(path, window.location.origin);
    const token = authToken();
    if (token) {
      url.searchParams.set('auth_token', token);
    }
    return `${url.pathname}${url.search}`;
  }

  async function request(path, options = {}) {
    const headers = new Headers(options.headers || {});
    const token = authToken();
    if (token) {
      headers.set('Authorization', `Bearer ${token}`);
    }
    const response = await fetch(apiUrl(path), { ...options, headers });
    const contentType = response.headers.get('content-type') || '';
    const payload = contentType.includes('application/json')
      ? await response.json().catch(() => ({}))
      : await response.text().catch(() => '');
    if (!response.ok) {
      const message = typeof payload === 'string' ? payload : payload.error || `HTTP ${response.status}`;
      throw new Error(message);
    }
    return payload;
  }

  return {
    apiUrl,
    listRuns: () => request('/api/runs'),
    getRunSummary: (runId) => request(`/api/runs/${encodeURIComponent(runId)}/summary`),
    getRunArtifacts: (runId) => request(`/api/runs/${encodeURIComponent(runId)}/artifacts`),
    getRunResults: (runId) => request(`/api/runs/${encodeURIComponent(runId)}/results`),
    getRunOrchestrator: (runId) => request(`/api/runs/${encodeURIComponent(runId)}/orchestrator`),
    getRunIterations: (runId) => request(`/api/runs/${encodeURIComponent(runId)}/iterations`),
    getRunTokens: (runId) => request(`/api/runs/${encodeURIComponent(runId)}/tokens`),
    getRunLog: (runId) => request(`/api/runs/${encodeURIComponent(runId)}/log`),
    createTask: (formData) => request('/api/tasks', { method: 'POST', body: formData }),
    preflightTask: (formData) => request('/api/tasks/preflight', { method: 'POST', body: formData }),
    stopRun: (runId) => request(`/api/runs/${encodeURIComponent(runId)}/stop`, { method: 'POST' }),
    cleanupRuns: () => request('/api/runs/cleanup', { method: 'POST' }),
    openStream(runId) {
      if (!runId) {
        return null;
      }
      return new EventSource(apiUrl(`/api/runs/${encodeURIComponent(runId)}/stream`));
    },
  };
}
