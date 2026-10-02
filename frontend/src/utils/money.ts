/**
 * 金额与时间格式化。
 *
 * 所有金额在系统内部都是整数分（cents）。前端只做展示格式化，
 * 绝不使用浮点数进行核心金额计算。
 */

const YUAN_PATTERN = /^[+-]?\d+(?:\.\d+)?$/;

/** 把用户输入的元金额转换为整数分。非法输入返回 null。 */
export function yuanToCents(input: string | number | null | undefined): number | null {
  if (input === null || input === undefined) return null;
  const raw = String(input).trim().replace(/[,\s，￥¥]/g, '');
  if (!raw) return null;
  const normalised = raw.replace(/[０-９．－＋]/g, (ch) =>
    String.fromCharCode(ch.charCodeAt(0) - 0xfee0),
  );
  if (!YUAN_PATTERN.test(normalised)) return null;

  const negative = normalised.startsWith('-');
  const unsigned = normalised.replace(/^[+-]/, '');
  const [intPart, fracPart = ''] = unsigned.split('.');

  // 保留 3 位用于四舍五入，避免浮点误差（例如 0.005 -> 1 分）
  const frac = `${fracPart}000`.slice(0, 3);
  const base = Number(intPart) * 1000 + Number(frac);
  if (!Number.isFinite(base)) return null;

  // 整数分计算：第三位小数 >= 5 进位
  const cents = Math.floor((base + 5) / 10);
  return negative ? -cents : cents;
}

/** 整数分 -> 元字符串（不带货币符号），保留两位小数。 */
export function centsToYuanString(cents: number | null | undefined): string {
  if (cents === null || cents === undefined || !Number.isFinite(cents)) return '--';
  const negative = cents < 0;
  const value = Math.abs(Math.trunc(cents));
  const yuan = Math.floor(value / 100);
  const frac = value % 100;
  return `${negative ? '-' : ''}${yuan}.${String(frac).padStart(2, '0')}`;
}

/** ¥1,234.56 */
export function formatCny(cents: number | null | undefined, withSymbol = true): string {
  if (cents === null || cents === undefined || !Number.isFinite(cents)) return '--';
  const negative = cents < 0;
  const value = Math.abs(Math.trunc(cents));
  const yuan = Math.floor(value / 100);
  const frac = value % 100;
  const grouped = yuan.toLocaleString('zh-CN');
  return `${negative ? '-' : ''}${withSymbol ? '¥' : ''}${grouped}.${String(frac).padStart(2, '0')}`;
}

/** 首页大字金额：拆分整数与小数部分，方便排版对齐。 */
export function splitCny(cents: number | null | undefined): {
  symbol: string;
  integer: string;
  fraction: string;
  negative: boolean;
} {
  if (cents === null || cents === undefined || !Number.isFinite(cents)) {
    return { symbol: '¥', integer: '--', fraction: '', negative: false };
  }
  const negative = cents < 0;
  const value = Math.abs(Math.trunc(cents));
  return {
    symbol: '¥',
    integer: Math.floor(value / 100).toLocaleString('zh-CN'),
    fraction: String(value % 100).padStart(2, '0'),
    negative,
  };
}

/** 带正负号的金额，用于余额变化展示。 */
export function formatSigned(cents: number | null | undefined): string {
  if (cents === null || cents === undefined || !Number.isFinite(cents)) return '--';
  return `${cents > 0 ? '+' : ''}${formatCny(cents)}`;
}

export function formatPercent(value: number, digits = 1): string {
  if (!Number.isFinite(value)) return '--';
  return `${(value * 100).toFixed(digits)}%`;
}
