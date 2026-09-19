export const labelize = (value: string) => value
  .replace(/([a-z])([A-Z])/g, "$1 $2")
  .replaceAll("_", " ")
  .replace(/\b\w/g, (char) => char.toUpperCase());
export const money = (value: number, currency: string) => new Intl.NumberFormat("en-SG", { style: "currency", currency, minimumFractionDigits: 2 }).format(value);
