import { lazy, Suspense } from 'react';
import { BrowserRouter, Routes, Route, Outlet } from 'react-router-dom';
import { AuthProvider } from './contexts/AuthContext';
import { EntitlementsProvider } from './contexts/EntitlementsContext';
import { ResearchProvider } from './contexts/ResearchContext';
import { OptionsLabProvider } from './contexts/OptionsLabContext';
import AppNav from './components/AppNav';
import RequireAuth from './components/RequireAuth';
import { PageSpinner } from './components/ui/feedback';
import Dashboard from './pages/Dashboard';
import LoginPage from './pages/LoginPage';

// Landing page (Dashboard) and LoginPage stay eager; everything else is its own chunk.
const UniversePage = lazy(() => import('./pages/UniversePage'));
const ResearchPage = lazy(() => import('./pages/ResearchPage'));
const KnowledgePage = lazy(() => import('./pages/KnowledgePage'));
const PositionsPage = lazy(() => import('./pages/PositionsPage'));
const OptionsLabPage = lazy(() => import('./pages/OptionsLabPage'));
const ScannerPage = lazy(() => import('./pages/ScannerPage'));
const IndustriesPage = lazy(() => import('./pages/IndustriesPage'));
const ChartsPage = lazy(() => import('./pages/ChartsPage'));
const SettingsPage = lazy(() => import('./pages/SettingsPage'));
const SchedulePage = lazy(() => import('./pages/SchedulePage'));
const PerformancePage = lazy(() => import('./pages/PerformancePage'));

function ProtectedLayout() {
  return (
    <RequireAuth>
      <div className="min-h-screen bg-page text-text-primary">
        <AppNav />
        <Suspense fallback={<PageSpinner />}>
          <Outlet />
        </Suspense>
      </div>
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
              <Route element={<ProtectedLayout />}>
                <Route path="/" element={<Dashboard />} />
                <Route path="/universe" element={<UniversePage />} />
                <Route path="/research" element={<ResearchPage />} />
                <Route path="/options-lab" element={<OptionsLabPage />} />
                <Route path="/scanner" element={<ScannerPage />} />
                <Route path="/industries" element={<IndustriesPage />} />
                <Route path="/charts" element={<ChartsPage />} />
                <Route path="/positions" element={<PositionsPage />} />
                <Route path="/performance" element={<PerformancePage />} />
                <Route path="/knowledge" element={<KnowledgePage />} />
                <Route path="/schedule" element={<SchedulePage />} />
                <Route path="/settings" element={<SettingsPage />} />
              </Route>
            </Routes>
            </OptionsLabProvider>
          </ResearchProvider>
        </EntitlementsProvider>
      </AuthProvider>
    </BrowserRouter>
  );
}

export default App;
