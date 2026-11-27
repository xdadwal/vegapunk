import { useAuth } from '../auth/auth-context'
import { Alert } from '../components/Alert'
import { Button } from '../components/Button'
import './auth.css'

export default function LoadError() {
  const { retry } = useAuth()
  return (
    <div className="page-center">
      <div className="page-center-box">
        <Alert
          tone="danger"
          title="Couldn't load Vegapunk"
          action={<Button onClick={() => void retry()}>Retry</Button>}
        >
          The server didn't respond.
        </Alert>
      </div>
    </div>
  )
}
