import type { ButtonHTMLAttributes } from 'react';

export type ButtonVariant = 'secondary' | 'primary' | 'danger' | 'info' | 'ghost';
export type ButtonSize = 'md' | 'sm' | 'chip';

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  // Bouton plus large (zone de saisie : Envoyer / Annuler).
  block?: boolean;
}

// Bouton de l'application : l'apparence (variantes, survol, focus, désactivé) vit dans styles/ui.css.
// type="button" par défaut : un bouton dans un formulaire ne doit pas le soumettre par accident.
export default function Button({ variant = 'secondary', size = 'md', block = false, className, type = 'button', ...rest }: ButtonProps) {
  const classes = [
    'btn',
    variant !== 'secondary' && `btn--${variant}`,
    size !== 'md' && `btn--${size}`,
    block && 'btn--block',
    className,
  ].filter(Boolean).join(' ');
  return <button type={type} className={classes} {...rest} />;
}
