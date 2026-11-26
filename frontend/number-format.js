// Consistent display precision for the calculated operating point.
// This is presentation-only: API values and calculation precision remain unchanged.
const operatingPointNumberFormat = new Intl.NumberFormat("ru-RU", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

export function formatOperatingPoint(value) {
  return Number.isFinite(value) ? operatingPointNumberFormat.format(value) : "—";
}
