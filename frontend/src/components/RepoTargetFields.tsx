interface RepoTargetFieldsProps {
  repoOwner: string;
  repoName: string;
  baseBranch: string;
  onRepoOwnerChange: (value: string) => void;
  onRepoNameChange: (value: string) => void;
  onBaseBranchChange: (value: string) => void;
  disabled: boolean;
}

export default function RepoTargetFields({
  repoOwner,
  repoName,
  baseBranch,
  onRepoOwnerChange,
  onRepoNameChange,
  onBaseBranchChange,
  disabled,
}: RepoTargetFieldsProps) {
  return (
    <div style={{ marginTop: '20px', backgroundColor: '#fff', padding: '15px', borderRadius: '6px', border: '1px solid #e1e4e8' }}>
      <p style={{ margin: '0 0 10px 0', fontWeight: 'bold' }}>🔗 Repository GitHub cible (optionnel) :</p>
      <div style={{ display: 'flex', gap: '10px', flexWrap: 'wrap' }}>
        <input
          type="text"
          placeholder="owner (ex: titimaes26-lab)"
          aria-label="Propriétaire du repository"
          disabled={disabled}
          value={repoOwner}
          onChange={(e) => onRepoOwnerChange(e.target.value)}
          style={{ flex: '1 1 180px', padding: '8px', borderRadius: '4px', border: '1px solid #ccc' }}
        />
        <input
          type="text"
          placeholder="repo (ex: myIACrew)"
          aria-label="Nom du repository"
          disabled={disabled}
          value={repoName}
          onChange={(e) => onRepoNameChange(e.target.value)}
          style={{ flex: '1 1 180px', padding: '8px', borderRadius: '4px', border: '1px solid #ccc' }}
        />
        <input
          type="text"
          placeholder="branche de base"
          aria-label="Branche de base"
          disabled={disabled}
          value={baseBranch}
          onChange={(e) => onBaseBranchChange(e.target.value)}
          style={{ flex: '1 1 140px', padding: '8px', borderRadius: '4px', border: '1px solid #ccc' }}
        />
      </div>
      <p style={{ margin: '8px 0 0 0', fontSize: '13px', color: '#666' }}>
        Si renseigné, les agents liront le code depuis ce repository et proposeront leurs changements via une Pull Request.
      </p>
    </div>
  );
}
