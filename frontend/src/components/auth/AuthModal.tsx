import React, { useState } from 'react';
import { Modal } from '../common/Modal';
import { LoginForm } from './LoginForm';
import { SignupForm } from './SignupForm';

interface AuthModalProps {
  isOpen: boolean;
  onClose: () => void;
  initialMode?: 'login' | 'signup';
}

export const AuthModal: React.FC<AuthModalProps> = ({
  isOpen,
  onClose,
  initialMode = 'login',
}) => {
  const [mode, setMode] = useState<'login' | 'signup'>(initialMode);

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title={mode === 'login' ? 'Sign in to TrustTrip' : 'Create your TrustTrip account'}
      subtitle={
        mode === 'login'
          ? 'Access your trips, saved flights, hotels, and research workspace.'
          : 'Plan, refine, and book high-trust journeys with intelligent assistance.'
      }
      maxWidth="md"
    >
      {mode === 'login' ? (
        <LoginForm
          onSuccess={onClose}
          onSwitchToSignup={() => setMode('signup')}
        />
      ) : (
        <SignupForm
          onSuccess={onClose}
          onSwitchToLogin={() => setMode('login')}
        />
      )}
    </Modal>
  );
};
