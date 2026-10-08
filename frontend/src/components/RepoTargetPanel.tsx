import Button from './ui/Button';
import RepoTargetFields from './RepoTargetFields';
import type { RepoTargetSuggestion } from '../types';

interface RepoTargetPanelProps {
  repoOwner: string;
  repoName: string;
  baseBranch: string;
  suggestions: RepoTargetSuggestion[];
  onApplySuggestion: (suggestion: RepoTargetSuggestion) => void;
  onRepoOwnerChange: (value: string) => void;
  onRepoNameChange: (value: string) => void;
  onBaseBranchChange: (value: string) => void;
  disabled: boolean;
}

// Panneau déplié du repository cible : suggestions récentes (un clic remplit les trois champs) et champs de saisie.
export default function RepoTargetPanel({
  repoOwner, repoName, baseBranch, suggestions, onApplySuggestion, onRepoOwnerChange, onRepoNameChange, onBaseBranchChange, disabled,
}: RepoTargetPanelProps) {
  return (
    <>
      {suggestions.length > 0 && (
        <div className="row">
          {suggestions.map((s) => (
            <Button
              key={`${s.repo_owner}/${s.repo_name}@${s.base_branch}`}
              size="chip"
              onClick={() => onApplySuggestion(s)}
              disabled={disabled}
            >
              {s.repo_owner}/{s.repo_name}{s.base_branch ? `@${s.base_branch}` : ''}
            </Button>
          ))}
        </div>
      )}
      <RepoTargetFields
        repoOwner={repoOwner}
        repoName={repoName}
        baseBranch={baseBranch}
        onRepoOwnerChange={onRepoOwnerChange}
        onRepoNameChange={onRepoNameChange}
        onBaseBranchChange={onBaseBranchChange}
        disabled={disabled}
      />
    </>
  );
}
