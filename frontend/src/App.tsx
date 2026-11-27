import { Navigate, Route, Routes } from 'react-router'

import { AuthProvider } from './auth/AuthProvider'
import { useAuth } from './auth/auth-context'
import { AppLayout } from './components/AppLayout'
import { Spinner } from './components/Spinner'
import LoadError from './pages/LoadError'
import Overview from './pages/Overview'
import Profile from './pages/Profile'
import SignIn from './pages/SignIn'
import Setup from './pages/Setup'

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
            <Route path="/profile" element={<Profile />} />
          </Route>
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
