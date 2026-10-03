import type { BadgeTone } from '../components/ui/Badge';

// Teinte du badge d'un verdict QA ou d'une confiance (voir parseAgentMetadata). Les négations sont testées
// AVANT « GO » : « NO_GO » / « NON-GO » contiennent « GO » et s'afficheraient sinon en vert.
export function statusTone(status: string): BadgeTone {
  const text = status.toUpperCase();
  if (/(^|[^A-Z])NON?[\s_-]?GO/.test(text) || text.includes('NON')) return 'danger';
  if (text.includes('RESERVE') || text.includes('RÉSERVE')) return 'warning';
  if (/(^|[^A-Z])GO([^A-Z]|$)/.test(text)) return 'success';
  return 'info';
}
