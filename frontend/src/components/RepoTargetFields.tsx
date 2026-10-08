import { memo } from 'react';
import Card from './ui/Card';

interface RepoTargetFieldsProps {
  repoOwner: string;
  repoName: string;
  baseBranch: string;
  onRepoOwnerChange: (value: string) => void;
  onRepoNameChange: (value: string) => void;
  onBaseBranchChange: (value: string) => void;
  disabled: boolean;
}

function RepoTargetFields({
  repoOwner,
  repoName,
  baseBranch,
  onRepoOwnerChange,
  onRepoNameChange,
  onBaseBranchChange,
  disabled,
}: RepoTargetFieldsProps) {
  return (
    <Card className="stack--section">
      <p className="history-title">🔗 Repository GitHub cible (optionnel) :</p>
      <div className="row--wrap-fields repo-fields">
        <input
          type="text"
          className="field"
          placeholder="owner (ex: titimaes26-lab)"
          aria-label="Propriétaire du repository"
          disabled={disabled}
          value={repoOwner}
          onChange={(e) => onRepoOwnerChange(e.target.value)}
        />
        <input
          type="text"
          className="field"
          placeholder="repo (ex: myIACrew)"
          aria-label="Nom du repository"
          disabled={disabled}
          value={repoName}
          onChange={(e) => onRepoNameChange(e.target.value)}
        />
        <input
          type="text"
          className="field field--branch"
          placeholder="branche de base"
          aria-label="Branche de base"
          disabled={disabled}
          value={baseBranch}
          onChange={(e) => onBaseBranchChange(e.target.value)}
        />
      </div>
      <p className="muted text-sm hint-text">
        Si renseigné, les agents liront le code depuis ce repository et proposeront leurs changements via une Pull Request.
      </p>
    </Card>
  );
}

// memo() : ChatInput.tsx enveloppe déjà ses 3 handlers onXChange dans useCallback (références
// stables) précisément pour que cette mémoïsation ait un effet réel — sinon chaque frappe dans
// la zone de message de ChatInput (un state qui ne concerne pas ce composant) le re-rendrait
// quand même.
export default memo(RepoTargetFields);
