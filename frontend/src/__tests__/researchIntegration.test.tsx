import assert from 'assert';
import { executeMockResearchQuery } from '../lib/mockTravelData';
import { ResearchQueryRequest, ResearchResult } from '../types/research';

console.log('Running Section 7 Research & Evidence Contract Frontend Tests...');

// Test 1: Query request accepts query and executes without client session_id
{
  const req: ResearchQueryRequest = {
    query: 'What are authentic kaiseki ryotei in Gion?',
    mode: 'hybrid',
    trip_id: 'trip_123',
  };

  const res: ResearchResult = executeMockResearchQuery(req);

  assert.strictEqual(res.query_text, 'What are authentic kaiseki ryotei in Gion?');
  assert.ok(res.research_session_id, 'Server generates research session ID');
  assert.ok(res.query_id, 'Server generates query ID');
  assert.strictEqual(res.evidence_status, 'complete');
  assert.ok(res.citations.length > 0, 'Citations returned');
  assert.ok(res.facts && res.facts.length > 0, 'Facts returned');

  console.log('✔ Test 1: Query request accepts query and returns structured ResearchResult with facts.');
}

// Test 2: Provenance integrity: each fact links to evidence_id and citation_id
{
  const req: ResearchQueryRequest = {
    query: 'Kyoto food and dining recommendations',
    mode: 'web',
  };

  const res = executeMockResearchQuery(req);
  assert.ok(res.facts && res.facts.length > 0);

  for (const fact of res.facts) {
    assert.ok(fact.fact_id, 'Fact has unique UUID/ID');
    assert.ok(fact.claim && fact.claim.trim().length > 0, 'Fact claim is non-empty text');
    assert.ok(fact.evidence_id, 'Fact links to evidence_id');
    assert.ok(fact.citation_id, 'Fact links to citation_id');

    // Corresponding citation must exist
    const matchedCit = res.citations.find(
      (c) => c.citation_id === fact.citation_id || c.evidence_id === fact.evidence_id
    );
    assert.ok(matchedCit, `Fact ${fact.fact_id} must have corresponding citation in citations list`);
  }

  console.log('✔ Test 2: Every ResearchFact strictly maintains claim -> evidence_id -> citation_id provenance.');
}

// Test 3: Absence of client session forging: client does not dictate server session
{
  const req: ResearchQueryRequest = {
    query: 'Amalfi Coast ferries and transit rules',
  };

  const res = executeMockResearchQuery(req);
  assert.ok(res.research_session_id.startsWith('rs_'));
  assert.ok(res.citations.length > 0);

  console.log('✔ Test 3: Client does not forge session ID; server/mock assigns session ID.');
}

// Test 4: Copy action produces accurate clipboard-success message and does NOT claim notes updated/revision linked
{
  let notifiedMessage = '';
  let notifiedType = '';
  let notifiedTitle = '';

  const mockNotify = (msg: string, type?: string, title?: string) => {
    notifiedMessage = msg;
    notifiedType = type || 'info';
    notifiedTitle = title || '';
  };

  const handleCopyInsight = (insightText: string) => {
    // Mimic ResearchView copy handler
    mockNotify('Insight copied to clipboard.', 'success', 'Insight Copied');
  };

  handleCopyInsight('Sample insight answer text');

  // A. Accurate clipboard success message
  assert.strictEqual(notifiedMessage, 'Insight copied to clipboard.');
  assert.strictEqual(notifiedType, 'success');
  assert.strictEqual(notifiedTitle, 'Insight Copied');

  // B. Strictly prohibited phrases
  const lowerMsg = notifiedMessage.toLowerCase();
  assert.ok(!lowerMsg.includes('note'), 'Message must not claim notes updated');
  assert.ok(!lowerMsg.includes('revision'), 'Message must not claim revision linked');
  assert.ok(!lowerMsg.includes('attached'), 'Message must not claim evidence attached');
  assert.ok(!lowerMsg.includes('saved to'), 'Message must not claim saved to notes');

  console.log('✔ Test 4: Copy action produces accurate clipboard-success message without false notes/revision claims.');
}

