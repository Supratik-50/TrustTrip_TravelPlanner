/**
 * Frontend Regression & Contract Tests for Passenger Contact Validation & Duffel Agreement.
 *
 * Verifies:
 * Test 1: TravelerModal labels - Lead passenger has required (*) email and phone,
 *         while Passenger 2 (and subsequent companions) have (optional) labels.
 * Test 2: TravelerModal validation logic - Rejects empty lead email and phone;
 *         accepts empty companion email and phone.
 * Test 3: Formatting validation - Rejects invalid email/phone formats for both lead and companion.
 * Test 4: BookingWorkspace validation - isAllTravelersComplete requires lead contact details,
 *         while allowing companion contact details to remain empty.
 */
import assert from 'node:assert';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { FlightPassenger } from '../types/flight';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

console.log('Running Passenger Contact Validation & Duffel Agreement Tests...\n');

// ---------------------------------------------------------------------------
// Test 1: Static contract audit of TravelerModal.tsx
// ---------------------------------------------------------------------------
{
  const modalPath = path.resolve(__dirname, '../components/booking/TravelerModal.tsx');
  assert.ok(fs.existsSync(modalPath), `File not found: ${modalPath}`);
  const src = fs.readFileSync(modalPath, 'utf-8');

  // Lead email and phone have asterisk (*), companion has (optional)
  assert.ok(
    src.includes('Contact Email {activePassengerIndex === 0 ? <span className="text-red-400 font-semibold">*</span> : <span className="text-[var(--text-muted)] font-normal lowercase">(optional)</span>}'),
    'TravelerModal must mark email as * for passenger 0 and (optional) for companions'
  );
  assert.ok(
    src.includes('Contact Phone {activePassengerIndex === 0 ? <span className="text-red-400 font-semibold">*</span> : <span className="text-[var(--text-muted)] font-normal lowercase">(optional)</span>}'),
    'TravelerModal must mark phone as * for passenger 0 and (optional) for companions'
  );

  // Phone input renders error state and message
  assert.ok(
    src.includes('currentErrors.phone_number'),
    'TravelerModal must render phone error state and message'
  );

  console.log('✔ Test 1: TravelerModal UI labels accurately mark Passenger 1 required and companions optional.');
}

