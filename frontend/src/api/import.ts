/** CSV 导入接口。 */

import { get, post, upload } from '@/api/client';
import type { ImportCommitResult, ImportPreview } from '@/types';

export const importApi = {
  upload: (file: File, fileType: 'transaction' | 'payment_plan') => {
    const formData = new FormData();
    formData.append('file', file);
    formData.append('file_type', fileType);
    return upload<ImportPreview>('/imports/csv/preview', formData);
  },
  remap: (batchId: string, mapping: Record<string, string>) =>
    post<ImportPreview>('/imports/csv/remap', { batch_id: batchId, mapping }),
  commit: (batchId: string) =>
    post<ImportCommitResult>('/imports/csv/commit', { batch_id: batchId }),
  templates: () =>
    get<{ transaction: string; payment_plan: string }>('/imports/csv/templates'),
};
