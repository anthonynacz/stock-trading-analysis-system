import { BrowserRouter, Routes, Route } from 'react-router-dom';
import { ResearchProvider } from './contexts/ResearchContext';
import { OptionsLabProvider } from './contexts/OptionsLabContext';
import AppNav from './components/AppNav';
import Dashboard from './pages/Dashboard';
import UniversePage from './pages/UniversePage';
import ResearchPage from './pages/ResearchPage';
import KnowledgePage from './pages/KnowledgePage';
import PositionsPage from './pages/PositionsPage';
import OptionsLabPage from './pages/OptionsLabPage';
import ScannerPage from './pages/ScannerPage';
import IndustriesPage from './pages/IndustriesPage';
import ChartsPage from './pages/ChartsPage';

function App() {
  return (
    <BrowserRouter>
      <ResearchProvider>
        <OptionsLabProvider>
          <AppNav />
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/universe" element={<UniversePage />} />
            <Route path="/research" element={<ResearchPage />} />
            <Route path="/options-lab" element={<OptionsLabPage />} />
            <Route path="/scanner" element={<ScannerPage />} />
            <Route path="/industries" element={<IndustriesPage />} />
            <Route path="/charts" element={<ChartsPage />} />
            <Route path="/positions" element={<PositionsPage />} />
            <Route path="/knowledge" element={<KnowledgePage />} />
          </Routes>
        </OptionsLabProvider>
      </ResearchProvider>
    </BrowserRouter>
  );
}

export default App;
