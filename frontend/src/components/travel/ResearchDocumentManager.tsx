import React, { useState, useRef } from 'react';
import {
  FileText,
  Upload,
  Trash2,
  CheckCircle2,
  Clock,
  AlertCircle,
  FileCheck,
  Sparkles,
} from 'lucide-react';
import { ResearchDocument } from '../../types/research';
import { api } from '../../lib/apiClient';
import { useNotification } from '../../context/NotificationContext';
import { Button } from '../common/Button';

interface ResearchDocumentManagerProps {
  documents: ResearchDocument[];
  onRefresh: () => void;
}

export const ResearchDocumentManager: React.FC<ResearchDocumentManagerProps> = ({
  documents,
  onRefresh,
}) => {
  const [isUploading, setIsUploading] = useState(false);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [isDragging, setIsDragging] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const { notify } = useNotification();

  const handleFileUpload = async (file: File) => {
    // 10 MB limit check
    const MAX_MB = 10;
    const MAX_BYTES = MAX_MB * 1024 * 1024;

    if (file.size > MAX_BYTES) {
      notify(
        `The selected document (${(file.size / (1024 * 1024)).toFixed(1)} MB) exceeds the maximum limit of ${MAX_MB} MB.`,
        'error',
        'File Size Exceeded'
      );
      return;
    }

    setIsUploading(true);
    try {
      await api.uploadResearchDocument(file, file.name);
      notify(
        `"${file.name}" uploaded and indexed into your personal travel research knowledge base.`,
        'success',
        'Document Added'
      );
      onRefresh();
    } catch (err: any) {
      notify(
        err.message || "We couldn't upload this document. Please try again.",
        'error',
        'Upload Failed'
      );
    } finally {
      setIsUploading(false);
      if (fileInputRef.current) {
        fileInputRef.current.value = '';
      }
    }
  };

  const handleFileInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) {
      handleFileUpload(file);
    }
  };

  const handleDrop = (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setIsDragging(false);
    const file = e.dataTransfer.files?.[0];
    if (file) {
      handleFileUpload(file);
    }
  };

  const handleDelete = async (docId: string, filename: string) => {
    setDeletingId(docId);
    try {
      await api.deleteResearchDocument(docId);
      notify(`Removed "${filename}" from your research documents.`, 'info', 'Document Removed');
      onRefresh();
    } catch (err: any) {
      notify('Unable to delete document. Please retry.', 'error', 'Delete Failed');
    } finally {
      setDeletingId(null);
    }
  };

  const formatFileSize = (bytes: number) => {
    if (bytes < 1024 * 1024) {
      return `${Math.round(bytes / 1024)} KB`;
    }
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  };

  const formatDate = (isoString?: string) => {
    if (!isoString) return '';
    try {
      return new Date(isoString).toLocaleDateString([], {
        month: 'short',
        day: 'numeric',
        year: 'numeric',
      });
    } catch {
      return '';
    }
  };

  return (
    <div className="bg-[var(--surface)] rounded-3xl border border-[var(--border)] p-6 sm:p-8 shadow-xs space-y-6">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 pb-4 border-b border-[var(--border)]">
        <div>
          <span className="px-2.5 py-0.5 rounded-full text-xs font-semibold bg-[var(--primary-soft)] text-[var(--primary)] font-mono">
            Personal Knowledge Base
          </span>
          <h3 className="text-xl sm:text-2xl font-bold font-editorial text-[var(--text-primary)] mt-1">
            My Research Documents
          </h3>
          <p className="text-xs sm:text-sm text-[var(--text-secondary)] font-light mt-0.5">
            Upload custom guidebooks, travel notes, transit regulations, or approved articles. Maximum 10 MB per document.
          </p>
        </div>

        <Button
          variant="primary"
          size="sm"
          onClick={() => fileInputRef.current?.click()}
          isLoading={isUploading}
          leftIcon={<Upload className="w-3.5 h-3.5" />}
        >
          Upload Document
        </Button>
        <input
          ref={fileInputRef}
          type="file"
          accept=".pdf,.txt,.md,.doc,.docx"
          onChange={handleFileInputChange}
          className="hidden"
        />
      </div>

      {/* Drag and Drop Zone */}
      <div
        onDragOver={(e) => {
          e.preventDefault();
          setIsDragging(true);
        }}
        onDragLeave={() => setIsDragging(false)}
        onDrop={handleDrop}
        onClick={() => fileInputRef.current?.click()}
        className={`p-6 border-2 border-dashed rounded-2xl text-center cursor-pointer transition-all ${
          isDragging
            ? 'border-[var(--primary)] bg-[var(--primary-soft)]/20'
            : 'border-[var(--border-strong)] hover:border-[var(--primary)] hover:bg-[var(--surface-muted)]/50'
        }`}
      >
        <div className="w-10 h-10 rounded-full bg-[var(--surface-muted)] text-[var(--primary)] mx-auto flex items-center justify-center mb-2">
          <Upload className="w-5 h-5" />
        </div>
        <p className="text-xs sm:text-sm font-semibold text-[var(--text-primary)]">
          Drop travel notes or guides here, or click to browse
        </p>
        <p className="text-[11px] text-[var(--text-muted)] mt-0.5">
          Supports PDF, TXT, Markdown and Word docs (up to 10 MB)
        </p>
      </div>

      {/* Documents List */}
      <div className="space-y-3">
        <h4 className="text-xs font-semibold uppercase tracking-wider text-[var(--text-secondary)] font-mono flex items-center gap-1.5">
          <FileCheck className="w-3.5 h-3.5 text-[var(--primary)]" />
          Indexed Research Documents ({documents.length})
        </h4>

        {documents.length === 0 ? (
          <div className="py-8 text-center text-xs text-[var(--text-muted)] bg-[var(--surface-muted)]/30 rounded-2xl border border-[var(--border)]">
            No private research documents uploaded yet. Add travel notes to combine with web research.
          </div>
        ) : (
          <div className="space-y-2">
            {documents.map((doc) => (
              <div
                key={doc.document_id}
                className="p-3.5 rounded-2xl border border-[var(--border)] bg-[var(--surface-muted)]/30 hover:bg-[var(--surface-muted)]/60 transition-all flex items-center justify-between gap-3 text-xs"
              >
                <div className="flex items-center gap-3 min-w-0">
                  <div className="w-9 h-9 rounded-xl bg-[var(--primary-soft)] text-[var(--primary)] flex items-center justify-center shrink-0">
                    <FileText className="w-4 h-4" />
                  </div>
                  <div className="min-w-0">
                    <div className="font-semibold text-[var(--text-primary)] truncate font-mono">
                      {doc.title || doc.filename}
                    </div>
                    <div className="text-[11px] text-[var(--text-muted)] flex items-center gap-2 mt-0.5 font-mono">
                      <span>{formatFileSize(doc.file_size_bytes)}</span>
                      <span>•</span>
                      <span>Uploaded {formatDate(doc.uploaded_at)}</span>
                    </div>
                  </div>
                </div>

                <div className="flex items-center gap-2 shrink-0">
                  <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-[11px] font-medium bg-[var(--status-success)]/10 text-[var(--status-success)] border border-[var(--status-success)]/20">
                    <CheckCircle2 className="w-3 h-3" />
                    Indexed
                  </span>

                  <button
                    type="button"
                    onClick={() => handleDelete(doc.document_id, doc.filename)}
                    disabled={deletingId === doc.document_id}
                    title="Delete document"
                    className="w-8 h-8 rounded-xl border border-[var(--border)] text-[var(--text-muted)] hover:text-[var(--status-danger)] hover:border-[var(--status-danger)]/30 hover:bg-[var(--surface)] flex items-center justify-center transition-colors cursor-pointer"
                  >
                    <Trash2 className="w-3.5 h-3.5" />
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
};
