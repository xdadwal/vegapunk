import { Navigate, Route, Routes } from 'react-router'

import { AuthProvider } from './auth/AuthProvider'
import { useAuth } from './auth/auth-context'
import { AppLayout } from './components/AppLayout'
import { Spinner } from './components/Spinner'
import ContainerDetail from './pages/ContainerDetail'
import Agent from './pages/Agent'
import Containers from './pages/Containers'
import FindingDetail from './pages/FindingDetail'
import Findings from './pages/Findings'
import History from './pages/History'
import LoadError from './pages/LoadError'
import Overview from './pages/Overview'
import Profile from './pages/Profile'
import SignIn from './pages/SignIn'
import Setup from './pages/Setup'
import SetupAi from './pages/SetupAi'
import Settings from './pages/Settings'

function AppRoutes() {
  const { state } = useAuth()

  switch (state.status) {
    case 'loading':
      return (
        <div className="page-center" role="status">
          <Spinner size={20} />
          &nbsp;Loading…
        </div>
      )
    case 'error':
      return <LoadError />
    case 'needs-setup':
      return (
        <Routes>
          <Route path="/setup" element={<Setup />} />
          <Route path="*" element={<Navigate to="/setup" replace />} />
        </Routes>
      )
    case 'signed-out':
      return (
        <Routes>
          <Route path="/sign-in" element={<SignIn />} />
          <Route path="*" element={<Navigate to="/sign-in" replace />} />
        </Routes>
      )
    case 'signed-in':
      return (
        <Routes>
          <Route element={<AppLayout />}>
            <Route path="/" element={<Overview />} />
            <Route path="/agent" element={<Agent />} />
            <Route path="/agent/history" element={<History />} />
            <Route path="/containers" element={<Containers />} />
            <Route path="/containers/:name" element={<ContainerDetail />} />
            <Route path="/findings" element={<Findings />} />
            <Route path="/findings/:id" element={<FindingDetail />} />
            <Route path="/profile" element={<Profile />} />
            <Route path="/settings" element={<Settings />} />
          </Route>
          <Route path="/setup/ai" element={<SetupAi />} />
          {/* Right after the account is created the person is still on /setup: step 2 is next. */}
          <Route path="/setup" element={<Navigate to="/setup/ai" replace />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      )
  }
}

export default function App() {
  return (
    <AuthProvider>
      <AppRoutes />
    </AuthProvider>
  )
}
