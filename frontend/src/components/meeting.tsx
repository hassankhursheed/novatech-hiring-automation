import { hasMeeting, validLink, type Meeting, type MeetingValues } from '../lib/meeting'
import { Field, Input, Select, Textarea } from './ui'

export function MeetingFields({ value, onChange }: { value: MeetingValues; onChange: (v: MeetingValues) => void }) {
  const set = (changes: Partial<MeetingValues>) => onChange({ ...value, ...changes })
  const linkError = value.meeting_url.trim() && !validLink(value.meeting_url) ? 'Paste the full link, starting with https://' : undefined
  return (
    <div className="space-y-3">
      <Field label="Interview format">
        <Select value={value.mode} onChange={(e) => set({ mode: e.target.value as MeetingValues['mode'] })}>
          <option value="ONLINE">Online meeting (Zoom, Google Meet, Teams)</option>
          <option value="ONSITE">At the office</option>
        </Select>
      </Field>
      {value.mode === 'ONLINE' ? (
        <>
          <Field label="Meeting link" required error={linkError}>
            <Input inputMode="url" placeholder="https://zoom.us/j/…" value={value.meeting_url} onChange={(e) => set({ meeting_url: e.target.value })} />
          </Field>
          <div className="grid grid-cols-2 gap-2">
            <Field label="Meeting ID"><Input value={value.meeting_id} onChange={(e) => set({ meeting_id: e.target.value })} /></Field>
            <Field label="Passcode"><Input value={value.meeting_passcode} onChange={(e) => set({ meeting_passcode: e.target.value })} /></Field>
          </div>
          <Field label="Other instructions" hint="Optional, e.g. join 5 minutes early.">
            <Textarea rows={2} value={value.meeting_notes} onChange={(e) => set({ meeting_notes: e.target.value })} />
          </Field>
        </>
      ) : (
        <Field label="Address and instructions" required hint="Where to come and whom to ask for.">
          <Textarea rows={3} value={value.meeting_notes} onChange={(e) => set({ meeting_notes: e.target.value })} />
        </Field>
      )}
    </div>
  )
}

/** Read-only meeting details (staff pages, and the candidate's page after booking). */
export function MeetingSummary({ meeting, missing }: { meeting: Partial<Meeting>; missing: string }) {
  if (!hasMeeting(meeting)) return <span className="text-slate-500">{missing}</span>
  if (meeting.mode === 'ONSITE') return <span className="whitespace-pre-line">At the office: {meeting.meeting_notes}</span>
  return (
    <span className="space-y-0.5">
      <a className="block break-all text-brand-700 underline" href={meeting.meeting_url ?? undefined} target="_blank" rel="noreferrer">{meeting.meeting_url}</a>
      {meeting.meeting_id && <span className="block">Meeting ID: <strong>{meeting.meeting_id}</strong></span>}
      {meeting.meeting_passcode && <span className="block">Passcode: <strong>{meeting.meeting_passcode}</strong></span>}
      {meeting.meeting_notes && <span className="block whitespace-pre-line text-slate-600">{meeting.meeting_notes}</span>}
    </span>
  )
}
