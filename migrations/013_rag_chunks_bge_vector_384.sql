-- Migration: 013_rag_chunks_bge_vector_384.sql
-- Description: Upgrade RAG chunk embedding vector dimension from 128 to 384 for BAAI/bge-small-en-v1.5.

-- 1. Drop existing HNSW index that depends on vector(128)
DROP INDEX IF EXISTS idx_rag_chunks_embedding;

-- 2. Add embedding_model metadata column to track model provenance
ALTER TABLE rag_chunks ADD COLUMN IF NOT EXISTS embedding_model TEXT NOT NULL DEFAULT 'BAAI/bge-small-en-v1.5';

-- 3. Alter embedding column from vector(128) to vector(384)
ALTER TABLE rag_chunks ALTER COLUMN embedding TYPE vector(384);

-- 4. Re-create HNSW index for cosine distance on vector(384)
CREATE INDEX IF NOT EXISTS idx_rag_chunks_embedding ON rag_chunks USING hnsw (embedding vector_cosine_ops);
