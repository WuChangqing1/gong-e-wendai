/**
 * 产品文案与 UI 约束测试。
 *
 * 这些断言保证产品长期不出现"自我降级"文案，并保证风险不只靠颜色表达。
 */

import { describe, expect, it } from 'vitest';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join, resolve } from 'node:path';

const SRC_DIR = resolve(__dirname, '..', 'src');

const FORBIDDEN_WORDS = [
  '演示版',
  '模拟系统',
  '模拟银行',
  '模拟数据',
  '仅供展示',
  '比赛展示',
  '原型页面',
  '概念验证',
  'demo',
];

// 技术术语不应出现在面向用户的界面文案里
const FORBIDDEN_TECH_TERMS = ['CVaR', '线性规划', 'CashEvent', 'maxWithdrawable', 'cashKey'];

function walk(dir: string): string[] {
  const entries = readdirSync(dir);
  const files: string[] = [];
  for (const entry of entries) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) {
      files.push(...walk(full));
    } else if (/\.(ts|tsx)$/.test(entry)) {
      files.push(full);
    }
  }
  return files;
}

const files = walk(SRC_DIR);

/** 去掉注释，只检查真正会渲染给用户的字符串。 */
function userFacingText(content: string): string {
  return content
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/^\s*\/\/.*$/gm, '')
    .replace(/\/\/.*$/gm, '');
}

/** 只提取会渲染成界面文案的部分：JSX 文本、字符串字面量、模板字面量。 */
function uiStrings(content: string): string[] {
  const text = userFacingText(content);
  const found: string[] = [];
  // 普通字符串与模板字面量
  for (const match of text.matchAll(/'([^'\\\n]*)'|"([^"\\\n]*)"|`([^`\\]*)`/g)) {
    found.push(match[1] ?? match[2] ?? match[3] ?? '');
  }
  // JSX 文本节点：>中文…<
  for (const match of text.matchAll(/>([^<>{}\n]{2,})</g)) {
    found.push(match[1]);
  }
  return found;
}

describe('产品文案约束', () => {
  it('页面中不出现自我降级文案', () => {
    const offenders: string[] = [];
    for (const file of files) {
      const text = userFacingText(readFileSync(file, 'utf8'));
      for (const word of FORBIDDEN_WORDS) {
        if (text.includes(word) && !file.includes('labels.ts')) {
          offenders.push(`${file}: ${word}`);
        }
      }
    }
    expect(offenders).toEqual([]);
  });

  it('界面文案不直接暴露技术术语', () => {
    const offenders: string[] = [];
    for (const file of files) {
      // 类型定义、API 客户端与工具模块属于技术语境，允许出现技术术语
      if (file.includes('types') || file.includes('api/') || file.includes('utils/')) continue;
      for (const value of uiStrings(readFileSync(file, 'utf8'))) {
        for (const term of FORBIDDEN_TECH_TERMS) {
          if (value.includes(term)) offenders.push(`${file}: ${term}`);
        }
      }
    }
    expect(offenders).toEqual([]);
  });
});

describe('术语映射', () => {
  it('面向用户使用业务语言而不是技术语言', async () => {
    const labels = await import('@/utils/labels');
    expect(labels.DIRECTION_LABELS.inflow).toBe('收入');
    expect(labels.STATE_LABELS.scheduled).toBe('计划中');
    expect(labels.STATUS_LABELS.PAYMENT_GAP).toBe('存在付款缺口');
    expect(labels.ROLE_LABELS.merchant).toBe('经营者');
  });
});

describe('状态表达', () => {
  it('状态标签同时包含图标与文字，不只依赖颜色', async () => {
    const { render, screen } = await import('@testing-library/react');
    const { StatusTag } = await import('@/components/ui');
    render(<StatusTag tone="danger">存在付款缺口</StatusTag>);
    const node = screen.getByText('存在付款缺口');
    expect(node).toBeInTheDocument();
    expect(node.textContent).toContain('×');
  });
});
