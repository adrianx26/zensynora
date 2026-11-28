import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import { reportCrash } from './crashReport'

createRoot(document.getElementById('root')!, {
  onUncaughtError: (error, info) => {
    console.error(error);
    reportCrash(error, info.componentStack || '');
  },
}).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
