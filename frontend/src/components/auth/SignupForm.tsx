import React, { useState } from 'react';
import { Mail, Lock } from 'lucide-react';
import { Input } from '../common/Input';
import { Button } from '../common/Button';
import { useAuth } from '../../context/AuthContext';

interface SignupFormProps {
  onSuccess: () => void;
  onSwitchToLogin: () => void;
}

export const SignupForm: React.FC<SignupFormProps> = ({ onSuccess, onSwitchToLogin }) => {
  const { signup, isLoading, error, clearError } = useAuth();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [validationError, setValidationError] = useState<string | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setValidationError(null);
    clearError();

    if (!email.trim() || !email.includes('@')) {
      setValidationError('Please provide a valid email address.');
      return;
    }
    if (password.length < 6) {
      setValidationError('Password should be at least 6 characters long.');
      return;
    }

    const success = await signup({
      email: email.trim(),
      password,
    });

    if (success) {
      onSuccess();
    }
  };

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      {error && (
        <div className="p-3 bg-[var(--status-danger)]/10 border border-[var(--status-danger)]/30 rounded-lg text-xs text-[var(--status-danger)] font-medium leading-relaxed">
          {error}
        </div>
      )}

      {validationError && (
        <div className="p-3 bg-[var(--status-warning)]/10 border border-[var(--status-warning)]/30 rounded-lg text-xs text-[var(--status-warning)] font-medium leading-relaxed">
          {validationError}
        </div>
      )}

      <Input
        label="Email Address"
        type="email"
        placeholder="you@example.com"
        value={email}
        onChange={(e) => {
          setEmail(e.target.value);
          if (validationError) setValidationError(null);
        }}
        autoComplete="email"
        leftIcon={<Mail className="w-4 h-4" />}
        required
      />

      <Input
        label="Password"
        type="password"
        placeholder="At least 6 characters"
        value={password}
        onChange={(e) => {
          setPassword(e.target.value);
          if (validationError) setValidationError(null);
        }}
        autoComplete="new-password"
        leftIcon={<Lock className="w-4 h-4" />}
        required
      />

      <div className="pt-2 flex flex-col gap-2.5">
        <Button
          type="submit"
          variant="primary"
          size="md"
          isLoading={isLoading}
          className="w-full"
        >
          Create Traveler Account
        </Button>

        <div className="text-center text-xs text-[var(--text-secondary)] pt-2">
          Already have an account?{' '}
          <button
            type="button"
            onClick={onSwitchToLogin}
            className="text-[var(--primary)] font-semibold hover:underline cursor-pointer"
          >
            Sign in
          </button>
        </div>
      </div>
    </form>
  );
};
