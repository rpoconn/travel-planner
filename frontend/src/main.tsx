import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'

import { App } from '@/app/App'
import { configureApiClient } from '@/lib/apiClient'

import './index.css'

configureApiClient()

const rootElement = document.getElementById('root')
if (!rootElement) {
    throw new Error('Root element #root not found in index.html')
}

createRoot(rootElement).render(
    <StrictMode>
        <App />
    </StrictMode>,
)
