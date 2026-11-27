import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router'

import './styles/tokens.css'
import './styles/components.css'
import './styles/base.css'
import App from './App.tsx'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <div className="vp-root">
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </div>
  </StrictMode>,
)
