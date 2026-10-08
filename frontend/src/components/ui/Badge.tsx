import type { HTMLAttributes } from 'react';

export type BadgeTone = 'info' | 'success' | 'danger' | 'warning';

export default function Badge({ tone = 'info', className, ...rest }: HTMLAttributes<HTMLSpanElement> & { tone?: BadgeTone }) {
  const classes = ['badge', tone !== 'info' && `badge--${tone}`, className].filter(Boolean).join(' ');
  return <span className={classes} {...rest} />;
}
