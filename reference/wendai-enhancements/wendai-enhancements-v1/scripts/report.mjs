import { mkdir, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { createMerchants } from '../data/fixtures.mjs';
import { computeEnhancements } from '../core/adapter.mjs';
import { METHOD_LABELS } from '../core/forecast.mjs';

const root=fileURLToPath(new URL('../',import.meta.url));
const merchants=createMerchants();
const summary=merchants.map(m=>{
  const r=computeEnhancements(m);
  return {merchant:m.name,merchantId:m.merchantId,seed:m.seed,historyDays:m.history.length,
    historicalTransactions:m.transactions.length,selected:r.forecast.selected,
    evaluation:['inflowCents','outflowCents'].flatMap(field=>r.forecast.results[field].map(x=>({
      field,method:x.method,label:METHOD_LABELS[x.method],selected:r.forecast.selected[field]===x.method,
      validation:x.validation.metrics,holdout:x.holdout.metrics,holdoutByHorizon:x.holdout.byHorizon,
      validationOrigins:x.validation.folds.length,holdoutOrigins:x.holdout.folds.length}))),
    nextSevenDays:r.forecast.daily.map(({sourceRefs,...rest})=>({...rest,sourceCount:sourceRefs.length})),
    settlement:r.settlement,reserve:r.reserveAdvice,
    balanceCases:{baseline:{status:r.baseline.status,withdrawableCents:r.baseline.maxWithdrawalCents},
      joint:{status:r.stress.status,withdrawableCents:r.stress.maxWithdrawalCents,
        paymentGapCents:r.stress.paymentGapCents,bufferGapCents:r.stress.bufferGapCents}},
    reserveStressBlocks:r.blocks.map(({sourceRefs,...rest})=>rest)};
});
const output={disclosure:'全为固定种子模拟数据；没有真实商户、银行接口或真实预测准确率。样本经验分位数不等于未来概率。',
  generatorVersion:'1.0.0',node:process.version,selectionDays:56,historyDays:84,
  horizon:7,initialTrainDays:28,alphaBps:3000,merchants:summary};
await mkdir(root+'data',{recursive:true});
await writeFile(root+'data/simulated-merchants.json',JSON.stringify(merchants,null,2));
await writeFile(root+'data/evaluation.json',JSON.stringify(output,null,2));
const lines=['# 模拟数据实际运行结果','',output.disclosure,'',
  '方法在前56天内选择并冻结；后28天滚动检验，每个方法22个起点、154个日预测误差。窗口重叠，不当成独立样本。','',
  '| 商户 | 指标 | 方法 | 选中 | 留出段MAE 元 | 留出段RMSE 元 |','|---|---|---|---|---:|---:|'];
for(const m of summary) for(const e of m.evaluation) lines.push(`| ${m.merchant} | ${e.field==='inflowCents'?'到账':'日常采购'} | ${e.label} | ${e.selected?'是':'否'} | ${(e.holdout.maeCents/100).toFixed(2)} | ${(e.holdout.rmseCents/100).toFixed(2)} |`);
lines.push('','## 留底建议','', '| 商户 | 当前留底 元 | 历史最大累计不利误差的经验q90 元 | 向上取整后的建议 元 |','|---|---:|---:|---:|');
for(const m of summary) lines.push(`| ${m.merchant} | ${(m.reserve.currentReserveCents/100).toFixed(2)} | ${(m.reserve.empiricalErrorCents/100).toFixed(2)} | ${(m.reserve.suggestedReserveCents/100).toFixed(2)} |`);
lines.push('','水果店包含人为设定的经营突变；高留底建议用来展示模型失效风险，不能解释为应保证准备到该金额即可安全。',
  '期末余额的情景比较均在 x=0 下进行；预测金额不进入确定性可提用计算。','');
await writeFile(root+'docs/模拟运行结果.md',lines.join('\n'));
console.table(summary.flatMap(m=>m.evaluation.map(e=>({商户:m.merchant,目标:e.field,方法:e.label,选中:e.selected,
  MAE元:(e.holdout.maeCents/100).toFixed(2),RMSE元:(e.holdout.rmseCents/100).toFixed(2)}))));
console.log('已生成 data/simulated-merchants.json、data/evaluation.json、docs/模拟运行结果.md');
