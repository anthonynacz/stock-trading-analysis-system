import { BrowserRouter, Routes, Route } from 'react-router-dom';
import { AuthProvider } from './contexts/AuthContext';
import { EntitlementsProvider } from './contexts/EntitlementsContext';
import { ResearchProvider } from './contexts/ResearchContext';
import { OptionsLabProvider } from './contexts/OptionsLabContext';
import AppNav from './components/AppNav';
import RequireAuth from './components/RequireAuth';
import Dashboard from './pages/Dashboard';
import UniversePage from './pages/UniversePage';
import ResearchPage from './pages/ResearchPage';
import KnowledgePage from './pages/KnowledgePage';
import PositionsPage from './pages/PositionsPage';
import OptionsLabPage from './pages/OptionsLabPage';
import ScannerPage from './pages/ScannerPage';
import IndustriesPage from './pages/IndustriesPage';
import ChartsPage from './pages/ChartsPage';
import LoginPage from './pages/LoginPage';
import SettingsPage from './pages/SettingsPage';
import SchedulePage from './pages/SchedulePage';

function ProtectedShell({ children }: { children: React.ReactNode }) {
  return (
    <RequireAuth>
      <AppNav />
      {children}
    </RequireAuth>
  );
}

function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <EntitlementsProvider>
          <ResearchProvider>
            <OptionsLabProvider>
            <Routes>
              <Route path="/login" element={<LoginPage />} />
              <Route path="/" element={<ProtectedShell><Dashboard /></ProtectedShell>} />
              <Route path="/universe" element={<ProtectedShell><UniversePage /></ProtectedShell>} />
              <Route path="/research" element={<ProtectedShell><ResearchPage /></ProtectedShell>} />
              <Route path="/options-lab" element={<ProtectedShell><OptionsLabPage /></ProtectedShell>} />
              <Route path="/scanner" element={<ProtectedShell><ScannerPage /></ProtectedShell>} />
              <Route path="/industries" element={<ProtectedShell><IndustriesPage /></ProtectedShell>} />
              <Route path="/charts" element={<ProtectedShell><ChartsPage /></ProtectedShell>} />
              <Route path="/positions" element={<ProtectedShell><PositionsPage /></ProtectedShell>} />
              <Route path="/knowledge" element={<ProtectedShell><KnowledgePage /></ProtectedShell>} />
              <Route path="/schedule" element={<ProtectedShell><SchedulePage /></ProtectedShell>} />
              <Route path="/settings" element={<ProtectedShell><SettingsPage /></ProtectedShell>} />
            </Routes>
            </OptionsLabProvider>
          </ResearchProvider>
        </EntitlementsProvider>
      </AuthProvider>
    </BrowserRouter>
  );
}

export default App;
