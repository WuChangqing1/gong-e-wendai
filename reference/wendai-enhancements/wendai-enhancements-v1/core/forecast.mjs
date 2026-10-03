import { assert, integer, sum, roundedRatio, dayIndex, addDays, atBeijing, median } from './common.mjs';

export const METHODS = ['seasonal_naive', 'weekday_median', 'ses'];
export const METHOD_LABELS = { seasonal_naive:'上周同一天', weekday_median:'近四周同星期中位数', ses:'简单指数平滑' };
export const FIELDS = ['inflowCents', 'outflowCents'];

/** One merchant, complete calendar days; a missing day is never silently interpreted as zero. */
export function validateDaily(rows) {
  assert(Array.isArray(rows) && rows.length > 0, 'HISTORY_REQUIRED');
  const merchantId = rows[0].merchantId;
  assert(typeof merchantId === 'string' && merchantId.length > 0, 'MERCHANT_REQUIRED');
  rows.forEach((r, i) => {
    assert(r.merchantId === merchantId, 'MIXED_MERCHANTS');
    assert(r.complete === true, 'INCOMPLETE_DAY');
    const idx = dayIndex(r.day);
    if (i) assert(idx === dayIndex(rows[i-1].day) + 1, 'MISSING_OR_DUPLICATE_DAY');
    FIELDS.forEach(f => integer(r[f], 'HISTORY_AMOUNT'));
    assert(Array.isArray(r.sourceRefs) && r.sourceRefs.length > 0, 'HISTORY_SOURCE');
  });
  return rows;
}

/** Aggregate posted settlement receipts + routine purchases only; fixed obligations stay in the ledger. */
export function aggregateDaily(transactions, calendar, merchantId, asOf) {
  integer(asOf, 'ASOF');
  assert(Array.isArray(calendar) && calendar.length > 0, 'CALENDAR_REQUIRED');
  const ids = new Set(), keys = new Set();
  const rows = calendar.map(c => {
    assert(c.complete === true, 'INCOMPLETE_DAY');
    assert(atBeijing(addDays(c.day, 1)) <= asOf, 'DAY_NOT_CLOSED');
    assert(c.sourceRef, 'DAY_SOURCE');
    return {merchantId, day:c.day, complete:true, inflowCents:0, outflowCents:0, sourceRefs:[c.sourceRef]};
  });
  validateDaily(rows);
  const byDay = new Map(rows.map(r => [r.day, r]));
  for (const t of transactions.filter(t => t.merchantId === merchantId)) {
    assert(t.id && !ids.has(t.id), 'DUPLICATE_TRANSACTION'); ids.add(t.id);
    integer(t.version, 'TRANSACTION_VERSION', 1);
    assert(['planned','pending','posted','voided'].includes(t.status), 'TRANSACTION_STATUS');
    if (t.status !== 'posted') continue;
    integer(t.availableAt, 'AVAILABLE_AT'); integer(t.at, 'POSTED_AT');
    if (t.availableAt > asOf || t.at > asOf) continue;
    if (!['settlement','purchase'].includes(t.kind)) continue;
    assert(t.scope === 'business', 'FORECAST_SCOPE');
    assert(t.currency === 'CNY', 'CURRENCY'); integer(t.amountCents, 'TRANSACTION_AMOUNT');
    assert(t.cashKey && !keys.has(t.cashKey), 'DUPLICATE_CASH_MOVEMENT'); keys.add(t.cashKey);
    assert(t.sourceRef, 'TRANSACTION_SOURCE');
    // Dates must describe cash arrival/payment, never an order date or sales recognition date.
    const localDay = new Date(t.at + 8*3600000).toISOString().slice(0,10);
    const row = byDay.get(localDay); if (!row) continue;
    const field = t.kind === 'settlement' ? 'inflowCents' : 'outflowCents';
    row[field] = sum([row[field], t.amountCents]);
    row.sourceRefs.push(`${t.sourceRef}@v${t.version}`);
  }
  return rows;
}

/** Forecast 1–7 days. SES rounds each level update to a cent using integer arithmetic. */
export function predict(rows, {method='seasonal_naive', field='inflowCents', horizon=7, alphaBps=3000}={}) {
  validateDaily(rows);
  assert(METHODS.includes(method) && FIELDS.includes(field), 'FORECAST_METHOD_OR_FIELD');
  integer(horizon, 'HORIZON', 1); assert(horizon <= 7, 'HORIZON_MAX_7');
  integer(alphaBps, 'ALPHA', 1); assert(alphaBps <= 10000, 'ALPHA');
  const needed = method === 'weekday_median' ? 28 : 7;
  assert(rows.length >= needed, 'INSUFFICIENT_HISTORY');
  const last = rows.at(-1), byDay = new Map(rows.map(r=>[r.day,r]));
  let level = rows[0][field];
  if (method === 'ses') for (const row of rows.slice(1)) {
    level = roundedRatio(BigInt(alphaBps)*BigInt(row[field]) + BigInt(10000-alphaBps)*BigInt(level), 10000n);
  }
  return Array.from({length:horizon}, (_,i) => {
    const day = addDays(last.day, i+1);
    const references = method === 'ses' ? rows : Array.from({length:method==='weekday_median'?4:1},
      (_,j)=>byDay.get(addDays(day,-7*(j+1))));
    assert(references.every(Boolean), 'MISSING_WEEKDAY');
    const cents = method === 'ses' ? level : method === 'weekday_median'
      ? median(references.map(r=>r[field])) : references[0][field];
    return {day, cents, method, field, provenance:'forecast', confirmed:false,
      trainingEnd:last.day, sourceRefs:[...new Set(references.flatMap(r=>r.sourceRefs))]};
  });
}

