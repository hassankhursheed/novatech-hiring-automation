import { lazy, Suspense, useEffect } from 'react'
import { Link, Navigate, Route, Routes, useLocation } from 'react-router-dom'
import { CareersLayout, HrAccessLayout, StaffLayout } from './components/layout'
import { Loading } from './components/ui'
import { COMPANY } from './lib/config'

const Careers = lazy(() => import('./pages/careers/Careers'))
const Interview = lazy(() => import('./pages/candidate/Interview'))
const Offer = lazy(() => import('./pages/candidate/Offer'))
const Feedback = lazy(() => import('./pages/staff/LinkPages').then((m) => ({ default: m.Feedback })))
const Approval = lazy(() => import('./pages/staff/LinkPages').then((m) => ({ default: m.Approval })))
const Login = lazy(() => import('./pages/staff/Login'))
const Dashboard = lazy(() => import('./pages/staff/Dashboard'))
const Applications = lazy(() => import('./pages/staff/Applications'))
const ApplicationDetail = lazy(() => import('./pages/staff/ApplicationDetail'))
const OnboardingBoard = lazy(() => import('./pages/staff/Operations').then((m) => ({ default: m.OnboardingBoard })))
const ErrorQueue = lazy(() => import('./pages/staff/Operations').then((m) => ({ default: m.ErrorQueue })))

function NotFound() {
  return (
    <div className="py-20 text-center">
      <h1 className="text-2xl font-semibold text-slate-900">Page not found</h1>
      <p className="mt-2 text-sm text-slate-600">If you followed a link from an email, please use the latest one we sent you.</p>
      <Link to="/careers" className="mt-6 inline-block text-sm font-medium text-brand-700 hover:underline">See open positions</Link>
    </div>
  )
}

// Browser tab title per page: candidates see "Careers", staff see where they are in the portal.
const TITLES: [RegExp, string][] = [
  [/^\/careers/, 'Careers'],
  [/^\/hr/, 'HR portal'],
  [/^\/candidate\/interview/, 'Your interview'],
  [/^\/candidate\/offer/, 'Your offer'],
  [/^\/staff\/feedback/, 'Interview scorecard'],
  [/^\/staff\/approval/, 'Offer approval'],
  [/^\/staff\/login/, 'HR portal sign-in'],
  [/^\/staff\/applications\/[^/]+/, 'Application'],
  [/^\/staff\/applications/, 'Applications'],
  [/^\/staff\/onboarding/, 'Onboarding'],
  [/^\/staff\/errors/, 'Automation errors'],
  [/^\/staff\/?$/, 'Dashboard'],
]
const BRAND = COMPANY.split(' ')[0]

function PageTitle() {
  const { pathname } = useLocation()
  useEffect(() => {
    const page = TITLES.find(([pattern]) => pattern.test(pathname))?.[1] ?? 'Page not found'
    const section = /^\/(staff|hr)/.test(pathname) ? `${BRAND} Hiring` : COMPANY
    document.title = `${page} · ${section}`
  }, [pathname])
  return null
}

export default function App() {
  return (
    <Suspense fallback={<Loading />}>
      <PageTitle />
      <Routes>
        {/* Job seekers and candidates: the careers site */}
        <Route index element={<Navigate to="/careers" replace />} />
        <Route element={<CareersLayout />}>
          <Route path="careers" element={<Careers />} />
          <Route path="candidate/interview" element={<Interview />} />
          <Route path="candidate/offer" element={<Offer />} />
          <Route path="*" element={<NotFound />} />
        </Route>
        {/* HR / recruiters / managers: the HR portal */}
        <Route path="hr" element={<Navigate to="/staff" replace />} />
        <Route element={<HrAccessLayout />}>
          <Route path="staff/login" element={<Login />} />
          <Route path="staff/feedback" element={<Feedback />} />
          <Route path="staff/approval" element={<Approval />} />
        </Route>
        <Route path="staff" element={<StaffLayout />}>
          <Route index element={<Dashboard />} />
          <Route path="applications" element={<Applications />} />
          <Route path="applications/:id" element={<ApplicationDetail />} />
          <Route path="onboarding" element={<OnboardingBoard />} />
          <Route path="errors" element={<ErrorQueue />} />
        </Route>
      </Routes>
    </Suspense>
  )
}
