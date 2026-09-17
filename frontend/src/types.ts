export interface QualificationReport {
  summary: string;
  is_clear: boolean;
  request_type: 'ANALYSE_ONLY' | 'BUGFIX' | 'FEATURE' | 'DESIGN_AND_DEV';
  questions: string[];
}
