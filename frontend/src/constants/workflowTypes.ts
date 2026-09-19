import type { QualificationReport } from '../types';

// Dérivé de QualificationReport['request_type'] (pas redéclaré indépendamment) : ce sont
// exactement les mêmes catégories, et cette dépendance de type force à revoir ce fichier
// (au minimum ce type) si ce dernier change un jour, plutôt que de laisser les deux unions
// diverger silencieusement en ne restant identiques que par coïncidence.
export type WorkflowType = 'AUTO' | QualificationReport['request_type'];

interface WorkflowTypeOption {
  value: WorkflowType;
  icon: string;
  label: string;
}

// 'AUTO' correspond au comportement historique (avant ce sélecteur manuel) : passe par
// /api/qualify pour laisser qualification_agent détecter la catégorie à partir du texte
// de la demande, avec une éventuelle étape de clarification si elle est ambiguë. Les 4
// autres valeurs court-circuitent cette détection pour exécuter directement le workflow
// choisi (voir sendMessage dans useConversation.ts) : elles reprennent exactement les
// catégories de backend/tasksquestion.yaml (voir aussi WORKFLOW_STEPS, qui partage ces
// mêmes clés côté affichage de la progression).
export const WORKFLOW_TYPE_OPTIONS: WorkflowTypeOption[] = [
  { value: 'AUTO', icon: '🤖', label: 'Détection automatique' },
  { value: 'ANALYSE_ONLY', icon: '🔍', label: 'Analyse uniquement' },
  { value: 'BUGFIX', icon: '🐛', label: 'Correction de bug' },
  { value: 'FEATURE', icon: '✨', label: 'Nouvelle fonctionnalité' },
  { value: 'DESIGN_AND_DEV', icon: '🎨', label: 'Design + développement complet' },
];

// Utilisé pour "Relancer cette demande" (ChatMessage/Studio) : turn.workflow est un simple
// `string` (valeur reçue du backend, potentiellement absente/inattendue), donc jamais
// assignable tel quel à WorkflowType sans cette vérification à l'exécution.
export function isWorkflowType(value: string): value is WorkflowType {
  return WORKFLOW_TYPE_OPTIONS.some((opt) => opt.value === value);
}
