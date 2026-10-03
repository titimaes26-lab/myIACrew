import type { HTMLAttributes } from 'react';

export type AlertTone = 'danger' | 'warning' | 'info';

// role="alert" pour une erreur (annoncée tout de suite), role="status" pour une information ou un
// avertissement non bloquant (annoncé poliment) — jamais le même rôle pour les deux.
export default function Alert({ tone = 'danger', className, role, ...rest }: HTMLAttributes<HTMLDivElement> & { tone?: AlertTone }) {
  const classes = ['alert', tone !== 'danger' && `alert--${tone}`, className].filter(Boolean).join(' ');
  return <div role={role ?? (tone === 'danger' ? 'alert' : 'status')} className={classes} {...rest} />;
}
