# NovaTech portal (React + TypeScript)

One single-page app for everyone who is not an automation. The start page links to **Careers** (job seekers) and the
**Staff portal** (HR, recruiters, managers).

| Who | Pages | How they get in |
|---|---|---|
| Applicants | `/careers`: open positions and the application form (CV upload, then intake with an `Idempotency-Key`) | public |
| Candidates | `/candidate/interview` (pick or cancel a slot), `/candidate/offer` (terms, PDF, accept / decline / negotiate) | signed link from their email |
| Interviewers, approvers | `/staff/feedback` (scorecard), `/staff/approval` (approve or reject one level) | signed link from their email |
| HR and managers | `/staff`: dashboard and work queues, applications with full history and actions, onboarding board, automation error queue with replay | email sign-in link, or email + demo password when `STAFF_DEMO_PASSWORD` is set (demo installations only) |

Link tokens arrive in the URL fragment (`#token=…`), are moved to `sessionStorage` and removed from the address bar
on load, and are sent to the API as `Authorization: Bearer`. The staff session (8 hours) is per browser tab.

## Develop

```bash
npm install
npm run dev        # http://localhost:5173 (the backend allows this origin)
npm run build      # type-check + production bundle in dist/
```

Configuration (build time): `VITE_API_URL` (default `http://localhost:8000`) and `VITE_INTAKE_URL` (default
`http://localhost:5678/webhook/applications`).

## Run in Docker

`docker compose up -d --build portal` serves the production bundle from unprivileged nginx on
`http://localhost:5173` with a Content-Security-Policy (`PORTAL_CONNECT_SRC` lists the API and intake origins),
`no-referrer`, `nosniff` and frame denial. Stop `npm run dev` first; both use port 5173.

## Structure

```
src/lib/        api client (problem+json errors), link tokens and staff session, formatting, staff data hooks
src/components/ layout shells (public, staff) and UI primitives
src/pages/      careers, candidate link pages, staff link pages, staff portal (dashboard, applications, operations)
```
