import { lazy, Suspense } from 'react'
import { Link, Route, Routes } from 'react-router-dom'
import { PublicLayout, StaffLayout } from './components/layout'
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

function Home() {
  return (
    <div className="grid gap-6 md:grid-cols-2">
      <Link to="/careers" className="rounded-xl border border-slate-200 bg-white p-8 shadow-sm hover:border-brand-500">
        <h2 className="text-xl font-semibold text-slate-900">Careers at {COMPANY}</h2>
        <p className="mt-2 text-sm text-slate-600">See open positions and apply in a few minutes.</p>
      </Link>
      <Link to="/staff" className="rounded-xl border border-slate-200 bg-white p-8 shadow-sm hover:border-brand-500">
        <h2 className="text-xl font-semibold text-slate-900">Staff portal</h2>
        <p className="mt-2 text-sm text-slate-600">Reviews, approvals, onboarding and automation health.</p>
      </Link>
    </div>
  )
}

function NotFound() {
  return (
    <div className="py-20 text-center">
      <h1 className="text-2xl font-semibold text-slate-900">Page not found</h1>
      <p className="mt-2 text-sm text-slate-600">If you followed a link from an email, please use the latest one we sent you.</p>
      <Link to="/" className="mt-6 inline-block text-sm font-medium text-brand-700 hover:underline">Go to the start page</Link>
    </div>
  )
}

export default function App() {
  return (
    <Suspense fallback={<Loading />}>
      <Routes>
        <Route element={<PublicLayout />}>
          <Route index element={<Home />} />
          <Route path="careers" element={<Careers />} />
          <Route path="candidate/interview" element={<Interview />} />
          <Route path="candidate/offer" element={<Offer />} />
          <Route path="staff/feedback" element={<Feedback />} />
          <Route path="staff/approval" element={<Approval />} />
          <Route path="staff/login" element={<Login />} />
          <Route path="*" element={<NotFound />} />
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
