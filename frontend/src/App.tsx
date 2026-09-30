/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState } from 'react';
import {
  BrowserRouter,
  Routes,
  Route,
  Navigate,
  useNavigate,
  useLocation,
  useParams,
} from 'react-router-dom';
import { NotificationProvider } from './context/NotificationContext';
import { AuthProvider, useAuth } from './context/AuthContext';
import { ThemeProvider } from './context/ThemeContext';
import { CurrencyProvider } from './context/CurrencyContext';
import { Header, NavTab } from './components/common/Header';
import { Footer } from './components/common/Footer';
import { OverviewView } from './components/views/OverviewView';
import { DashboardView } from './components/views/DashboardView';
import { TripsView } from './components/views/TripsView';
import { CreateTripView } from './components/views/CreateTripView';
import { TripDetailView } from './components/views/TripDetailView';
import { FlightsView } from './components/views/FlightsView';
import { HotelsView } from './components/views/HotelsView';
import { ResearchView } from './components/views/ResearchView';
import { PartRoadmapPlaceholder } from './components/views/PartRoadmapPlaceholder';
import { AuthModal } from './components/auth/AuthModal';
import { Button } from './components/common/Button';
import { Lock } from 'lucide-react';

interface ProtectedRouteProps {
  children: React.ReactNode;
  onOpenAuth: () => void;
}

const ProtectedRoute: React.FC<ProtectedRouteProps> = ({ children, onOpenAuth }) => {
  const { isAuthenticated, isLoading } = useAuth();

  if (isLoading) {
    return (
      <div className="py-24 text-center text-xs text-[var(--text-secondary)]">
        Verifying session...
      </div>
    );
  }

  if (!isAuthenticated) {
    return (
      <div className="py-20 max-w-md mx-auto text-center space-y-4 px-4">
        <div className="w-12 h-12 rounded-full bg-[var(--primary-soft)] text-[var(--primary)] mx-auto flex items-center justify-center">
          <Lock className="w-5 h-5" />
        </div>
        <h2 className="text-xl font-bold text-[var(--text-primary)] font-editorial">
          Sign In Required
        </h2>
        <p className="text-xs text-[var(--text-secondary)] leading-relaxed">
          Trip workspaces and planning dashboards are protected. Please sign in or create an
          account to access your canonical trips.
        </p>
        <div className="pt-2">
          <Button variant="primary" size="md" onClick={onOpenAuth}>
            Sign In to Continue
          </Button>
        </div>
      </div>
    );
  }

  return <>{children}</>;
};

const TripDetailRouteWrapper: React.FC<{ onBack: () => void; initialTab?: 'itinerary' | 'flights' | 'hotels' | 'research' | 'revisions' | 'booking' }> = ({ onBack, initialTab }) => {
  const { tripId } = useParams<{ tripId: string }>();
  if (!tripId) {
    return <Navigate to="/trips" replace />;
  }
  return <TripDetailView tripId={tripId} initialTab={initialTab} onBack={onBack} />;
};

const CreateTripRouteWrapper: React.FC<{ onBack: () => void }> = ({ onBack }) => {
  const navigate = useNavigate();
  const location = useLocation();
  const state = location.state as { destination?: string; origin?: string } | undefined;

  return (
    <CreateTripView
      onBack={onBack}
      onTripCreated={(tripId) => navigate(`/trips/${tripId}`)}
      initialDestination={state?.destination || ''}
      initialOrigin={state?.origin || ''}
    />
  );
};

