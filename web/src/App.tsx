import { BrowserRouter, Routes, Route } from 'react-router-dom'
import { AuthProvider } from './auth/AuthContext'
import { AuthConnector } from './api/AuthConnector'
import { Watchtower } from './routes/Watchtower'
import { Table } from './routes/Table'

function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <AuthConnector />
        <Routes>
          <Route path="/" element={<Watchtower />} />
          <Route path="/tables/:tableId" element={<Table />} />
        </Routes>
      </BrowserRouter>
    </AuthProvider>
  )
}

export default App