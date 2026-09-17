export interface QualificationReport {
  summary: string;
  is_clear: boolean;
  request_type: 'ANALYSE_ONLY' | 'BUGFIX' | 'FEATURE' | 'DESIGN_AND_DEV';
  questions: string[];
}

export interface ExecutionHistoryEntry {
  id: number;
  user_request: string;
  workflow: string;
  clarifications: string | null;
  result: string | null;
  status: 'running' | 'success' | 'failed';
  repo_owner: string | null;
  repo_name: string | null;
  created_at: string;
}