const MainContent: React.FC = () => {
  const navigate = useNavigate();
  const location = useLocation();
  const { isAuthenticated } = useAuth();
  const [isAuthModalOpen, setIsAuthModalOpen] = useState(false);
  const [authModalMode, setAuthModalMode] = useState<'login' | 'signup'>('login');

  const getActiveTab = (): NavTab => {
    const path = location.pathname;
    if (path === '/trips/new') return 'plan';
    if (path.startsWith('/trips')) return 'trips';
    if (path.startsWith('/flights')) return 'flights';
    if (path.startsWith('/hotels')) return 'hotels';
    if (path.startsWith('/research')) return 'research';
    if (path === '/dashboard') return 'dashboard';
    return isAuthenticated ? 'dashboard' : 'overview';
  };

  const handleSelectTab = (tab: NavTab) => {
    switch (tab) {
      case 'overview':
        navigate('/');
        break;
      case 'dashboard':
        navigate('/dashboard');
        break;
      case 'trips':
        navigate('/trips');
        break;
      case 'plan':
        navigate('/trips/new');
        break;
      case 'flights':
        navigate('/flights');
        break;
      case 'hotels':
        navigate('/hotels');
        break;
      case 'research':
        navigate('/research');
        break;
    }
  };

  const handleOpenAuth = (mode: 'login' | 'signup' = 'login') => {
    setAuthModalMode(mode);
    setIsAuthModalOpen(true);
  };

  const handlePlanTripClick = () => {
    if (!isAuthenticated) {
      handleOpenAuth('login');
      return;
    }
    navigate('/trips/new');
  };

  return (
    <div className="min-h-screen flex flex-col bg-[var(--background)] text-[var(--text-primary)] selection:bg-[var(--primary-soft)] selection:text-[var(--primary)] transition-colors">
      {/* Top Bar Contract with compact theme toggle */}
      <Header
        activeTab={getActiveTab()}
        onSelectTab={handleSelectTab}
        onOpenAuth={handleOpenAuth}
        onPlanTripClick={handlePlanTripClick}
      />

      {/* Main Content View with Protected Routes */}
      <main className="flex-1 max-w-7xl w-full mx-auto px-4 sm:px-6 lg:px-8 py-6">
        <Routes>
          {/* Landing / Authenticated Entry */}
          <Route
            path="/"
            element={
              isAuthenticated ? (
                <Navigate to="/dashboard" replace />
              ) : (
                <OverviewView
                  onSelectTab={handleSelectTab}
                  onOpenAuth={handleOpenAuth}
                  onStartPlanning={(initialData) => {
                    if (!isAuthenticated) {
                      handleOpenAuth('login');
                    } else {
                      navigate('/trips/new', { state: initialData });
                    }
                  }}
                />
              )
            }
          />

          {/* Main Authenticated Dashboard */}
          <Route
            path="/dashboard"
            element={
              <ProtectedRoute onOpenAuth={() => handleOpenAuth('login')}>
                <DashboardView
                  onOpenTrip={(tripId) => navigate(`/trips/${tripId}`)}
                  onCreateTrip={(prefillDestination) =>
                    navigate('/trips/new', {
                      state: prefillDestination ? { destination: prefillDestination } : undefined,
                    })
                  }
                  onNavigateTab={handleSelectTab}
                />
              </ProtectedRoute>
            }
          />

          {/* Trips List */}
          <Route
            path="/trips"
            element={
              <ProtectedRoute onOpenAuth={() => handleOpenAuth('login')}>
                <TripsView
                  onOpenTrip={(tripId) => navigate(`/trips/${tripId}`)}
                  onCreateTrip={() => navigate('/trips/new')}
                />
              </ProtectedRoute>
            }
          />

          {/* Create Trip */}
          <Route
            path="/trips/new"
            element={
              <ProtectedRoute onOpenAuth={() => handleOpenAuth('login')}>
                <CreateTripRouteWrapper onBack={() => navigate('/trips')} />
              </ProtectedRoute>
            }
          />

          {/* Trip Detail Workspace */}
          <Route
            path="/trips/:tripId"
            element={
              <ProtectedRoute onOpenAuth={() => handleOpenAuth('login')}>
                <TripDetailRouteWrapper onBack={() => navigate('/trips')} />
              </ProtectedRoute>
            }
          />

          {/* Canonical /trips/:tripId/plan redirect/alias */}
          <Route
            path="/trips/:tripId/plan"
            element={
              <ProtectedRoute onOpenAuth={() => handleOpenAuth('login')}>
                <TripDetailRouteWrapper onBack={() => navigate('/trips')} />
              </ProtectedRoute>
            }
          />

          {/* Canonical /trips/:tripId/research notebook */}
          <Route
            path="/trips/:tripId/research"
            element={
              <ProtectedRoute onOpenAuth={() => handleOpenAuth('login')}>
                <TripDetailRouteWrapper initialTab="research" onBack={() => navigate('/trips')} />
              </ProtectedRoute>
            }
          />

          {/* Canonical /trips/:tripId/booking (Part 5A) */}
          <Route
            path="/trips/:tripId/booking"
            element={
              <ProtectedRoute onOpenAuth={() => handleOpenAuth('login')}>
                <TripDetailRouteWrapper initialTab="booking" onBack={() => navigate('/trips')} />
              </ProtectedRoute>
            }
          />

          {/* Canonical /trips/:tripId/confirmation */}
          <Route
            path="/trips/:tripId/confirmation"
            element={
              <ProtectedRoute onOpenAuth={() => handleOpenAuth('login')}>
                <TripDetailRouteWrapper initialTab="booking" onBack={() => navigate('/trips')} />
              </ProtectedRoute>
            }
          />

          {/* Real Flights Search & Discovery (Part 3) */}
          <Route path="/flights" element={<FlightsView />} />

          {/* Real Hotels Search & Discovery (Part 3) */}
          <Route path="/hotels" element={<HotelsView />} />

          {/* Real Research & Travel Knowledge Discovery (Part 4) */}
          <Route
            path="/research"
            element={<ResearchView />}
          />

          {/* Fallback to root */}
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>

      {/* Footer */}
      <Footer />

      {/* Auth Modal */}
      <AuthModal
        isOpen={isAuthModalOpen}
        onClose={() => setIsAuthModalOpen(false)}
        initialMode={authModalMode}
      />
    </div>
  );
};

export default function App() {
  return (
    <ThemeProvider>
      <CurrencyProvider>
        <NotificationProvider>
          <AuthProvider>
            <BrowserRouter>
              <MainContent />
            </BrowserRouter>
          </AuthProvider>
        </NotificationProvider>
      </CurrencyProvider>
    </ThemeProvider>
  );
}
