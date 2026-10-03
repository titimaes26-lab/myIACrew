import type { HTMLAttributes } from 'react';

interface CardProps extends HTMLAttributes<HTMLDivElement> {
  flat?: boolean;
  tight?: boolean;
  selected?: boolean;
}

export default function Card({ flat, tight, selected, className, ...rest }: CardProps) {
  const classes = ['card', flat && 'card--flat', tight && 'card--tight', selected && 'card--selected', className]
    .filter(Boolean).join(' ');
  return <div className={classes} {...rest} />;
}
