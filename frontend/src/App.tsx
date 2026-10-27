import { Route, Routes } from 'react-router-dom'
import Layout from './components/Layout'
import Dashboard from './pages/Dashboard'
import NewComparison from './pages/NewComparison'
import Workspace from './pages/Workspace'
import CommonRemovals from './pages/CommonRemovals'

export default function App() {
  return (
    <Layout>
      <Routes>
        <Route path="/" element={<Dashboard />} />
        <Route path="/new" element={<NewComparison />} />
        <Route path="/comparisons/:id" element={<Workspace />} />
        <Route path="/common-removals" element={<CommonRemovals />} />
        <Route path="*" element={<p className="p-10 text-muted">Page not found.</p>} />
      </Routes>
    </Layout>
  )
}
