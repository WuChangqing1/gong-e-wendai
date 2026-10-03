/**
 * 决策结论文案与可提用金额语义测试。
 *
 * 这些断言锁定四条产品底线：
 * 1. 状态不同，首屏结论结构必须不同；
 * 2. PAYMENT_GAP / BELOW_BUFFER 下不得出现「资金安排可行」这类文案；
 * 3. 可提用金额为 0 不等于资金安排可行，必须单独说明；
 * 4. 资料不完整时首屏显示文字，而不是把 null 显示成 ¥0.00。
 */

import { describe, expect, it } from 'vitest';

import {
  FEASIBLE_ZERO_DETAIL,
  STATUS_LABELS,
  STATUS_TONE,
  decisionCopy,
} from '@/utils/labels';
import type { AnalysisStatus } from '@/types';

const ALL_STATUSES: AnalysisStatus[] = [
  'FEASIBLE',
  'PAYMENT_GAP',
  'BELOW_BUFFER',
  'INPUT_INCOMPLETE',
];

describe('决策结论文案', () => {
  it('四种状态都有独立结论，且标题互不相同', () => {
    const headlines = ALL_STATUSES.map((status) => decisionCopy(status).headline);
    expect(new Set(headlines).size).toBe(ALL_STATUSES.length);
    expect(decisionCopy('FEASIBLE').headline).toBe('今日最多可提用');
    expect(decisionCopy('PAYMENT_GAP').headline).toBe('当前存在付款缺口');
    expect(decisionCopy('BELOW_BUFFER').headline).toBe('低于经营留底');
    expect(decisionCopy('INPUT_INCOMPLETE').headline).toBe('暂不能计算');
  });

  it('只允许 FEASIBLE 展示可提用金额', () => {
    expect(decisionCopy('FEASIBLE').amountKind).toBe('withdrawable');
    expect(decisionCopy('PAYMENT_GAP').amountKind).toBe('payment_gap');
    expect(decisionCopy('BELOW_BUFFER').amountKind).toBe('buffer_gap');
    expect(decisionCopy('INPUT_INCOMPLETE').amountKind).toBe('none');
  });

  it('缺口状态一律标记为负面结论', () => {
    expect(decisionCopy('FEASIBLE').negative).toBe(false);
    expect(decisionCopy('PAYMENT_GAP').negative).toBe(true);
    expect(decisionCopy('BELOW_BUFFER').negative).toBe(true);
  });

  it('缺口状态下不得出现「可行 / 满足全部情景」表述', () => {
    for (const status of ['PAYMENT_GAP', 'BELOW_BUFFER'] as AnalysisStatus[]) {
      const copy = decisionCopy(status);
      const text = `${copy.headline}${copy.detail}`;
      expect(text).not.toContain('资金安排可行');
      expect(text).not.toContain('满足全部情景');
      expect(text).not.toContain('0 元即可满足');
    }
  });

  it('PAYMENT_GAP 明确说明「即使不提用也存在缺口」', () => {
    expect(decisionCopy('PAYMENT_GAP').detail).toContain('即使不提用家庭资金');
  });

  it('INPUT_INCOMPLETE 说明需要补充资料，而不是给出金额', () => {
    expect(decisionCopy('INPUT_INCOMPLETE').detail).toContain('尚未确认');
  });

  it('可提用为 0 时使用专用文案，不描述为安全提用', () => {
    expect(FEASIBLE_ZERO_DETAIL).toContain('没有额外资金适合用于家庭提用');
    expect(FEASIBLE_ZERO_DETAIL).not.toContain('仍能满足已确认经营付款和留底要求');
  });
});

describe('状态标签与色彩', () => {
  it('FEASIBLE 使用 ok 语义色，缺口使用危险色', () => {
    expect(STATUS_TONE.FEASIBLE).toBe('ok');
    expect(STATUS_TONE.PAYMENT_GAP).toBe('danger');
    expect(STATUS_TONE.BELOW_BUFFER).toBe('warning');
    expect(STATUS_TONE.INPUT_INCOMPLETE).toBe('info');
  });

  it('不存在旧的 OK 状态定义', () => {
    expect(Object.keys(STATUS_LABELS)).toEqual(ALL_STATUSES);
    expect('OK' in STATUS_LABELS).toBe(false);
    expect('OK' in STATUS_TONE).toBe(false);
  });
});
