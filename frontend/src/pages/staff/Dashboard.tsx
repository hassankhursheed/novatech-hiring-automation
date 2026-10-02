import { useState } from 'react'
import { Link } from 'react-router-dom'
import { PageTitle } from '../../components/layout'
import { Alert, Card, Empty, Loading, StatusBadge, cx } from '../../components/ui'
import { explain } from '../../lib/api'
import { formatDate, formatMoney, formatShort } from '../../lib/format'
import { useStaffSession } from '../../lib/session'
import { useStaffQuery } from '../../lib/staff'

type Row = Record<string, string | number | null>

const QUEUES: { key: string; label: string; columns: [string, string][] }[] = [
  { key: 'screening-review', label: 'Reviews', columns: [['application_code', 'Application'], ['full_name', 'Candidate'], ['position', 'Position'], ['status', 'Status'], ['review_reason', 'Reason'], ['waiting_since', 'Waiting since']] },
  { key: 'offer-approvals', label: 'Offer approvals', columns: [['offer_code', 'Offer'], ['full_name', 'Candidate'], ['position', 'Position'], ['monthly_salary', 'Salary'], ['next_level', 'Level'], ['waiting_since', 'Waiting since']] },
  { key: 'interview-feedback', label: 'Missing feedback', columns: [['interview_code', 'Interview'], ['candidate_name', 'Candidate'], ['interviewer_name', 'Interviewer'], ['scheduled_end', 'Interview ended']] },
  { key: 'open-offers', label: 'Open offers', columns: [['offer_code', 'Offer'], ['full_name', 'Candidate'], ['position', 'Position'], ['status', 'Status'], ['monthly_salary', 'Salary'], ['expires_at', 'Expires']] },
  { key: 'overdue-onboarding', label: 'Overdue onboarding', columns: [['employee_code', 'Employee'], ['employee_name', 'Name'], ['title', 'Task'], ['assignee_name', 'Owner'], ['due_date', 'Due'], ['days_overdue', 'Days overdue']] },
]

const TILES: [string, string, boolean][] = [
  ['pending_screening_reviews', 'Screening reviews', true],
  ['pending_interview_reviews', 'Interview reviews', true],
  ['interview_feedback_pending', 'Feedback pending', true],
  ['offers_pending_approval', 'Offers to approve', true],
  ['offers_awaiting_response', 'Offers out', false],
  ['employees_onboarding', 'Onboarding', false],
  ['overdue_onboarding_tasks', 'Overdue tasks', true],
  ['manual_intervention_required', 'Need a person', true],
  ['applications_received', 'Applications today', false],
  ['shortlisted', 'Shortlisted today', false],
  ['workflow_failures', 'Automation failures today', true],
  ['retries_recovered', 'Recovered by retry', false],
]

function cell(key: string, value: Row[string]) {
  if (value === null || value === undefined || value === '') return '-'
  if (key === 'status') return <StatusBadge status={String(value)} />
  if (key === 'monthly_salary') return formatMoney(Number(value))
  if (key.endsWith('_at') || key === 'waiting_since' || key === 'scheduled_end') return formatShort(String(value))
  if (key.endsWith('_date')) return formatDate(String(value))
  return String(value)
}

export default function Dashboard() {
  const session = useStaffSession()
  const overview = useStaffQuery<Record<string, number>>(['overview'], '/overview', { refetchInterval: 30_000 })
  const pipeline = useStaffQuery<Row[]>(['pipeline'], '/queues/pipeline')
  const [queue, setQueue] = useState(QUEUES[0].key)
  const active = QUEUES.find((q) => q.key === queue)!
  const rows = useStaffQuery<Row[]>(['queue', queue], `/queues/${queue}?limit=100`, { refetchInterval: 30_000 })

  return (
    <>
      <PageTitle title={`Good day, ${session?.staff.full_name.split(' ')[0]}`} subtitle="What needs a person today. Numbers refresh every 30 seconds." />
      {overview.error && <Alert tone="error">{explain(overview.error)}</Alert>}
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4 lg:grid-cols-6">
        {TILES.map(([key, label, warn]) => {
          const value = overview.data?.[key] ?? 0
          return (
            <div key={key} className={cx('rounded-xl border bg-white p-4', warn && value > 0 ? 'border-amber-300 bg-amber-50/40' : 'border-slate-200')}>
              <p className="text-2xl font-semibold text-slate-900">{overview.isLoading ? '…' : value}</p>
              <p className="mt-1 text-xs text-slate-500">{label}</p>
            </div>
          )
        })}
      </div>

      <div className="mt-8 grid gap-6 lg:grid-cols-3">
        <Card title="Work queues" className="lg:col-span-2">
          <div className="-mt-1 mb-4 flex flex-wrap gap-2">
            {QUEUES.map((q) => (
              <button key={q.key} onClick={() => setQueue(q.key)}
                className={cx('rounded-full px-3 py-1 text-xs font-medium', q.key === queue ? 'bg-brand-600 text-white' : 'bg-slate-100 text-slate-700 hover:bg-slate-200')}>
                {q.label}
              </button>
            ))}
          </div>
          {rows.isLoading ? <Loading /> : rows.error ? <Alert tone="error">{explain(rows.error)}</Alert> : !rows.data?.length ? <Empty>Nothing waiting here.</Empty> : (
            <div className="overflow-x-auto">
              <table className="min-w-full text-left text-sm">
                <thead><tr className="border-b border-slate-200 text-xs uppercase text-slate-500">{active.columns.map(([, label]) => <th key={label} className="py-2 pr-4 font-medium">{label}</th>)}</tr></thead>
                <tbody>
                  {rows.data.map((row, i) => (
                    <tr key={i} className="border-b border-slate-100 last:border-0">
                      {active.columns.map(([key]) => (
                        <td key={key} className="max-w-xs truncate py-2 pr-4 text-slate-700">
                          {key === 'application_code' && row.application_id
                            ? <Link className="font-medium text-brand-700 hover:underline" to={`/staff/applications/${row.application_id}`}>{cell(key, row[key])}</Link>
                            : cell(key, row[key])}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>

        <Card title="Pipeline">
          {pipeline.isLoading ? <Loading /> : (
            <ul className="space-y-2">
              {(pipeline.data ?? []).map((row) => (
                <li key={String(row.status)} className="flex items-center justify-between text-sm">
                  <Link to={`/staff/applications?status=${row.status}`}><StatusBadge status={String(row.status)} /></Link>
                  <span className="font-medium text-slate-900">{row.applications ?? '-'}</span>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </>
  )
}
