export const labelize = (value: string) => value
  .replace(/([a-z])([A-Z])/g, "$1 $2")
  .replaceAll("_", " ")
  .replace(/\b\w/g, (char) => char.toUpperCase());

/**
 * Turn a SCREAMING_SNAKE_CASE enum code into UI copy.
 *
 * `labelize` alone is not enough: it capitalises word starts but leaves the rest
 * of each word alone, so "CONTRADICTORY_EVIDENCE" comes out as "CONTRADICTORY
 * EVIDENCE". Reason codes are read by operators, not by developers, so they get
 * lowercased first.
 */
export const humanize = (value: string) => labelize(value.toLowerCase());

export const money = (value: number, currency: string) => new Intl.NumberFormat("en-SG", { style: "currency", currency, minimumFractionDigits: 2 }).format(value);
