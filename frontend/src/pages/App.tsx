import { FormEvent, useEffect, useMemo, useState } from 'react'
import { Link, NavLink, Route, Routes, useNavigate, useParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { api, Experiment, Run } from '../api/client'

const active = ['QUEUED','SUBMITTING','PENDING','RUNNING','CANCELLING','UNKNOWN']
const time = (value?: string | null) => value ? new Date(value).toLocaleString() : '—'
const message = (error: unknown) => typeof error === 'string' ? error : error instanceof Error ? error.message : 'Something went wrong'
function useRuns(id?: string) { return useQuery({ queryKey:['runs',id], queryFn:()=>api.runs(id), refetchInterval:(query)=>query.state.data?.some(run=>active.includes(run.status)) ? 3000 : false }) }
function State({value}:{value:string}) { return <span className={`state state-${value.toLowerCase()}`}>{value}</span> }
function ErrorBox({error}:{error:unknown}) { return error ? <div className="error" role="alert">{message(error)}</div> : null }

export default function App() {
  const ready = useQuery({ queryKey: ['ready'], queryFn: api.ready, refetchInterval: 10000, retry: false })
  return <div className="shell"><aside className="sidebar"><Link to="/" className="brand"><span className="brand-mark">S</span><span>slurmscope<small>EXPERIMENT CONTROL</small></span></Link><div className="nav-label">WORKSPACE</div><NavLink end to="/">⌂　Overview</NavLink><NavLink to="/experiments">▦　Experiments</NavLink><div className="side-note"><span className={`live-dot ${ready.isError ? 'offline' : ''}`}/>{ready.isError ? 'API unavailable' : ready.data ? 'API ready' : 'Checking API…'}<small>{ready.data?.database === 'ok' ? 'PostgreSQL connected' : 'Database status unavailable'}</small></div><div className="side-footer">v0.1 · Single user workspace</div></aside><main className="main"><header className="topbar"><div><span className="crumb">RESEARCH WORKSPACE</span><h1>Experiment control</h1></div><Link className="button primary" to="/experiments/new">＋ New experiment</Link></header><Routes><Route path="/" element={<Dashboard/>}/><Route path="/experiments" element={<Experiments/>}/><Route path="/experiments/new" element={<Editor/>}/><Route path="/experiments/:id/edit" element={<Editor isEdit/>}/><Route path="/experiments/:id" element={<ExperimentPage/>}/><Route path="/runs/:id" element={<RunPage/>}/><Route path="*" element={<Dashboard/>}/></Routes></main></div>
}

function Dashboard() {
  const experiments=useQuery({queryKey:['experiments'],queryFn:api.experiments})
  const runs=useRuns()
  const list=runs.data??[]
  const counts=['RUNNING','QUEUED','SUCCEEDED','FAILED'].map(state=>[state,list.filter(r=>r.status===state).length])
  return <><section className="welcome"><div><span className="eyebrow">YOUR RESEARCH, IN MOTION</span><h2>Run experiments with confidence.</h2><p>Configure parameter sweeps, monitor compute jobs, and compare results in one place.</p><Link className="button light" to="/experiments/new">Create an experiment <span>→</span></Link></div><div className="welcome-art"><div className="ring ring-one"/><div className="ring ring-two"/><div className="orbit-dot"/><div className="orbit-core">∑</div></div></section><ErrorBox error={experiments.error||runs.error}/><section className="stats">{counts.map(([state,count])=><div className="stat" key={state}><span>{state}</span><strong>{count}</strong><small>Recent runs</small></div>)}</section><div className="section-head"><div><span className="eyebrow">ACTIVITY</span><h2>Recent runs</h2></div><Link to="/experiments">All experiments →</Link></div><RunTable runs={list.slice(0,8)}/><div className="section-head"><div><span className="eyebrow">YOUR WORK</span><h2>Experiments</h2></div><Link to="/experiments">View all →</Link></div><ExperimentCards experiments={(experiments.data??[]).slice(0,3)}/></>
}

function Experiments() { const q=useQuery({queryKey:['experiments'],queryFn:api.experiments}); const [search,setSearch]=useState(''); const items=(q.data??[]).filter(e=>e.name.toLowerCase().includes(search.toLowerCase())); return <><div className="section-head"><div><span className="eyebrow">WORKSPACE</span><h2>Experiments</h2><p className="muted">Manage configurations and explore run history.</p></div><Link className="button primary" to="/experiments/new">＋ New experiment</Link></div><input className="search" aria-label="Search experiments" placeholder="Search experiments…" value={search} onChange={e=>setSearch(e.target.value)}/><ErrorBox error={q.error}/><ExperimentCards experiments={items}/></> }

function ExperimentCards({experiments}:{experiments:Experiment[]}) { if(!experiments.length)return <div className="empty">No experiments yet. Create one to start a parameter sweep.</div>; return <div className="experiment-grid">{experiments.map(e=><Link className="experiment-card" key={e.id} to={`/experiments/${e.id}`}><div className="card-top"><span className="template-icon">⌘</span><span className="backend">{e.backend}</span></div><h3>{e.name}</h3><p>{e.description||'Synthetic benchmark experiment'}</p><div className="card-bottom"><span>{Object.keys(e.sweep_spec).length} parameters</span><span>{time(e.created_at)}</span></div></Link>)}</div> }

function Editor({ isEdit = false }: { isEdit?: boolean }) {
  const { id = '' } = useParams()
  const nav = useNavigate()
  const cache = useQueryClient()
  const original = useQuery({ queryKey: ['experiment', id], queryFn: () => api.experiment(id), enabled: isEdit })
  const [name, setName] = useState('Synthetic benchmark sweep')
  const [description, setDescription] = useState('')
  const [backend, setBackend] = useState<'local' | 'slurm'>('local')
  const [grid, setGrid] = useState('{\n  "learning_rate": [0.01, 0.1, 0.5],\n  "seed": [1, 2]\n}')
  const [resources, setResources] = useState('{\n  "cpus_per_task": 2,\n  "memory_mb": 2048,\n  "time_limit_minutes": 30\n}')
  const [error, setError] = useState('')
  const [preview, setPreview] = useState<Record<string, number>[] | null>(null)
  useEffect(() => {
    if (!original.data) return
    setName(original.data.name)
    setDescription(original.data.description ?? '')
    setBackend(original.data.backend)
    setGrid(JSON.stringify(original.data.sweep_spec, null, 2))
    setResources(JSON.stringify(original.data.resource_spec, null, 2))
  }, [original.data])
  const save = useMutation({
    mutationFn: (data: Partial<Experiment>) => isEdit ? api.updateExperiment(id, data) : api.createExperiment(data),
    onSuccess: async item => {
      await Promise.all([cache.invalidateQueries({ queryKey: ['experiments'] }), cache.invalidateQueries({ queryKey: ['experiment', item.id] })])
      nav(`/experiments/${item.id}`)
    },
  })
  function parse(): { sweep: Record<string, number[]>; resource: Record<string, string | number> } | null {
    try {
      const sweep = JSON.parse(grid) as Record<string, number[]>
      const count = Object.values(sweep).reduce((n, values) => n * values.length, 1)
      if (!sweep || Object.keys(sweep).length === 0) throw Error('Add at least one sweep parameter.')
      if (count > 50) throw Error('A launch can contain at most 50 runs.')
      const resource = backend === 'slurm' ? JSON.parse(resources) as Record<string, string | number> : {}
      setError('')
      return { sweep, resource }
    } catch (cause) {
      setError(`Check the experiment settings: ${String(cause)}`)
      return null
    }
  }
  function submit(event: FormEvent) {
    event.preventDefault()
    const value = parse()
    if (!value) return
    save.mutate({ name, description: description || null, backend, template_key: 'synthetic_benchmark', sweep_spec: value.sweep, resource_spec: value.resource })
  }
  const count = useMemo(() => {
    try { return Object.values(JSON.parse(grid) as Record<string, number[]>).reduce((n, values) => n * values.length, 1) }
    catch { return 0 }
  }, [grid])
  if (isEdit && original.isLoading) return <div className="empty">Loading experiment…</div>
  if (original.error) return <ErrorBox error={original.error} />
  return <><div className="section-head"><div><span className="eyebrow">{isEdit ? 'EDIT CONFIGURATION' : 'NEW CONFIGURATION'}</span><h2>{isEdit ? 'Edit experiment' : 'Create experiment'}</h2><p className="muted">Define the approved task, execution backend, and parameter grid.</p></div></div><ErrorBox error={save.error} /><form className="editor" onSubmit={submit}><div className="form-main"><label>Experiment name<input value={name} onChange={event => setName(event.target.value)} required maxLength={120} /></label><label>Description<input value={description} onChange={event => setDescription(event.target.value)} maxLength={2000} placeholder="Optional context for this experiment" /></label><label>Task template<select value="synthetic_benchmark" disabled><option value="synthetic_benchmark">Synthetic Benchmark</option></select><small>Approved Python workload writes repeatable accuracy and loss metrics.</small></label><label>Execution backend<select value={backend} onChange={event => setBackend(event.target.value as 'local' | 'slurm')} disabled={isEdit}><option value="local">Local worker</option><option value="slurm">Slurm cluster</option></select><small>Slurm requires an authorized worker and shared artifact storage.</small></label>{backend === 'slurm' && <label>Slurm resources<textarea rows={4} spellCheck={false} value={resources} onChange={event => setResources(event.target.value)} /><small>Optional JSON: cpus_per_task, memory_mb, time_limit_minutes, partition, account.</small></label>}<label>Parameter sweep <span className="inline-count">{count || 0} runs · max 50</span><textarea rows={8} spellCheck={false} value={grid} onChange={event => setGrid(event.target.value)} /><small>JSON object where each approved parameter maps to numeric values.</small></label><ErrorBox error={error} /><div className="button-row"><button className="button" type="button" onClick={() => { const value = parse(); if (value) setPreview(Object.entries(value.sweep).reduce<Record<string, number>[]>((rows, [key, values]) => rows.flatMap(row => values.map(v => ({ ...row, [key]: v }))), [{}])) }}>Preview combinations</button><button className="button primary" disabled={save.isPending}>{isEdit ? 'Save changes' : 'Create experiment'}</button></div></div><aside className="editor-aside"><span className="eyebrow">SWEEP PREVIEW</span><div className="preview-count">{preview ? preview.length : (count || 0)}<small>planned runs</small></div>{preview?.slice(0, 8).map((item, index) => <div className="preview-row" key={index}><span>RUN {String(index + 1).padStart(2, '0')}</span>{Object.entries(item).map(([key, value]) => <b key={key}>{key}={value}</b>)}</div>)}{preview && preview.length > 8 && <small>+{preview.length - 8} more combinations</small>}</aside></form></>
}

function RunTable({runs}:{runs:Run[]}) {if(!runs.length)return <div className="empty">No runs yet. Launch an experiment to see activity here.</div>;return <div className="table-wrap"><table><thead><tr><th>RUN</th><th>STATUS</th><th>BACKEND</th><th>PARAMETERS</th><th>STARTED</th><th>RESULT</th></tr></thead><tbody>{runs.map(run=><tr key={run.id}><td><Link to={`/runs/${run.id}`} className="run-link">{run.id.slice(0,8)}</Link></td><td><State value={run.status}/></td><td><span className="backend">{run.backend}</span></td><td className="params">{Object.entries(run.parameters).map(([k,v])=><span key={k}>{k}: <b>{v}</b></span>)}</td><td>{time(run.started_at)}</td><td>{run.metrics.find(m=>m.name==='accuracy')?.value.toFixed(3)??'—'}</td></tr>)}</tbody></table></div>}

function ExperimentPage() {
  const { id = '' } = useParams()
  const cache = useQueryClient()
  const nav = useNavigate()
  const exp = useQuery({ queryKey: ['experiment', id], queryFn: () => api.experiment(id) })
  const runs = useRuns(id)
  const comparison = useQuery({ queryKey: ['comparison', id], queryFn: () => api.comparison(id), refetchInterval: () => runs.data?.some(run => active.includes(run.status)) ? 5000 : false })
  const [metric, setMetric] = useState('accuracy')
  const [xParameter, setXParameter] = useState('')
  const [statusFilter, setStatusFilter] = useState('ALL')
  const launch = useMutation({ mutationFn: () => api.launch(id), onSuccess: async () => { await Promise.all([cache.invalidateQueries({ queryKey: ['runs', id] }), cache.invalidateQueries({ queryKey: ['comparison', id] })]) } })
  const cancel = useMutation({ mutationFn: api.cancel, onSuccess: async () => { await cache.invalidateQueries({ queryKey: ['runs', id] }) } })
  const remove = useMutation({ mutationFn: () => api.deleteExperiment(id), onSuccess: async () => { await cache.invalidateQueries({ queryKey: ['experiments'] }); nav('/experiments') } })
  if (exp.isLoading) return <div className="empty">Loading experiment…</div>
  if (exp.error || !exp.data) return <ErrorBox error={exp.error ?? new Error('Experiment not found')} />
  const parameterNames = Object.keys(exp.data.sweep_spec)
  const xKey = parameterNames.includes(xParameter) ? xParameter : parameterNames[0]
  const successfulRuns = (comparison.data?.runs ?? []).filter(run => run.status === 'SUCCEEDED')
    .map(run => ({ run, x: run.parameters[xKey], value: run.metrics.find(item => item.name === metric)?.value }))
    .filter((item): item is { run: Run; x: number; value: number } => typeof item.x === 'number' && typeof item.value === 'number' && Number.isFinite(item.value))
  const grouped = new Map<number, number[]>()
  successfulRuns.forEach(point => grouped.set(point.x, [...(grouped.get(point.x) ?? []), point.value]))
  const chart = [...grouped.entries()].sort(([left], [right]) => left - right).map(([x, values]) => ({ x, value: values.reduce((sum, value) => sum + value, 0) / values.length, runs: values.length }))
  const visibleRuns = (runs.data ?? []).filter(run => statusFilter === 'ALL' || run.status === statusFilter)
  return <><div className="section-head"><div><span className="eyebrow">{exp.data.backend.toUpperCase()} · SYNTHETIC BENCHMARK</span><h2>{exp.data.name}</h2><p className="muted">{exp.data.description || 'Parameter sweep experiment'} · Created {time(exp.data.created_at)}</p></div><div className="button-row"><Link className="button" to={`/experiments/${id}/edit`}>Edit definition</Link><button className="button" disabled={remove.isPending || runs.isLoading || (runs.data ?? []).some(run => active.includes(run.status))} onClick={() => { if (window.confirm('Delete this completed experiment and its run artifacts?')) remove.mutate() }}>Delete</button><button className="button primary" disabled={launch.isPending} onClick={() => launch.mutate()}>▶ Launch sweep</button></div></div><ErrorBox error={launch.error || cancel.error || remove.error || comparison.error || runs.error} /><div className="stats compact">{['QUEUED', 'RUNNING', 'SUCCEEDED', 'FAILED'].map(state => <div className="stat" key={state}><span>{state}</span><strong>{(runs.data ?? []).filter(run => run.status === state).length}</strong></div>)}</div><div className="section-head"><div><span className="eyebrow">RUN HISTORY</span><h2>Runs <small className="count">{runs.data?.length ?? 0}</small></h2></div><label className="select-label">Status <select value={statusFilter} onChange={event => setStatusFilter(event.target.value)}><option value="ALL">All statuses</option>{['QUEUED', 'PENDING', 'RUNNING', 'SUCCEEDED', 'FAILED', 'CANCELLING', 'CANCELLED', 'UNKNOWN', 'LOST'].map(state => <option key={state}>{state}</option>)}</select></label></div><RunTable runs={visibleRuns} />{visibleRuns.some(run => active.includes(run.status)) && <div className="cancel-list">{visibleRuns.filter(run => active.includes(run.status)).map(run => <button className="button small" key={run.id} onClick={() => { if (window.confirm('Request cancellation for this run?')) cancel.mutate(run.id) }}>Cancel {run.id.slice(0, 8)}</button>)}</div>}<div className="compare"><div className="section-head"><div><span className="eyebrow">RESULTS</span><h2>Metric comparison</h2></div><div className="chart-selectors"><label className="select-label">X-axis <select value={xKey} onChange={event => setXParameter(event.target.value)}>{parameterNames.map(name => <option key={name}>{name}</option>)}</select></label><label className="select-label">Metric <select value={metric} onChange={event => setMetric(event.target.value)}>{(comparison.data?.metrics ?? []).map(name => <option key={name}>{name}</option>)}</select></label></div></div>{chart.length ? <><p className="muted">Mean of successful runs at each {xKey} value. Failed runs and missing metrics are omitted.</p><div className="chart"><ResponsiveContainer width="100%" height="100%"><LineChart data={chart}><CartesianGrid strokeDasharray="3 3" stroke="#e7ebf1" /><XAxis dataKey="x" label={{ value: xKey, position: 'insideBottom', offset: -5 }} /><YAxis /><Tooltip /><Line type="monotone" dataKey="value" name={`Mean ${metric}`} stroke="#5965e8" strokeWidth={3} dot={{ r: 5 }} /></LineChart></ResponsiveContainer></div><div className="comparison-table"><strong>Run-level values</strong>{successfulRuns.map(({ run, x, value }) => <div key={run.id}><span>{xKey} {x} · seed {run.parameters.seed ?? '—'}</span><b>{value.toFixed(5)}</b></div>)}</div></> : <div className="empty">Numeric results will appear after successful runs finish.</div>}</div></>
}

function RunPage() {
  const { id = '' } = useParams()
  const [stream, setStream] = useState<'stdout' | 'stderr'>('stdout')
  const cache = useQueryClient()
  const run = useQuery({ queryKey: ['run', id], queryFn: () => api.run(id), refetchInterval: q => q.state.data && active.includes(q.state.data.status) ? 3000 : false })
  const logs = useQuery({ queryKey: ['logs', id, stream], queryFn: () => api.logs(id, stream), refetchInterval: () => run.data && active.includes(run.data.status) ? 5000 : false })
  const events = useQuery({ queryKey: ['events', id], queryFn: () => api.events(id) })
  const cancel = useMutation({ mutationFn: api.cancel, onSuccess: async () => { await cache.invalidateQueries({ queryKey: ['run', id] }) } })
  if (run.isLoading) return <div className="empty">Loading run…</div>
  if (run.error || !run.data) return <ErrorBox error={run.error ?? new Error('Run not found')} />
  const r = run.data
  return <><Link to={`/experiments/${r.experiment_id}`} className="back-link">← Back to experiment</Link><div className="section-head"><div><span className="eyebrow">RUN DETAIL</span><h2>{r.id.slice(0, 8)} <State value={r.status} /></h2><p className="muted">{r.backend} executor · submitted {time(r.submitted_at)}</p></div>{active.includes(r.status) && <button className="button" onClick={() => { if (window.confirm('Request cancellation?')) cancel.mutate(id) }}>Cancel run</button>}</div><ErrorBox error={cancel.error} /><div className="detail-grid"><section className="panel"><h3>Parameters</h3>{Object.entries(r.parameters).map(([key, value]) => <div className="detail-row" key={key}><span>{key}</span><b>{value}</b></div>)}<h3>Job information</h3><div className="detail-row"><span>Backend ID</span><b>{r.external_job_id ?? 'Awaiting submission'}</b></div><div className="detail-row"><span>Exit code</span><b>{r.exit_code ?? '—'}</b></div><div className="detail-row"><span>Created</span><b>{time(r.created_at)}</b></div><div className="detail-row"><span>Started</span><b>{time(r.started_at)}</b></div><div className="detail-row"><span>Finished</span><b>{time(r.finished_at)}</b></div>{r.error_message && <p className="error">{r.error_message}</p>}</section><section className="panel"><h3>Metrics</h3>{r.metrics.length ? r.metrics.map(m => <div className="detail-row" key={m.name}><span>{m.name}</span><b>{m.value}</b></div>) : <p className="muted">No metrics recorded.</p>}<h3>Output logs</h3><div className="button-row log-tabs"><button className={`button small ${stream === 'stdout' ? 'primary' : ''}`} onClick={() => setStream('stdout')}>stdout</button><button className={`button small ${stream === 'stderr' ? 'primary' : ''}`} onClick={() => setStream('stderr')}>stderr</button></div><pre className="logs">{logs.data?.content || `No ${stream} output captured yet.`}</pre><ErrorBox error={logs.error} /></section><section className="panel"><h3>Status history</h3>{events.data?.map(e => <div className="event-row" key={e.id}><span className="event-dot" /><div><State value={e.new_status} /><p>{e.message}</p><small>{time(e.created_at)}</small></div></div>)}</section></div></>
}
