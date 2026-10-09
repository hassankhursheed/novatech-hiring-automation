import { useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { PageTitle } from '../../components/layout'
import { Alert, Card, Empty, Input, Loading, OpenButton, Select, StatusBadge } from '../../components/ui'
import { explain } from '../../lib/api'
import { formatScore, formatShort, humanize } from '../../lib/format'
import { useStaffQuery, type ApplicationRow } from '../../lib/staff'

const STATUSES = [
  'SCREENING_REVIEW', 'SHORTLISTED', 'INTERVIEW_SCHEDULED', 'INTERVIEWED', 'INTERVIEW_REVIEW', 'SELECTED',
  'OFFER_PENDING_APPROVAL', 'OFFERED', 'NEGOTIATION', 'ACCEPTED', 'ONBOARDING', 'ONBOARDED', 'REJECTED', 'DECLINED',
  'OFFER_EXPIRED', 'WITHDRAWN', 'VALIDATED', 'SCORED',
]
const PAGE = 50

export default function Applications() {
  const [params, setParams] = useSearchParams()
  const status = params.get('status') ?? ''
  const [search, setSearch] = useState(params.get('search') ?? '')
  const page = Number(params.get('page') ?? 0)
  const query = new URLSearchParams({ limit: String(PAGE), offset: String(page * PAGE) })
  if (status) query.set('status', status)
  if (params.get('search')) query.set('search', params.get('search')!)
  const rows = useStaffQuery<ApplicationRow[]>(['applications', query.toString()], `/applications?${query}`)

  const update = (changes: Record<string, string>) => {
    const next = new URLSearchParams(params)
    for (const [key, value] of Object.entries(changes)) value ? next.set(key, value) : next.delete(key)
    if (!('page' in changes)) next.delete('page')
    setParams(next)
  }

  return (
    <>
      <PageTitle title="Applications" subtitle="Newest activity first. Open an application for its full history and actions." />
      <Card>
        <form className="mb-4 flex flex-wrap gap-3" onSubmit={(e) => { e.preventDefault(); update({ search: search.trim() }) }}>
          <Input className="max-w-xs" placeholder="Search name, email or APP-…" value={search} onChange={(e) => setSearch(e.target.value)} />
          <Select className="max-w-xs" value={status} onChange={(e) => update({ status: e.target.value })}>
            <option value="">All statuses</option>
            {STATUSES.map((s) => <option key={s} value={s}>{humanize(s)}</option>)}
          </Select>
        </form>
        {rows.isLoading ? <Loading /> : rows.error ? <Alert tone="error">{explain(rows.error)}</Alert> : !rows.data?.length ? <Empty>No applications match.</Empty> : (
          <div className="overflow-x-auto">
            <table className="min-w-full text-left text-sm">
              <thead>
                <tr className="border-b border-slate-200 text-xs uppercase text-slate-500">
                  <th className="py-2 pr-4 font-medium">Application</th><th className="py-2 pr-4 font-medium">Candidate</th>
                  <th className="py-2 pr-4 font-medium">Position</th><th className="py-2 pr-4 font-medium">Status</th>
                  <th className="py-2 pr-4 font-medium">Screening</th><th className="py-2 pr-4 font-medium">Final</th>
                  <th className="py-2 pr-4 font-medium">AI (advisory)</th><th className="py-2 pr-4 font-medium">Updated</th>
                  <th className="py-2 font-medium"><span className="sr-only">Open</span></th>
                </tr>
              </thead>
              <tbody>
                {rows.data.map((r) => (
                  <tr key={r.application_id} className="border-b border-slate-100 last:border-0 hover:bg-slate-50">
                    <td className="py-2 pr-4"><Link className="font-medium text-brand-700 hover:underline" to={`/staff/applications/${r.application_id}`}>{r.application_code}</Link></td>
                    <td className="py-2 pr-4"><p className="text-slate-900">{r.full_name}</p><p className="text-xs text-slate-500">{r.email}</p></td>
                    <td className="py-2 pr-4 text-slate-700">{r.position_title}</td>
                    <td className="py-2 pr-4"><StatusBadge status={r.status} /></td>
                    <td className="py-2 pr-4 text-slate-700">{formatScore(r.application_score)}</td>
                    <td className="py-2 pr-4 text-slate-700">{formatScore(r.final_score)}</td>
                    <td className="py-2 pr-4 text-slate-700">{humanize(r.ai_recommendation)}</td>
                    <td className="py-2 pr-4 text-slate-500">{formatShort(r.status_changed_at)}</td>
                    <td className="py-2 text-right"><OpenButton to={`/staff/applications/${r.application_id}`} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <div className="mt-4 flex justify-between text-sm">
          <button className="text-brand-700 disabled:text-slate-300" disabled={page === 0} onClick={() => update({ page: String(page - 1) })}>← Newer</button>
          <button className="text-brand-700 disabled:text-slate-300" disabled={(rows.data?.length ?? 0) < PAGE} onClick={() => update({ page: String(page + 1) })}>Older →</button>
        </div>
      </Card>
    </>
  )
}
