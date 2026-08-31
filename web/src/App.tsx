import { useState, type FormEvent } from 'react'
import { BrowserRouter, Routes, Route } from 'react-router-dom'
import { AuthProvider, useAuth } from './auth/AuthContext'
import { AuthConnector } from './api/AuthConnector'
import { Taproom } from './routes/Taproom'
import { Table } from './routes/Table'
import './components/ModeIndicator.css'

function CredentialGate() {
  const { submitCredential } = useAuth()
  const [credential, setCredential] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [isSubmitting, setIsSubmitting] = useState(false)

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault()
    setIsSubmitting(true)
    setError(null)
    const result = await submitCredential(credential)
    if (!result.ok) {
      setError(result.message)
    } else {
      setCredential('')
    }
    setIsSubmitting(false)
  }

  return (
    <main className="credential-gate" aria-labelledby="credential-gate-title">
      <section className="credential-gate-card">
        <h1 id="credential-gate-title">Access required</h1>
        <p>Enter a Viewer or Admin credential to open Tasca.</p>
        <form className="token-form" onSubmit={handleSubmit}>
          <label className="token-label" htmlFor="access-credential">
            Access credential
          </label>
          <input
            id="access-credential"
            className="token-input"
            type="password"
            value={credential}
            onChange={(event) => setCredential(event.target.value)}
            autoComplete="off"
            autoFocus
            aria-describedby={error ? 'access-credential-error' : undefined}
            aria-invalid={error ? true : undefined}
            disabled={isSubmitting}
          />
          {error && (
            <p id="access-credential-error" className="token-error" role="alert">
              {error}
            </p>
          )}
          <button
            type="submit"
            className="token-btn token-btn--submit"
            disabled={!credential.trim() || isSubmitting}
          >
            {isSubmitting ? 'Checking…' : 'Continue'}
          </button>
        </form>
      </section>
    </main>
  )
}

function AccessState() {
  const { status, error, retryDiscovery } = useAuth()

  if (status === 'discovering') {
    return (
      <main className="credential-gate" aria-busy="true">
        <p role="status">Checking access…</p>
      </main>
    )
  }

  if (status === 'error') {
    return (
      <main className="credential-gate">
        <section className="credential-gate-card" role="alert">
          <h1>Unable to check access</h1>
          <p>{error}</p>
          <button type="button" className="token-btn token-btn--submit" onClick={retryDiscovery}>
            Retry
          </button>
        </section>
      </main>
    )
  }

  if (status === 'credential-required') {
    return <CredentialGate />
  }

  return (
    <>
      <AuthConnector />
      <BrowserRouter>
        <Routes>
          <Route path="/" element={<Taproom />} />
          <Route path="/tables/:tableId" element={<Table />} />
        </Routes>
      </BrowserRouter>
    </>
  )
}

function App() {
  return (
    <AuthProvider>
      <AccessState />
    </AuthProvider>
  )
}

export default App