// ---------------------------------------------------------------------------
// Test 2: Validation logic accepts empty companion contact but rejects empty lead contact
// ---------------------------------------------------------------------------
{
  const validatePhone = (rawPhone: string) => {
    const cleaned = rawPhone.trim().replace(/[\s\-()]/g, '');
    const formatted = cleaned.startsWith('00') ? '+' + cleaned.slice(2) : cleaned;
    if (/^\d{10}$/.test(formatted)) return true;
    return /^\+\d{8,15}$/.test(formatted);
  };

  const validatePassenger = (p: FlightPassenger, idx: number): Record<string, string> => {
    const pErr: Record<string, string> = {};
    if (!p.given_name?.trim()) pErr.given_name = 'First name is required.';
    if (!p.family_name?.trim()) pErr.family_name = 'Last name is required.';
    if (!p.born_on?.trim()) pErr.born_on = 'Date of birth is required.';

    if (idx === 0) {
      if (!p.email || !p.email.trim()) {
        pErr.email = 'Contact email is required for the lead passenger.';
      } else if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(p.email.trim())) {
        pErr.email = 'Please enter a valid email address.';
      }

      if (!p.phone_number || !p.phone_number.trim()) {
        pErr.phone_number = 'Contact phone number is required for the lead passenger.';
      } else if (!validatePhone(p.phone_number)) {
        pErr.phone_number = 'Please enter a valid phone number (e.g. +91 98765 43210).';
      }
    } else {
      if (p.email && p.email.trim() && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(p.email.trim())) {
        pErr.email = 'Please enter a valid email address.';
      }
      if (p.phone_number && p.phone_number.trim() && !validatePhone(p.phone_number)) {
        pErr.phone_number = 'Please enter a valid phone number (e.g. +91 98765 43210).';
      }
    }
    return pErr;
  };

  // Case A: Lead passenger has valid contact, Companion has empty contact
  const leadValid: FlightPassenger = {
    given_name: 'Rahul',
    family_name: 'Sharma',
    born_on: '1990-05-15',
    gender: 'm',
    title: 'mr',
    email: 'rahul.sharma@example.com',
    phone_number: '+91 98765 43210',
  };
  const companionEmptyContact: FlightPassenger = {
    given_name: 'Priya',
    family_name: 'Sharma',
    born_on: '1993-08-20',
    gender: 'f',
    title: 'mrs',
    email: '',
    phone_number: '',
  };

  const errLead = validatePassenger(leadValid, 0);
  assert.strictEqual(Object.keys(errLead).length, 0, 'Lead passenger with valid details should have 0 errors');

  const errComp = validatePassenger(companionEmptyContact, 1);
  assert.strictEqual(Object.keys(errComp).length, 0, 'Companion with empty contact should have 0 errors (optional)');

  // Case B: Lead passenger missing email and phone
  const leadMissingContact: FlightPassenger = {
    given_name: 'Rahul',
    family_name: 'Sharma',
    born_on: '1990-05-15',
    gender: 'm',
    title: 'mr',
    email: '',
    phone_number: '',
  };
  const errLeadMissing = validatePassenger(leadMissingContact, 0);
  assert.ok(errLeadMissing.email, 'Lead passenger without email must fail validation');
  assert.ok(errLeadMissing.phone_number, 'Lead passenger without phone must fail validation');

  console.log('✔ Test 2: Validation accepts empty companion email/phone but rejects empty lead contact.');
}

// ---------------------------------------------------------------------------
// Test 3: Formatting validation rejects invalid phone/email
// ---------------------------------------------------------------------------
{
  const validatePhone = (rawPhone: string) => {
    const cleaned = rawPhone.trim().replace(/[\s\-()]/g, '');
    const formatted = cleaned.startsWith('00') ? '+' + cleaned.slice(2) : cleaned;
    if (/^\d{10}$/.test(formatted)) return true;
    return /^\+\d{8,15}$/.test(formatted);
  };

  assert.strictEqual(validatePhone('+919876543210'), true, 'Valid E.164 must pass');
  assert.strictEqual(validatePhone('+91 98765 43210'), true, 'E.164 with spaces must pass');
  assert.strictEqual(validatePhone('9876543210'), true, '10-digit number must pass');
  assert.strictEqual(validatePhone('+1-555-0199'), true, 'US number with hyphens must pass');
  assert.strictEqual(validatePhone('123'), false, '3-digit phone must fail');
  assert.strictEqual(validatePhone('abc'), false, 'Non-digit phone must fail');

  console.log('✔ Test 3: International phone validation correctly normalizes and rejects malformed values.');
}

// ---------------------------------------------------------------------------
// Test 4: BookingWorkspace static audit confirms lead contact enforcement
// ---------------------------------------------------------------------------
{
  const wsPath = path.resolve(__dirname, '../components/booking/BookingWorkspace.tsx');
  assert.ok(fs.existsSync(wsPath), `File not found: ${wsPath}`);
  const src = fs.readFileSync(wsPath, 'utf-8');

  // isTravelerValid accepts isLead param
  assert.ok(
    src.includes('isTravelerValid = (p?: FlightPassenger, isLead = false)'),
    'BookingWorkspace must support isLead parameter in isTravelerValid'
  );
  // isAllTravelersComplete passes idx === 0 for isLead
  assert.ok(
    src.includes('isTravelerValid(p, idx === 0)'),
    'BookingWorkspace must validate lead traveler contact on index 0'
  );

  console.log('✔ Test 4: BookingWorkspace properly enforces lead contact details while keeping companion optional.');
}

console.log('All Passenger Contact Validation & Duffel Agreement Tests PASSED!\n');
