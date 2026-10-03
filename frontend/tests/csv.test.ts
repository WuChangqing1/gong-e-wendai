/**
 * CSV 解析与编码识别测试。
 *
 * 重点：缺失的日期不能被静默当成 0，因此解析层必须如实保留「有哪几天」，
 * 完整性由用户在界面上明确确认。
 */

import { describe, expect, it } from 'vitest';

import { parseCsvText } from '@/utils/csv';

describe('CSV 解析', () => {
  it('解析标准中文表头', () => {
    const table = parseCsvText(
      '日期,收入,支出,来源\n2026-01-01,100.00,30.00,日结\n2026-01-02,200.00,0,日结\n',
    );
    expect(table.columns).toEqual(['日期', '收入', '支出', '来源']);
    expect(table.rows).toHaveLength(2);
    expect(table.rows[0]['收入']).toBe('100.00');
  });

  it('去掉 UTF-8 BOM，避免首列名匹配失败', () => {
    const table = parseCsvText('\uFEFF日期,收入\n2026-01-01,100\n');
    expect(table.columns[0]).toBe('日期');
  });

  it('支持引号包裹的字段与转义引号', () => {
    const table = parseCsvText('日期,来源\n2026-01-01,"日结,含备注"\n2026-01-02,"他说""收到了"""\n');
    expect(table.rows[0]['来源']).toBe('日结,含备注');
    expect(table.rows[1]['来源']).toBe('他说"收到了"');
  });

  it('兼容制表符分隔', () => {
    const table = parseCsvText('日期\t收入\n2026-01-01\t100\n');
    expect(table.columns).toEqual(['日期', '收入']);
    expect(table.rows[0]['收入']).toBe('100');
  });

  it('跳过空行', () => {
    const table = parseCsvText('日期,收入\n\n2026-01-01,100\n\n');
    expect(table.rows).toHaveLength(1);
  });

  it('缺少某列时该字段为空字符串而不是抛错', () => {
    const table = parseCsvText('日期,收入\n2026-01-01\n');
    expect(table.rows[0]['收入']).toBe('');
  });

  it('空文件返回空结构', () => {
    expect(parseCsvText('')).toEqual({ columns: [], rows: [] });
  });
});