export function metrics(actual, predicted) {
  assert(actual.length > 0 && actual.length === predicted.length, 'METRIC_LENGTH');
  const errors = actual.map((v,i)=>sum([integer(v,'ACTUAL',-Number.MAX_SAFE_INTEGER),
    -integer(predicted[i],'PREDICTED',-Number.MAX_SAFE_INTEGER)]));
  // MAE/RMSE may be fractional cents; they are diagnostics, not amounts inserted in the ledger.
  const abs = sum(errors.map(Math.abs));
  const scale = Math.max(...errors.map(Math.abs));
  const rmse = scale === 0 ? 0 : scale*Math.sqrt(errors.reduce((s,e)=>s+(e/scale)**2,0)/errors.length);
  return {count:errors.length, maeCents:abs/errors.length, rmseCents:rmse};
}

/** origin = number of visible historical days; all 7 future values are predicted at that origin. */
export function rollingBacktest(rows, {method='seasonal_naive', field='inflowCents', startOrigin=28,
  stopOrigin=rows.length-7, horizon=7, stride=1, alphaBps=3000}={}) {
  validateDaily(rows); integer(startOrigin,'START_ORIGIN',28); integer(stopOrigin,'STOP_ORIGIN',startOrigin);
  integer(stride,'STRIDE',1); integer(horizon,'HORIZON',1);
  assert(horizon<=7 && stopOrigin+horizon<=rows.length,'BACKTEST_RANGE');
  const folds=[];
  for(let origin=startOrigin;origin<=stopOrigin;origin+=stride) {
    const forecast=predict(rows.slice(0,origin),{method,field,horizon,alphaBps});
    const actual=rows.slice(origin,origin+horizon).map(r=>r[field]);
    folds.push({origin, trainingEnd:rows[origin-1].day, days:forecast.map(f=>f.day),
      predictions:forecast.map(f=>f.cents), actual,
      sourceRefs:[...new Set(rows.slice(0,origin+horizon).flatMap(r=>r.sourceRefs))]});
  }
  assert(folds.length,'NO_FOLDS');
  return {method,field,horizon,folds,metrics:metrics(folds.flatMap(f=>f.actual),folds.flatMap(f=>f.predictions)),
    byHorizon:Array.from({length:horizon},(_,h)=>({horizon:h+1,
      ...metrics(folds.map(f=>f.actual[h]),folds.map(f=>f.predictions[h]))})),
    disclosure:'滚动窗口有重叠，误差不是独立样本；不报告未来覆盖概率。'};
}

/** Choose on first 56 days only, freeze choices, then evaluate on untouched target dates 57–84. */
export function evaluateForecasts(rows, {selectionDays=56,horizon=7,alphaBps=3000}={}) {
  validateDaily(rows); integer(selectionDays,'SELECTION_DAYS',35);
  assert(rows.length>=selectionDays+horizon,'INSUFFICIENT_HOLDOUT');
  const results={},selected={},diagnostics={};
  for(const field of FIELDS) {
    const candidates=METHODS.map(method=>({method,
      validation:rollingBacktest(rows.slice(0,selectionDays),{method,field,horizon,alphaBps}),
      holdout:rollingBacktest(rows,{method,field,horizon,alphaBps,startOrigin:selectionDays})}));
    // Ties keep the simpler baseline; holdout results are never used to choose.
    const winner=candidates.reduce((a,b)=>b.validation.metrics.maeCents<a.validation.metrics.maeCents?b:a);
    selected[field]=winner.method; results[field]=candidates;
    const baseline=candidates.find(c=>c.method==='seasonal_naive');
    diagnostics[field]={needsReview:winner.holdout.metrics.maeCents>baseline.holdout.metrics.maeCents,
      selectedHoldoutMaeCents:winner.holdout.metrics.maeCents,
      baselineHoldoutMaeCents:baseline.holdout.metrics.maeCents,
      explanation:'留出段变差时提示复核，不用同一检验段偷偷重选并宣称提升；这不是显著性或漂移检验。'};
  }
  const income=predict(rows,{method:selected.inflowCents,field:'inflowCents',horizon,alphaBps});
  const expense=predict(rows,{method:selected.outflowCents,field:'outflowCents',horizon,alphaBps});
  return {selected,results,diagnostics,selectionDays,alphaBps,
    trainingEnd:rows.at(-1).day,historyDays:rows.length,
    daily:income.map((f,i)=>({day:f.day,inflowCents:f.cents,outflowCents:expense[i].cents,
      netCents:sum([f.cents,-expense[i].cents]),provenance:'forecast',confirmed:false,
      sourceRefs:[...new Set([...f.sourceRefs,...expense[i].sourceRefs])]})),
    disclosure:'仅为模拟历史上的方法对照；预测不是已确认现金事件，不提高今日可提用上限。'};
}
