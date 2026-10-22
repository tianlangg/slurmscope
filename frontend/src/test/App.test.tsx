import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import App from '../pages/App'

const experiment={id:'e1',name:'optimizer sweep',description:null,template_key:'synthetic_benchmark',backend:'local',sweep_spec:{learning_rate:[0.1],seed:[1]},resource_spec:{},created_at:'2026-10-21T10:00:00Z',updated_at:'2026-10-21T10:00:00Z'}
const run={id:'r1',experiment_id:'e1',parameters:{seed:1},backend:'local',status:'UNKNOWN',external_job_id:null,error_message:null,created_at:'2026-10-21T10:00:00Z',submitted_at:null,started_at:null,finished_at:null,metrics:[]}
function mount(path='/'){const queryClient=new QueryClient({defaultOptions:{queries:{retry:false}}});return render(<QueryClientProvider client={queryClient}><MemoryRouter initialEntries={[path]}><App/></MemoryRouter></QueryClientProvider>)}

describe('SlurmScope dashboard',()=>{
  beforeEach(()=>{vi.restoreAllMocks();vi.unstubAllGlobals()})
  it('shows returned run states and keeps UNKNOWN visible',async()=>{
    vi.stubGlobal('fetch',vi.fn().mockImplementation((url:string)=>Promise.resolve({ok:true,status:200,json:async()=>url.includes('/experiments')?[experiment]:[run]})))
    mount()
    expect(await screen.findByText('UNKNOWN')).toBeInTheDocument()
  })
  it('shows API failures instead of presenting an empty dashboard',async()=>{
    vi.stubGlobal('fetch',vi.fn().mockResolvedValue({ok:false,status:503,json:async()=>({detail:'Database unavailable'})}))
    mount()
    expect(await screen.findAllByRole('alert')).not.toHaveLength(0)
    expect(screen.getAllByText('Database unavailable').length).toBeGreaterThan(0)
  })
  it('previews cartesian parameter combinations and reports invalid JSON',async()=>{
    vi.stubGlobal('fetch',vi.fn().mockResolvedValue({ok:true,status:200,json:async()=>[]}))
    mount('/experiments/new')
    expect(screen.getByText('planned runs').parentElement).toHaveTextContent('6')
    fireEvent.change(screen.getByLabelText(/Parameter sweep/),{target:{value:'{invalid'}})
    fireEvent.click(screen.getByRole('button',{name:'Preview combinations'}))
    await waitFor(()=>expect(screen.getByText(/Check the experiment settings/)).toBeInTheDocument())
  })
  it('filters run statuses and requests cancellation with confirmation',async()=>{
    const running={...run,id:'r0000001',status:'RUNNING'}
    const failed={...run,id:'r0000002',status:'FAILED'}
    let cancelled=false
    vi.stubGlobal('confirm',vi.fn(()=>true))
    vi.stubGlobal('fetch',vi.fn().mockImplementation((input:string)=>{
      const url=String(input)
      if(url.includes('/api/experiments/e1/runs')) return Promise.resolve({ok:true,status:200,json:async()=>cancelled?[{...running,status:'CANCELLING'},failed]:[running,failed]})
      if(url.includes('/api/experiments/e1/comparison')) return Promise.resolve({ok:true,status:200,json:async()=>({runs:[running,failed],metrics:[]})})
      if(url.includes('/api/experiments/e1')) return Promise.resolve({ok:true,status:200,json:async()=>experiment})
      if(url.includes('/api/runs/r0000001/cancel')) {cancelled=true;return Promise.resolve({ok:true,status:202,json:async()=>({status:'CANCELLING',accepted:true})})}
      return Promise.resolve({ok:true,status:200,json:async()=>[]})
    }))
    mount('/experiments/e1')
    expect(await screen.findByText('r0000001')).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('Status'),{target:{value:'FAILED'}})
    expect(screen.queryByText('r0000001')).not.toBeInTheDocument()
    expect(screen.getByText('r0000002')).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('Status'),{target:{value:'ALL'}})
    fireEvent.click(await screen.findByRole('button',{name:'Cancel r0000001'}))
    await waitFor(()=>expect(fetch).toHaveBeenCalledWith(expect.stringContaining('/api/runs/r0000001/cancel'),expect.any(Object)))
  })
})
