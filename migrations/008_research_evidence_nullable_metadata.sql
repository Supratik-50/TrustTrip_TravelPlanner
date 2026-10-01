-- Migration 008: Preserve NULL semantics for optional research evidence and source metadata
-- Allows genuinely unknown title, snippet, locator, and relevance_score without forcing placeholder defaults.

-- 1. research_sources: title is optional metadata
ALTER TABLE research_sources ALTER COLUMN title DROP NOT NULL;

-- 2. research_evidence: title, snippet, locator, and relevance_score are optional metadata
ALTER TABLE research_evidence ALTER COLUMN title DROP NOT NULL;
ALTER TABLE research_evidence ALTER COLUMN snippet DROP NOT NULL;
ALTER TABLE research_evidence ALTER COLUMN locator DROP NOT NULL;
ALTER TABLE research_evidence ALTER COLUMN locator DROP DEFAULT;
ALTER TABLE research_evidence ALTER COLUMN relevance_score DROP NOT NULL;
ALTER TABLE research_evidence ALTER COLUMN relevance_score DROP DEFAULT;

-- Update relevance score check constraint to allow NULL while preserving bounds [0.0, 1.0] when populated
ALTER TABLE research_evidence DROP CONSTRAINT IF EXISTS check_relevance_score_range;
ALTER TABLE research_evidence DROP CONSTRAINT IF EXISTS chk_research_evidence_relevance;
ALTER TABLE research_evidence ADD CONSTRAINT check_relevance_score_range
    CHECK (relevance_score IS NULL OR (relevance_score >= 0.0 AND relevance_score <= 1.0));

-- 3. research_citations: title and locator are optional metadata
ALTER TABLE research_citations ALTER COLUMN title DROP NOT NULL;
ALTER TABLE research_citations ALTER COLUMN locator DROP NOT NULL;
ALTER TABLE research_citations ALTER COLUMN locator DROP DEFAULT;
