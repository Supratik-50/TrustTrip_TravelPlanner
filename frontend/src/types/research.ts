export type ResearchSourceType = 'web' | 'rag' | 'hybrid' | 'document';
export type ResearchMode = 'auto' | 'web' | 'rag' | 'hybrid';
export type EvidenceStatus = 'complete' | 'partial' | 'conflicted' | 'not_found';
export type SourceTrustClass = 'OFFICIAL' | 'TRUSTED_SECONDARY' | 'USER_PROVIDED' | 'UNKNOWN';

export interface ResearchFact {
  fact_id: string;
  claim: string;
  evidence_id: string;
  citation_id: string;
  source_id?: string | null;
}

export interface ResearchCitation {
  citation_id?: string;
  evidence_id?: string;
  title?: string | null;
  domain?: string;
  canonical_url?: string;
  published_at?: string | null;
  retrieved_at?: string;
  locator?: string | null;
  cited_claims?: string[];
  source_id?: string | null;

  // UI / legacy backward compatibility properties
  id?: string;
  source_type?: ResearchSourceType;
  publisher?: string;
  url?: string;
  snippet?: string | null;
  claim?: string;
  freshness?: string;
  confidence?: number;
  location?: string;
}

export type EvidenceRecord = ResearchCitation;

export interface ResearchConflict {
  conflict_id?: string;
  topic?: string;
  claim_a?: string;
  source_a_title?: string;
  source_a_trust?: SourceTrustClass | string;
  claim_b?: string;
  source_b_title?: string;
  source_b_trust?: SourceTrustClass | string;
  resolution_summary?: string;
  resolved?: boolean;

  // UI legacy compatibility
  field?: string;
  description?: string;
  resolution_suggestion?: string;
  conflicting_sources?: string[];
}

export interface ResearchResult {
  research_session_id: string;
  query_id: string;
  query_text?: string;
  intent: string;
  mode: ResearchMode | string;
  answer: string;
  citations: ResearchCitation[];
  facts?: ResearchFact[];
  evidence_status: EvidenceStatus | string;
  conflicts?: ResearchConflict[];
  stale?: boolean;
  evidence_count?: number;
  created_at: string;

  // Backward compatibility aliases
  session_id?: string;
  trip_id?: string;
  question?: string;
  source_type?: ResearchSourceType;
  confidence_score?: number;
  freshness_note?: string;
  insights_count?: {
    places?: number;
    itinerary_suggestions?: number;
    verified_sources?: number;
  };
}

export type ResearchResponse = ResearchResult;

export interface ResearchQueryRequest {
  query: string;
  mode?: ResearchMode;
  trip_id?: string;
  document_ids?: string[];
  locale?: string;
  max_sources?: number;

  // Backward compatibility alias (deprecated)
  question?: string;
  session_id?: string;
  constraints?: Record<string, unknown>;
}

export interface ResearchDocument {
  document_id: string;
  user_id?: string;
  trip_id?: string;
  filename: string;
  title?: string;
  file_size_bytes: number;
  content_type?: string;
  content_hash?: string;
  chunk_count?: number;
  created_at?: string;
  uploaded_at?: string;
  status?: 'indexed' | 'processing' | 'failed';
  doc_type?: string;
}

export interface ResearchSession {
  session_id: string;
  user_id?: string;
  trip_id?: string;
  mode?: ResearchMode | string;
  status?: string;
  created_at: string;
  expires_at?: string;
  updated_at?: string;
  queries: ResearchResult[];
}
