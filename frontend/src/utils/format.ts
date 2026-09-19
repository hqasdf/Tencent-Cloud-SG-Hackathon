export const labelize = (value: string) => value.replaceAll("_", " ").replace(/\b\w/g, (char) => char.toUpperCase());
export const money = (value: number, currency: string) => new Intl.NumberFormat("en-SG", { style: "currency", currency, minimumFractionDigits: 2 }).format(value);
