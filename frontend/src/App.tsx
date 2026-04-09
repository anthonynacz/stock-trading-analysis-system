import { BrowserRouter, Routes, Route } from 'react-router-dom';
import { ResearchProvider } from './contexts/ResearchContext';
import AppNav from './components/AppNav';
import Dashboard from './pages/Dashboard';
import UniversePage from './pages/UniversePage';
import ResearchPage from './pages/ResearchPage';

function App() {
  return (
    <BrowserRouter>
      <ResearchProvider>
        <AppNav />
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/universe" element={<UniversePage />} />
          <Route path="/research" element={<ResearchPage />} />
        </Routes>
      </ResearchProvider>
    </BrowserRouter>
  );
}

export default App;
