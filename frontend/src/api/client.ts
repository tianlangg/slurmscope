export const API = import.meta.env.VITE_API_URL ?? ''

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API}/api${path}`, { ...init, headers: { 'Content-Type': 'application/json', ...init?.headers } })
  if (!response.ok) {
    let message = `Request failed (${response.status})`
    try { const body = await response.json(); message = body.detail ?? message } catch { /* keep status message */ }
    throw new Error(message)
  }
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

export type Metric = { name: string; value: number }
export type Run = { id: string; experiment_id: string; parameters: Record<string, number>; backend: string; status: string; external_job_id: string | null; exit_code: number | null; error_message: string | null; created_at: string; submitted_at: string | null; started_at: string | null; finished_at: string | null; metrics: Metric[] }
export type Experiment = { id: string; name: string; description: string | null; template_key: string; backend: 'local'|'slurm'; sweep_spec: Record<string, number[]>; resource_spec: Record<string, string|number>; created_at: string; updated_at: string }

export const api = {
  ready: () => request<{status:string;database:string}>('/ready'),
  experiments: () => request<Experiment[]>('/experiments'),
  experiment: (id: string) => request<Experiment>(`/experiments/${id}`),
  createExperiment: (data: Partial<Experiment>) => request<Experiment>('/experiments', { method: 'POST', body: JSON.stringify(data) }),
  updateExperiment: (id: string, data: Partial<Experiment>) => request<Experiment>(`/experiments/${id}`, { method: 'PATCH', body: JSON.stringify(data) }),
  deleteExperiment: (id: string) => request<void>(`/experiments/${id}`, { method: 'DELETE' }),
  runs: (id?: string) => request<Run[]>(id ? `/experiments/${id}/runs` : '/runs'),
  run: (id: string) => request<Run>(`/runs/${id}`),
  preview: (id: string, sweep_spec?: Record<string, number[]>) => request<{count:number;runs:Record<string,number>[]}>(`/experiments/${id}/preview`, { method:'POST', body:JSON.stringify({sweep_spec}) }),
  launch: (id: string) => request<{count:number;run_ids:string[]}>(`/experiments/${id}/launch`, { method:'POST', headers:{'Idempotency-Key': crypto.randomUUID()} }),
  cancel: (id: string) => request<{status:string;accepted:boolean}>(`/runs/${id}/cancel`, { method:'POST' }),
  logs: (id: string, stream: string) => request<{content:string}>(`/runs/${id}/logs?stream=${stream}&tail=200`),
  events: (id: string) => request<{id:string;new_status:string;message:string|null;created_at:string}[]>(`/runs/${id}/events`),
  comparison: (id: string) => request<{runs:Run[];metrics:string[]}>(`/experiments/${id}/comparison`),
}