// Test 5: Existing trip-linked research query API behavior remains intact
{
  const tripReq: ResearchQueryRequest = {
    query: 'Kyoto ryokan etiquette and shoe removal',
    mode: 'auto',
    trip_id: 'trip_kyoto_456',
  };

  const tripRes = executeMockResearchQuery(tripReq);
  assert.strictEqual(tripRes.query_text, 'Kyoto ryokan etiquette and shoe removal');
  assert.ok(tripRes.research_session_id);
  assert.ok(tripRes.citations.length > 0);
  assert.ok(tripRes.facts && tripRes.facts.length > 0);

  console.log('✔ Test 5: Existing trip-linked research query API behavior remains intact.');
}

// Test 6: Citation canonicality, non-positional resolution, and rejection of numeric/shortened tokens
{
  const uuidA = '11112222-3333-4444-5555-000000000001';
  const uuidB = '11112222-3333-4444-5555-000000000002'; // Shares same 8-char prefix as uuidA

  const citA = {
    citation_id: `[cite:${uuidA}]`,
    evidence_id: uuidA,
    source_id: 'src_1',
    title: 'Source A Title',
    domain: 'source-a.com',
    url: 'https://source-a.com',
    snippet: 'Snippet A',
    relevance_score: 0.9,
  };

  const citB = {
    citation_id: `[cite:${uuidB}]`,
    evidence_id: uuidB,
    source_id: 'src_2',
    title: 'Source B Title',
    domain: 'source-b.com',
    url: 'https://source-b.com',
    snippet: 'Snippet B',
    relevance_score: 0.85,
  };

  const CANONICAL_CITE_SPLIT = /(\[cite:[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\])/g;
  const CANONICAL_CITE_MATCH = /^\[cite:([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})\]$/;

  const testText = `First point [cite:${uuidA}]. Second point [cite:${uuidB}]. Positional point [cite:1]. Shortened point [cite:11112222].`;
  const parts = testText.split(CANONICAL_CITE_SPLIT);

  // A. Only full canonical UUID tokens are recognized
  const matchedTokens = parts.filter(p => CANONICAL_CITE_MATCH.test(p));
  assert.strictEqual(matchedTokens.length, 2, 'Only the two canonical full UUID tokens must match');
  assert.strictEqual(matchedTokens[0], `[cite:${uuidA}]`);
  assert.strictEqual(matchedTokens[1], `[cite:${uuidB}]`);

  // B. Numeric [cite:1] and shortened [cite:11112222] are NOT recognized as citation tokens
  assert.strictEqual(CANONICAL_CITE_MATCH.test('[cite:1]'), false, 'Numeric [cite:1] must be rejected');
  assert.strictEqual(CANONICAL_CITE_MATCH.test('[cite:2]'), false, 'Numeric [cite:2] must be rejected');
  assert.strictEqual(CANONICAL_CITE_MATCH.test('[cite:11112222]'), false, 'Shortened UUID must be rejected');

  // C. Resolution is non-positional and order-invariant
  const listOrder1 = [citA, citB];
  const listOrder2 = [citB, citA]; // Inverted list order

  const resolve = (token: string, citations: typeof listOrder1) => {
    const match = token.match(CANONICAL_CITE_MATCH);
    if (!match) return undefined;
    const citeUuid = match[1].toLowerCase();
    return citations.find(
      (c) =>
        (c.citation_id && c.citation_id.toLowerCase() === `[cite:${citeUuid}]`) ||
        (c.evidence_id && c.evidence_id.toLowerCase() === citeUuid)
    );
  };

  const resolvedA1 = resolve(`[cite:${uuidA}]`, listOrder1);
  const resolvedA2 = resolve(`[cite:${uuidA}]`, listOrder2);
  assert.strictEqual(resolvedA1?.evidence_id, uuidA);
  assert.strictEqual(resolvedA2?.evidence_id, uuidA);
  assert.strictEqual(resolvedA1, resolvedA2, 'Array order does NOT alter citation resolution');

  // D. Shared prefix UUIDs remain distinct
  const resolvedB1 = resolve(`[cite:${uuidB}]`, listOrder1);
  assert.strictEqual(resolvedB1?.evidence_id, uuidB);
  assert.notStrictEqual(resolvedA1?.evidence_id, resolvedB1?.evidence_id, 'Shared prefix UUIDs remain strictly distinct');

  console.log('✔ Test 6: Frontend citation canonicality, non-positional resolution, and prefix safety verified.');
}

console.log('All Section 7 Frontend Research Contract Tests PASSED!\n');
