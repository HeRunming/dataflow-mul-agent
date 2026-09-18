/* Thin fetch wrapper. Every call surfaces the backend `detail` message,
   because those messages are the workbench's main failure explanation. */
async function request(path, options = {}) {
  const response = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  if (!response.ok) {
    const body = await response.json().catch(() => ({}))
    throw new Error(body.detail || `${response.status} ${response.statusText}`)
  }
  return response.status === 204 ? null : response.json()
}

const send = (method) => (path, body) =>
  request(path, { method, ...(body === undefined ? {} : { body: JSON.stringify(body) }) })

export const api = {
  get: (path) => request(path),
  post: send('POST'),
  delete: send('DELETE'),

  health: () => request('/api/v1/health'),

  resources: () => request('/api/v1/resources'),
  registerResource: (payload) => send('POST')('/api/v1/resources', payload),
  deleteResource: (name) => send('DELETE')(`/api/v1/resources/${encodeURIComponent(name)}`),
  models: (payload) => send('POST')('/api/v1/models', payload),

  datasets: () => request('/api/v1/datasets'),
  registerDataset: (payload) => send('POST')('/api/v1/datasets', payload),
  datasetPreview: (id) => request(`/api/v1/datasets/${encodeURIComponent(id)}/preview`),
  deleteDataset: (id) => send('DELETE')(`/api/v1/datasets/${encodeURIComponent(id)}`),

  runs: () => request('/api/v1/runs'),
  run: (id) => request(`/api/v1/runs/${id}`),
  events: (id) => request(`/api/v1/runs/${id}/events`),
  agentOutputs: (id) => request(`/api/v1/runs/${id}/agent-outputs`),
  stages: (id) => request(`/api/v1/runs/${id}/stages`),
  collaboration: (id) => request(`/api/v1/runs/${id}/collaboration`),
  skills: (id) => request(`/api/v1/runs/${id}/skills`),
  evidence: (id) => request(`/api/v1/runs/${id}/evidence`),
  pipelineCode: (id) => request(`/api/v1/runs/${id}/pipeline-code`),
  createRun: (payload) => send('POST')('/api/v1/runs', payload),
  deleteRun: (id) => send('DELETE')(`/api/v1/runs/${id}`),
  execute: (id) => send('POST')(`/api/v1/runs/${id}/execute`),

  createConversation: (payload) => send('POST')('/api/v1/conversations', payload),
  sendMessage: (id, payload) => send('POST')(`/api/v1/conversations/${id}/messages`, payload),
}
