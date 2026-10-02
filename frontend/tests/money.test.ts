/**
 * 金额工具测试。
 *
 * 核心约束：所有金额计算在整数分上完成，绝不使用浮点。
 */

import { describe, expect, it } from 'vitest';

import {
  centsToYuanString,
  formatCny,
  formatSigned,
  splitCny,
  yuanToCents,
} from '@/utils/money';

describe('yuanToCents', () => {
  it('把元金额转换为整数分', () => {
    expect(yuanToCents('123.45')).toBe(12345);
    expect(yuanToCents('123')).toBe(12300);
    expect(yuanToCents('0.01')).toBe(1);
    expect(yuanToCents(123.45)).toBe(12345);
    expect(yuanToCents('1,234.56')).toBe(123456);
    expect(yuanToCents('￥88.00')).toBe(8800);
    expect(yuanToCents('１２３．４５')).toBe(12345);
    expect(yuanToCents('  12.30  ')).toBe(1230);
  });

  it('对非法输入返回 null', () => {
    expect(yuanToCents('')).toBeNull();
    expect(yuanToCents('abc')).toBeNull();
    expect(yuanToCents(null)).toBeNull();
    expect(yuanToCents(undefined)).toBeNull();
    expect(yuanToCents('1e3')).toBeNull();
    expect(yuanToCents('12.3.4')).toBeNull();
  });

  it('不使用浮点导致精度丢失', () => {
    // 0.1 + 0.2 的浮点问题在整数分上不存在
    const values = ['0.10', '0.20', '0.30'].map((item) => yuanToCents(item)!);
    expect(values.reduce((sum, item) => sum + item, 0)).toBe(60);
    expect(yuanToCents('0.005')).toBe(1);
    expect(yuanToCents('19.99')).toBe(1999);
    expect(yuanToCents('1000000.01')).toBe(100000001);
  });
});

describe('格式化', () => {
  it('centsToYuanString', () => {
    expect(centsToYuanString(12345)).toBe('123.45');
    expect(centsToYuanString(-5)).toBe('-0.05');
    expect(centsToYuanString(0)).toBe('0.00');
  });

  it('formatCny 带千分位', () => {
    expect(formatCny(12345)).toBe('¥123.45');
    expect(formatCny(123456)).toBe('¥1,234.56');
    expect(formatCny(-123456)).toBe('-¥1,234.56');
    expect(formatCny(null)).toBe('--');
    expect(formatCny(100000000)).toBe('¥1,000,000.00');
  });

  it('formatSigned 展示余额变化', () => {
    expect(formatSigned(1200)).toBe('+¥12.00');
    expect(formatSigned(-1200)).toBe('-¥12.00');
    expect(formatSigned(0)).toBe('¥0.00');
  });

  it('splitCny 用于首页大字排版', () => {
    expect(splitCny(120000)).toEqual({
      symbol: '¥',
      integer: '1,200',
      fraction: '00',
      negative: false,
    });
    expect(splitCny(null).integer).toBe('--');
  });
});
