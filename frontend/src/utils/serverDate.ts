const HAS_TIMEZONE = /[zZ]|[+-]\d{2}:\d{2}$/;

// Le backend sérialise des datetimes naïfs (datetime.utcnow(), sans suffixe de fuseau).
// Sans "Z"/offset explicite, `new Date(...)` interprète la chaîne comme une heure locale
// au lieu d'UTC, faussant tous les calculs d'heure affichée et de durée écoulée.
export function toServerDate(iso: string): Date {
  return new Date(HAS_TIMEZONE.test(iso) ? iso : `${iso}Z`);
}
