/** All monetary values are safe integer CNY cents. Times are UTC epoch ms; days use UTC+08. */
export const DAY_MS = 86400000;
export function assert(condition, code) { if (!condition) throw new Error(code); }
export function integer(value, code = 'INTEGER_REQUIRED', minimum = 0) {
  assert(Number.isSafeInteger(value) && value >= minimum, code); return value;
}
export function sum(values) {
  return values.reduce((a, b) => integer(a + integer(b, 'AMOUNT', -Number.MAX_SAFE_INTEGER),
    'SUM_OVERFLOW', -Number.MAX_SAFE_INTEGER), 0);
}
export function roundedRatio(numerator, denominator) {
  assert(typeof numerator === 'bigint' && numerator >= 0n && denominator > 0n, 'RATIO');
  const n = (numerator + denominator / 2n) / denominator;
  assert(n <= BigInt(Number.MAX_SAFE_INTEGER), 'OVERFLOW'); return Number(n);
}
export function dayIndex(day) {
  assert(typeof day === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(day), 'DAY_FORMAT');
  const at = Date.parse(day + 'T00:00:00Z');
  assert(Number.isFinite(at) && new Date(at).toISOString().slice(0, 10) === day, 'INVALID_DAY');
  return Math.floor(at / DAY_MS);
}
export function addDays(day, count) {
  integer(count, 'DAY_OFFSET', -100000);
  return new Date((dayIndex(day) + count) * DAY_MS).toISOString().slice(0, 10);
}
export function beijingDay(at) {
  integer(at, 'TIMESTAMP'); return new Date(at + 8 * 3600000).toISOString().slice(0, 10);
}
export function atBeijing(day, clock = '00:00') {
  dayIndex(day); assert(/^([01]\d|2[0-3]):[0-5]\d$/.test(clock), 'CLOCK');
  return Date.parse(`${day}T${clock}:00+08:00`);
}
export function median(values) {
  assert(values.length > 0, 'EMPTY_MEDIAN');
  const v = [...values].map(x => integer(x, 'AMOUNT')).sort((a, b) => a - b);
  const m = Math.floor(v.length / 2);
  return v.length % 2 ? v[m] : roundedRatio(BigInt(v[m-1]) + BigInt(v[m]), 2n);
}
/** Nearest-rank empirical quantile. q is a design parameter, NOT a future coverage guarantee. */
export function quantile(values, q) {
  assert(Number.isFinite(q) && q > 0 && q <= 1 && values.length > 0, 'QUANTILE');
  const v = [...values].map(x => integer(x, 'QUANTILE_VALUE')).sort((a,b) => a-b);
  return v[Math.ceil(q * v.length) - 1];
}
export function yuan(cents) { return (cents / 100).toFixed(2); }
