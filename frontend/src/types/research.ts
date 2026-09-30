export type ResearchSourceType = 'web' | 'rag' | 'hybrid' | 'document';

export interface ResearchCitation {
  id: string;
  source_type: ResearchSourceType;
  title: string;
  publisher?: string;
  url?: string;
  snippet: string;
  claim?: string;
  freshness?: string;
  confidence?: number; // 0 to 1
  location?: string;
}

export type EvidenceRecord = ResearchCitation;

export interface ResearchConflict {
  field: string;
  description: string;
  resolution_suggestion?: string;
  conflicting_sources?: string[];
}

export interface ResearchResponse {
  session_id: string;
  trip_id?: string;
  question: string;
  answer: string;
  source_type: ResearchSourceType;
  confidence_score: number;
  freshness_note?: string;
  citations: ResearchCitation[];
  conflicts?: ResearchConflict[];
  insights_count?: {
    places?: number;
    itinerary_suggestions?: number;
    verified_sources?: number;
  };
  created_at: string;
}

export type ResearchResult = ResearchResponse;

export interface ResearchQueryRequest {
  trip_id?: string;
  session_id?: string;
  question: string;
  mode?: 'auto' | 'web' | 'rag' | 'hybrid';
  constraints?: Record<string, unknown>;
}

export interface ResearchDocument {
  document_id: string;
  filename: string;
  title?: string;
  file_size_bytes: number;
  uploaded_at: string;
  status: 'indexed' | 'processing' | 'failed';
  doc_type?: string;
}

export interface ResearchSession {
  session_id: string;
  trip_id?: string;
  queries: ResearchResponse[];
  created_at: string;
  updated_at: string;
}

