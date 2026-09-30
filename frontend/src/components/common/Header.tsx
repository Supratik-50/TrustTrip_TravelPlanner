import React, { useState } from 'react';
import { Compass, Menu, X, Plus } from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import { Button } from './Button';
import { UserProfileMenu } from '../auth/UserProfileMenu';
import { BackendStatusIndicator } from './BackendStatusIndicator';
import { ThemeToggle } from '../ui/ThemeToggle';
import { CurrencySelector } from './CurrencySelector';

export type NavTab = 'overview' | 'dashboard' | 'trips' | 'plan' | 'flights' | 'hotels' | 'research';

interface HeaderProps {
  activeTab: NavTab;
  onSelectTab: (tab: NavTab) => void;
  onOpenAuth: (mode?: 'login' | 'signup') => void;
  onPlanTripClick: () => void;
}

export const Header: React.FC<HeaderProps> = ({
  activeTab,
  onSelectTab,
  onOpenAuth,
  onPlanTripClick,
}) => {
  const { isAuthenticated, user, logout } = useAuth();
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);

  // Clean 3-zone navigation links as specified in Section 13
  const primaryNavItems: { id: NavTab; label: string }[] = [
    { id: isAuthenticated ? 'dashboard' : 'overview', label: 'Discover' },
    { id: 'trips', label: 'My Trips' },
    { id: 'plan', label: 'Plan' },
  ];

  return (
    <header className="sticky top-0 z-40 bg-[var(--background)]/90 backdrop-blur-md border-b border-[var(--border)] transition-colors">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        <div className="flex items-center justify-between h-16">
          {/* Zone 1: Single text element wordmark */}
          <div className="flex items-center gap-3">
            <button
              type="button"
              onClick={() => onSelectTab(isAuthenticated ? 'dashboard' : 'overview')}
              className="text-xl font-bold tracking-tight text-[var(--text-primary)] hover:text-[var(--primary)] transition-colors cursor-pointer flex items-center gap-2"
            >
              <Compass className="w-5 h-5 text-[var(--primary)]" />
              <span className="font-editorial text-2xl tracking-tight">TrustTrip</span>
            </button>
            <div className="hidden lg:block">
              <BackendStatusIndicator />
            </div>
          </div>

          {/* Zone 2: Clean 3 primary text navigation links with subtle underline */}
          <nav className="hidden md:flex items-center gap-8 text-sm font-medium text-[var(--text-secondary)]">
            {primaryNavItems.map((item) => {
              const isActive =
                activeTab === item.id ||
                (item.id === 'dashboard' && activeTab === 'overview') ||
                (item.id === 'overview' && activeTab === 'dashboard');

              return (
                <button
                  key={item.id}
                  type="button"
                  onClick={() => onSelectTab(item.id)}
                  className={`transition-colors whitespace-nowrap cursor-pointer py-1 relative ${
                    isActive
                      ? 'text-[var(--primary)] font-semibold'
                      : 'hover:text-[var(--text-primary)]'
                  }`}
                >
                  {item.label}
                  {isActive && (
                    <span className="absolute bottom-0 left-0 right-0 h-0.5 bg-[var(--primary)] rounded-full" />
                  )}
                </button>
              );
            })}
          </nav>

          {/* Zone 3: Compact currency + theme toggle + 1-2 primary actions */}
          <div className="flex items-center gap-2 sm:gap-3">
            <CurrencySelector />
            <ThemeToggle />

            {isAuthenticated ? (
              <>
                <Button
                  variant="primary"
                  size="sm"
                  onClick={onPlanTripClick}
                  className="hidden sm:inline-flex shadow-xs"
                  leftIcon={<Plus className="w-3.5 h-3.5" />}
                >
                  New Journey
                </Button>
                <UserProfileMenu />
              </>
            ) : (
              <>
                <button
                  type="button"
                  onClick={() => onOpenAuth('login')}
                  className="text-xs font-semibold text-[var(--text-primary)] hover:text-[var(--primary)] transition-colors cursor-pointer px-2.5 py-1.5"
                >
                  Sign In
                </button>
                <Button
                  variant="accent"
                  size="sm"
                  onClick={() => onOpenAuth('signup')}
                  className="shadow-xs font-semibold"
                >
                  Start Planning
                </Button>
              </>
            )}

            {/* Mobile menu trigger */}
            <button
              type="button"
              onClick={() => setMobileMenuOpen(!mobileMenuOpen)}
              className="md:hidden p-1.5 text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition-colors"
              aria-label="Toggle menu"
            >
              {mobileMenuOpen ? <X className="w-5 h-5" /> : <Menu className="w-5 h-5" />}
            </button>
          </div>
        </div>
      </div>

      {/* Mobile drawer */}
      {mobileMenuOpen && (
        <div className="md:hidden border-t border-[var(--border)] bg-[var(--surface)] px-4 py-4 space-y-3">
          <div className="flex items-center justify-between pb-2 border-b border-[var(--border-subtle)]">
            <BackendStatusIndicator />
            <div className="flex items-center gap-2">
              <CurrencySelector />
            </div>
          </div>
          {primaryNavItems.map((item) => (
            <button
              key={item.id}
              type="button"
              onClick={() => {
                onSelectTab(item.id);
                setMobileMenuOpen(false);
              }}
              className={`w-full text-left px-3 py-2 text-sm rounded-xl transition-colors ${
                activeTab === item.id
                  ? 'bg-[var(--primary-soft)] text-[var(--primary)] font-semibold'
                  : 'text-[var(--text-secondary)] hover:bg-[var(--background)] hover:text-[var(--text-primary)]'
              }`}
            >
              {item.label}
            </button>
          ))}
          {isAuthenticated ? (
            <div className="pt-2 border-t border-[var(--border-subtle)] space-y-2">
              <div className="px-3 py-1.5 text-xs text-[var(--text-secondary)] flex items-center justify-between">
                <span className="truncate max-w-[200px] font-mono text-[11px]">{user?.email}</span>
                <span className="text-[10px] text-[var(--status-success)] font-medium">Verified</span>
              </div>
              <Button
                variant="primary"
                size="sm"
                onClick={() => {
                  onPlanTripClick();
                  setMobileMenuOpen(false);
                }}
                className="w-full shadow-xs"
              >
                Plan New Journey
              </Button>
              <button
                type="button"
                onClick={() => {
                  setMobileMenuOpen(false);
                  logout();
                }}
                className="w-full text-left px-3 py-2 text-sm text-[var(--status-danger)] hover:bg-[var(--status-danger)]/10 rounded-xl transition-colors cursor-pointer"
              >
                Sign Out
              </button>
            </div>
          ) : (
            <div className="pt-2 border-t border-[var(--border-subtle)] flex flex-col gap-2">
              <button
                type="button"
                onClick={() => {
                  setMobileMenuOpen(false);
                  onOpenAuth('login');
                }}
                className="w-full text-center py-2 text-sm font-semibold text-[var(--text-primary)] hover:text-[var(--primary)] transition-colors cursor-pointer"
              >
                Sign In
              </button>
              <Button
                variant="accent"
                size="sm"
                onClick={() => {
                  setMobileMenuOpen(false);
                  onOpenAuth('signup');
                }}
                className="w-full shadow-xs font-semibold"
              >
                Start Planning
              </Button>
            </div>
          )}
        </div>
      )}
    </header>
  );
};
