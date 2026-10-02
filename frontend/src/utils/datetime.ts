/**
 * 时间工具。
 *
 * 数据库与接口一律使用 UTC；界面统一按 Asia/Shanghai 展示。
 */

import dayjs from 'dayjs';
import utc from 'dayjs/plugin/utc';
import timezone from 'dayjs/plugin/timezone';
import relativeTime from 'dayjs/plugin/relativeTime';
import 'dayjs/locale/zh-cn';

dayjs.extend(utc);
dayjs.extend(timezone);
dayjs.extend(relativeTime);
dayjs.locale('zh-cn');

export const APP_TIMEZONE = 'Asia/Shanghai';

export function toLocal(value: string | Date | null | undefined) {
  if (!value) return null;
  return dayjs(value).tz(APP_TIMEZONE);
}

/** 2025-10-03 10:00 */
export function formatDateTime(value: string | Date | null | undefined): string {
  const local = toLocal(value);
  return local ? local.format('YYYY-MM-DD HH:mm') : '--';
}

/** 10-03 10:00 */
export function formatShortDateTime(value: string | Date | null | undefined): string {
  const local = toLocal(value);
  return local ? local.format('MM-DD HH:mm') : '--';
}

/** 2025-10-03 */
export function formatDate(value: string | Date | null | undefined): string {
  const local = toLocal(value);
  return local ? local.format('YYYY-MM-DD') : '--';
}

/** 10月3日 */
export function formatChineseDate(value: string | Date | null | undefined): string {
  const local = toLocal(value);
  return local ? local.format('M月D日') : '--';
}

export function formatWeekday(value: string | Date | null | undefined): string {
  const local = toLocal(value);
  return local ? local.format('ddd') : '--';
}

/** 相对时间：3 小时前 */
export function formatRelative(value: string | Date | null | undefined): string {
  const local = toLocal(value);
  return local ? local.fromNow() : '--';
}

export function isToday(value: string | Date | null | undefined): boolean {
  const local = toLocal(value);
  return local ? local.isSame(dayjs().tz(APP_TIMEZONE), 'day') : false;
}

/** 当前时间的 ISO 字符串（UTC）。 */
export function nowIso(): string {
  return dayjs().utc().toISOString();
}

/** 在给定时间上加天数，返回 ISO 字符串。 */
export function addDaysIso(value: string | Date, days: number): string {
  return dayjs(value).add(days, 'day').utc().toISOString();
}

export { dayjs };
