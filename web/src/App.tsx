import { BrowserRouter, Routes, Route } from 'react-router-dom'
import { Watchtower } from './routes/Watchtower'
import { Table } from './routes/Table'

function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Watchtower />} />
        <Route path="/tables/:tableId" element={<Table />} />
      </Routes>
    </BrowserRouter>
  )
}

export default App