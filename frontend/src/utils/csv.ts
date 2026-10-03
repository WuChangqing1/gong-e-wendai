/**
 * CSV 解析与编码识别。
 *
 * 后端已支持 UTF-8 / UTF-8 BOM / GB18030，前端在这一层做同样的判断，
 * 以便在上传前就能给出准确提示，并把结构化行发给后端预览接口。
 */

export interface CsvTable {
  columns: string[];
  rows: Record<string, string>[];
}

export interface DecodedCsv {
  text: string;
  encoding: 'utf-8' | 'utf-8-bom' | 'gb18030';
}

/** 常见中文环境下可能出现的编码。 */
const CANDIDATE_ENCODINGS = ['utf-8', 'gb18030'] as const;

/** 用 TextDecoder 尝试解码；不支持 gb18030 的环境会退回 utf-8。 */
function tryDecode(buffer: ArrayBuffer, encoding: string): string | null {
  try {
    const decoder = new TextDecoder(encoding, { fatal: true });
    return decoder.decode(buffer);
  } catch {
    return null;
  }
}

/**
 * 识别并解码 CSV 文件。
 *
 * 顺序：UTF-8 BOM → UTF-8 → GB18030。
 * 全部失败时给出可执行的提示，而不是抛出一个技术异常。
 */
export async function decodeCsvFile(file: File): Promise<DecodedCsv> {
  const buffer = await file.arrayBuffer();
  const bytes = new Uint8Array(buffer);

  if (bytes.length >= 3 && bytes[0] === 0xef && bytes[1] === 0xbb && bytes[2] === 0xbf) {
    const text = new TextDecoder('utf-8').decode(buffer);
    return { text: text.replace(/^\uFEFF/, ''), encoding: 'utf-8-bom' };
  }

  for (const encoding of CANDIDATE_ENCODINGS) {
    const text = tryDecode(buffer, encoding);
    if (text !== null && !text.includes('\uFFFD')) {
      return { text, encoding: encoding === 'gb18030' ? 'gb18030' : 'utf-8' };
    }
  }

  // 宽松模式兜底：允许替换字符，但提示用户检查编码
  const fallback = new TextDecoder('utf-8').decode(buffer);
  if (fallback.includes('\uFFFD')) {
    throw new Error(
      '文件编码无法识别（可能是 GBK 或 Excel 导出的旧格式）。请另存为 UTF-8 或 GB18030 后重试。',
    );
  }
  return { text: fallback, encoding: 'utf-8' };
}

/** 解析一行 CSV，支持引号包裹与转义引号。 */
function parseLine(line: string, delimiter: string): string[] {
  const cells: string[] = [];
  let current = '';
  let inQuotes = false;
  for (let index = 0; index < line.length; index += 1) {
    const char = line[index];
    if (inQuotes) {
      if (char === '"') {
        if (line[index + 1] === '"') {
          current += '"';
          index += 1;
        } else {
          inQuotes = false;
        }
      } else {
        current += char;
      }
    } else if (char === '"') {
      inQuotes = true;
    } else if (char === delimiter) {
      cells.push(current);
      current = '';
    } else {
      current += char;
    }
  }
  cells.push(current);
  return cells.map((item) => item.trim());
}

/** 把 CSV 文本解析为列名 + 行对象。 */
export function parseCsvText(text: string): CsvTable {
  const normalised = text.replace(/\r\n/g, '\n').replace(/\r/g, '\n');
  const lines = normalised.split('\n').filter((line) => line.trim().length > 0);
  if (!lines.length) return { columns: [], rows: [] };

  const delimiter = (lines[0].match(/\t/g)?.length ?? 0) > (lines[0].match(/,/g)?.length ?? 0)
    ? '\t'
    : ',';
  const columns = parseLine(lines[0], delimiter).map((item) => item.replace(/^\uFEFF/, ''));
  const rows: Record<string, string>[] = [];
  for (let index = 1; index < lines.length; index += 1) {
    const cells = parseLine(lines[index], delimiter);
    if (cells.every((cell) => !cell)) continue;
    const row: Record<string, string> = {};
    columns.forEach((column, position) => {
      row[column] = cells[position] ?? '';
    });
    rows.push(row);
  }
  return { columns, rows };
}
