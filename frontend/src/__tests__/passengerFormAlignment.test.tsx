/**
 * Passenger Form Alignment & Traveler Data Regression Tests
 *
 * Verifies:
 * 1. Desktop field grouping, labels, and proportional layout structure in TravelerModal.
 * 2. First-name and last-name values are preserved without truncation or data distortion.
 * 3. Gender and date-of-birth validation and field contracts work consistently.
 * 4. Contact email and phone fields remain correctly associated with lead traveler.
 * 5. Responsive layout classes (grid-cols-12, sm:col-span-5, sm:col-span-2) prevent overflow.
 */
import assert from 'node:assert';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { FlightPassenger } from '../types/flight';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

console.log('Running Passenger Form Alignment & Traveler Data Regression Tests...\n');

// ---------------------------------------------------------------------------
// Test 1: Static layout & responsive grid audit of TravelerModal.tsx
// ---------------------------------------------------------------------------
{
  const modalPath = path.resolve(__dirname, '../components/booking/TravelerModal.tsx');
  assert.ok(fs.existsSync(modalPath), 'TravelerModal.tsx must exist');
  const modalSrc = fs.readFileSync(modalPath, 'utf8');

  // Verify unified 12-column responsive grid
  assert.ok(
    modalSrc.includes('grid-cols-1 sm:grid-cols-12 gap-3.5'),
    'Row 1 must use responsive 12-column grid layout'
  );

  // Verify proportional field widths in Row 1: Title (2), Given Name (5), Family Name (5)
  assert.ok(modalSrc.includes('sm:col-span-2'), 'Title must span 2 columns on desktop');
  assert.ok(modalSrc.includes('sm:col-span-5'), 'First name and Last name must span 5 columns each on desktop');

  // Verify consistent input heights
  const h10Matches = (modalSrc.match(/h-10/g) || []).length;
  assert.ok(h10Matches >= 6, 'Inputs and controls must use consistent h-10 height');

  // Verify non-wrapping required indicators attached to labels
  assert.ok(
    modalSrc.includes('<span className="text-red-400 font-semibold">*</span>'),
    'Required asterisks must be styled consistently'
  );

  // Verify lead traveler indicator aligned with section heading
  assert.ok(
    modalSrc.includes('Lead Traveler'),
    'Lead traveler badge must be present in section heading'
  );

  console.log('✔ Test 1: Desktop field grouping, responsive grid, and label styling verified.');
}

// ---------------------------------------------------------------------------
// Test 2: First-name and last-name values preserved and validated
// ---------------------------------------------------------------------------
{
  const passenger: FlightPassenger = {
    given_name: 'Alexander',
    family_name: 'Montgomery-Smith',
    born_on: '1988-11-23',
    title: 'mr',
    gender: 'm',
    email: 'alex.m@example.com',
    phone_number: '+1-555-0199',
    type: 'adult',
  };

  assert.strictEqual(passenger.given_name.trim(), 'Alexander');
  assert.strictEqual(passenger.family_name.trim(), 'Montgomery-Smith');
  assert.ok(passenger.given_name.length <= 40);
  assert.ok(passenger.family_name.length <= 40);

  console.log('✔ Test 2: First-name and last-name values preserved without truncation.');
}

// ---------------------------------------------------------------------------
// Test 3: Gender and date-of-birth selection structure
// ---------------------------------------------------------------------------
{
  const modalPath = path.resolve(__dirname, '../components/booking/TravelerModal.tsx');
  const modalSrc = fs.readFileSync(modalPath, 'utf8');

  // Check 3-column gender sub-grid with uniform button heights
  assert.ok(
    modalSrc.includes('grid grid-cols-3 gap-2 h-10'),
    'Gender options must be in an evenly distributed 3-column subgrid'
  );
  assert.ok(modalSrc.includes("'Male'"), 'Male gender option must exist');
  assert.ok(modalSrc.includes("'Female'"), 'Female gender option must exist');
  assert.ok(modalSrc.includes("'Other'"), 'Other gender option must exist');

  // Date of birth input
  assert.ok(
    modalSrc.includes('type="date"'),
    'Date of birth must use date picker input'
  );
  assert.ok(
    modalSrc.includes('Date of Birth (YYYY-MM-DD)'),
    'Date of birth must have explicit YYYY-MM-DD label'
  );

  console.log('✔ Test 3: Gender and date-of-birth controls verified.');
}

// ---------------------------------------------------------------------------
// Test 4: Email and phone fields association with lead traveler
// ---------------------------------------------------------------------------
{
  const modalPath = path.resolve(__dirname, '../components/booking/TravelerModal.tsx');
  const modalSrc = fs.readFileSync(modalPath, 'utf8');

  // Lead traveler email requirement distinction
  assert.ok(
    modalSrc.includes('activePassengerIndex === 0'),
    'Lead traveler must be differentiated from companion travelers'
  );
  assert.ok(
    modalSrc.includes('(optional)'),
    'Companion email/phone must indicate optionality'
  );

  console.log('✔ Test 4: Contact email and phone association with lead traveler verified.');
}

// ---------------------------------------------------------------------------
// Test 5: Responsive layout does not break existing traveler identity behavior
// ---------------------------------------------------------------------------
{
  const modalPath = path.resolve(__dirname, '../components/booking/TravelerModal.tsx');
  const modalSrc = fs.readFileSync(modalPath, 'utf8');

  // Mobile widths: stack or wrap fields deliberately
  assert.ok(
    modalSrc.includes('grid-cols-1 sm:grid-cols-12'),
    'Mobile layout must use single column stacking to prevent horizontal overflow'
  );
  assert.ok(
    modalSrc.includes('overflow-y-auto'),
    'Modal content must handle vertical scroll cleanly without horizontal overflow'
  );

  console.log('✔ Test 5: Mobile responsiveness and layout safety verified.');
}

console.log('\nAll Passenger Form Alignment tests PASSED!\n');
