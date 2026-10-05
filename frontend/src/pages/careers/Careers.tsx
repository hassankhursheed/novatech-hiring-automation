import { useQuery } from '@tanstack/react-query'
import { useRef, useState, type FormEvent } from 'react'
import { Alert, Button, Card, Field, Input, Loading, Select, Textarea } from '../../components/ui'
import { api, ApiError, explain, request } from '../../lib/api'
import { COMPANY, INTAKE_URL } from '../../lib/config'
import { humanize } from '../../lib/format'

interface Position {
  code: string
  title: string
  department: string
  employment_type: string
  min_experience_years: number
  description: string | null
}

interface Submitted {
  correlation_id: string
  message?: string
}

const MAX_MB = 5

export default function Careers() {
  const positions = useQuery({ queryKey: ['positions'], queryFn: () => api<Position[]>('/v1/public/positions') })
  const [selected, setSelected] = useState<string>('')
  const formRef = useRef<HTMLFormElement>(null)

  return (
    <div className="space-y-10">
      <section>
        <h1 className="text-3xl font-semibold text-slate-900">Work with {COMPANY}</h1>
        <p className="mt-2 max-w-2xl text-slate-600">
          We build software for businesses across Pakistan and beyond. Every application is read: our team reviews it,
          and you will hear from us by email at each step.
        </p>
      </section>

      <section className="grid gap-4 md:grid-cols-3">
        {positions.isLoading && <Loading label="Loading open positions" />}
        {positions.error && <Alert tone="error">{explain(positions.error)}</Alert>}
        {positions.data?.map((p) => (
          <Card key={p.code} className="flex flex-col">
            <p className="text-xs font-medium uppercase tracking-wide text-brand-700">{p.department}</p>
            <h2 className="mt-1 text-lg font-semibold text-slate-900">{p.title}</h2>
            <p className="mt-2 text-sm text-slate-600">{p.description}</p>
            <p className="mt-3 text-xs text-slate-500">
              {humanize(p.employment_type)} · {p.min_experience_years}+ year(s) of experience
            </p>
            <Button
              variant="secondary"
              className="mt-4 w-full"
              onClick={() => {
                setSelected(p.code)
                formRef.current?.scrollIntoView({ behavior: 'smooth' })
              }}
            >
              Apply
            </Button>
          </Card>
        ))}
      </section>

      <ApplicationForm formRef={formRef} positions={positions.data ?? []} selected={selected} onSelect={setSelected} />
    </div>
  )
}

function ApplicationForm({ formRef, positions, selected, onSelect }: {
  formRef: React.RefObject<HTMLFormElement | null>
  positions: Position[]
  selected: string
  onSelect: (code: string) => void
}) {
  // One key per filled-in form: a retry after a timeout reuses it, so the application can never be stored twice.
  const idempotencyKey = useRef(crypto.randomUUID())
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [done, setDone] = useState<Submitted | null>(null)

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setError(null)
    const form = new FormData(event.currentTarget)
    const cv = form.get('cv') as File | null
    if (cv && cv.size > MAX_MB * 1024 * 1024) {
      setError(`Your CV is larger than ${MAX_MB} MB. Please upload a smaller PDF or Word file.`)
      return
    }
    setBusy(true)
    try {
      let cvRef: string | undefined
      if (cv && cv.size > 0) {
        const upload = new FormData()
        upload.append('file', cv)
        cvRef = (await api<{ cv_ref: string }>('/v1/public/cv', { body: upload })).cv_ref
      }
      const text = (name: string) => String(form.get(name) ?? '').trim() || undefined
      const body = {
        full_name: text('full_name'),
        email: text('email'),
        phone: text('phone'),
        position: text('position'),
        experience_years: text('experience_years'),
        skills: text('skills')?.split(',').map((s) => s.trim()).filter(Boolean),
        expected_salary: text('expected_salary'),
        available_from: text('available_from'),
        current_title: text('current_title'),
        current_company: text('current_company'),
        city: text('city'),
        linkedin_url: text('linkedin_url'),
        cover_letter: text('cover_letter'),
        cv_ref: cvRef,
        consent: form.get('consent') === 'on',
      }
      const result = await request<Submitted>(INTAKE_URL, { body, headers: { 'Idempotency-Key': idempotencyKey.current } })
      setDone(result)
    } catch (err) {
      setError(err instanceof ApiError && err.code === 'IDEMPOTENCY_KEY_REUSED'
        ? 'This form was already submitted with different details. Please reload the page to start a new application.'
        : explain(err))
    } finally {
      setBusy(false)
    }
  }

  if (done) {
    return (
      <Card title="Application received">
        <Alert tone="success" title="Thank you for applying!">
          Your reference is <span className="font-mono font-semibold">{done.correlation_id}</span>. A confirmation email is on
          its way, and we will contact you about the next steps.
        </Alert>
      </Card>
    )
  }

  return (
    <Card title="Apply">
      <form id="apply" ref={formRef} onSubmit={submit} className="grid scroll-mt-24 gap-5 md:grid-cols-2" noValidate={false}>
        <Field label="Position" required>
          <Select name="position" required value={selected} onChange={(e) => onSelect(e.target.value)}>
            <option value="" disabled>Choose a position</option>
            {positions.map((p) => <option key={p.code} value={p.code}>{p.title}</option>)}
          </Select>
        </Field>
        <Field label="Full name" required><Input name="full_name" required autoComplete="name" maxLength={120} /></Field>
        <Field label="Email" required><Input name="email" type="email" required autoComplete="email" /></Field>
        <Field label="Phone" hint="e.g. 0300 1234567"><Input name="phone" type="tel" autoComplete="tel" /></Field>
        <Field label="Years of experience" required><Input name="experience_years" inputMode="decimal" required placeholder="e.g. 3" /></Field>
        <Field label="Expected monthly salary (PKR)" required hint="e.g. 180000, 180k or 1.8 lakh"><Input name="expected_salary" required /></Field>
        <Field label="Available from" required><Input name="available_from" type="date" required /></Field>
        <Field label="Current job title"><Input name="current_title" /></Field>
        <Field label="Current company"><Input name="current_company" /></Field>
        <Field label="City"><Input name="city" autoComplete="address-level2" /></Field>
        <div className="md:col-span-2">
          <Field label="Key skills" required hint="Comma-separated, e.g. Python, FastAPI, PostgreSQL">
            <Input name="skills" required />
          </Field>
        </div>
        <div className="md:col-span-2">
          <Field label="LinkedIn profile"><Input name="linkedin_url" type="url" placeholder="https://www.linkedin.com/in/…" /></Field>
        </div>
        <div className="md:col-span-2">
          <Field label="Cover letter" hint="A few sentences about why this role fits you."><Textarea name="cover_letter" maxLength={5000} /></Field>
        </div>
        <div className="md:col-span-2">
          <Field label="CV" required hint={`PDF or Word (.docx), up to ${MAX_MB} MB`}>
            <Input name="cv" type="file" required accept=".pdf,.docx,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document" />
          </Field>
        </div>
        <label className="flex items-start gap-3 text-sm text-slate-700 md:col-span-2">
          <input name="consent" type="checkbox" required className="mt-1 h-4 w-4 rounded border-slate-300" />
          <span>I agree that {COMPANY} may process the information in this application, including my CV, for this recruitment.</span>
        </label>
        {error && <div className="md:col-span-2"><Alert tone="error">{error}</Alert></div>}
        <div className="md:col-span-2">
          <Button type="submit" busy={busy} className="w-full md:w-auto">Submit application</Button>
        </div>
      </form>
    </Card>
  )
}
