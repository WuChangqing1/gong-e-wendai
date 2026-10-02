const M = Object.create(null);
M["core/common.mjs"] = (() => {
/** All monetary values are safe integer CNY cents. Times are UTC epoch ms; days use UTC+08. */
const DAY_MS = 86400000;
function assert(condition, code) { if (!condition) throw new Error(code); }
function integer(value, code = 'INTEGER_REQUIRED', minimum = 0) {
  assert(Number.isSafeInteger(value) && value >= minimum, code); return value;
}
function sum(values) {
  return values.reduce((a, b) => integer(a + integer(b, 'AMOUNT', -Number.MAX_SAFE_INTEGER),
    'SUM_OVERFLOW', -Number.MAX_SAFE_INTEGER), 0);
}
function roundedRatio(numerator, denominator) {
  assert(typeof numerator === 'bigint' && numerator >= 0n && denominator > 0n, 'RATIO');
  const n = (numerator + denominator / 2n) / denominator;
  assert(n <= BigInt(Number.MAX_SAFE_INTEGER), 'OVERFLOW'); return Number(n);
}
function dayIndex(day) {
  assert(typeof day === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(day), 'DAY_FORMAT');
  const at = Date.parse(day + 'T00:00:00Z');
  assert(Number.isFinite(at) && new Date(at).toISOString().slice(0, 10) === day, 'INVALID_DAY');
  return Math.floor(at / DAY_MS);
}
function addDays(day, count) {
  integer(count, 'DAY_OFFSET', -100000);
  return new Date((dayIndex(day) + count) * DAY_MS).toISOString().slice(0, 10);
}
function beijingDay(at) {
  integer(at, 'TIMESTAMP'); return new Date(at + 8 * 3600000).toISOString().slice(0, 10);
}
function atBeijing(day, clock = '00:00') {
  dayIndex(day); assert(/^([01]\d|2[0-3]):[0-5]\d$/.test(clock), 'CLOCK');
  return Date.parse(`${day}T${clock}:00+08:00`);
}
function median(values) {
  assert(values.length > 0, 'EMPTY_MEDIAN');
  const v = [...values].map(x => integer(x, 'AMOUNT')).sort((a, b) => a - b);
  const m = Math.floor(v.length / 2);
  return v.length % 2 ? v[m] : roundedRatio(BigInt(v[m-1]) + BigInt(v[m]), 2n);
}
/** Nearest-rank empirical quantile. q is a design parameter, NOT a future coverage guarantee. */
function quantile(values, q) {
  assert(Number.isFinite(q) && q > 0 && q <= 1 && values.length > 0, 'QUANTILE');
  const v = [...values].map(x => integer(x, 'QUANTILE_VALUE')).sort((a,b) => a-b);
  return v[Math.ceil(q * v.length) - 1];
}
function yuan(cents) { return (cents / 100).toFixed(2); }

return {DAY_MS,assert,integer,sum,roundedRatio,dayIndex,addDays,beijingDay,atBeijing,median,quantile,yuan};
})();
M["core/cash-engine.mjs"] = (() => {
/** 工e稳袋参考计算内核。仅处理已规范化的模拟现金事件；无银行接口、预测或授信。 */
function yuanToCents(text) {
  if (typeof text !== 'string' || !/^-?\d+(\.\d{1,2})?$/.test(text)) throw new Error('AMOUNT_FORMAT');
  const negative = text.startsWith('-');
  const [whole, fraction = ''] = text.replace(/^-/, '').split('.');
  const cents = BigInt(whole) * 100n + BigInt(fraction.padEnd(2, '0'));
  if (cents > BigInt(Number.MAX_SAFE_INTEGER)) throw new Error('AMOUNT_OVERFLOW');
  return Number(negative ? -cents : cents);
}

function safeInt(value, name, minimum = -Number.MAX_SAFE_INTEGER) {
  if (!Number.isSafeInteger(value) || value < minimum) throw new Error(name);
  return value;
}
function add(a, b) { return safeInt(a + b, 'SUM_OVERFLOW'); }

/**
 * Input times: integer UTC epoch milliseconds. UI must normalize dates in Asia/Shanghai.
 * An event is a single cash movement, not an order or an accounting revenue record.
 * cashKey is the stable cash-movement identity from the normalization layer.
 * state: scheduled | included_in_opening | cancelled.
 * Unknown amount/time/state, uncertain balance scope or unconfirmed obligations => no upper bound.
 */
function evaluate(input) {
  try {
    const {asOf, end, openingCents, reserveCents, events, scenarios} = input;
    safeInt(asOf, 'ASOF'); safeInt(end, 'END');
    if (end <= asOf) throw new Error('HORIZON');
    safeInt(openingCents, 'OPENING'); safeInt(reserveCents, 'RESERVE', 0);
    if (input.currency !== 'CNY') throw new Error('CURRENCY');
    if (input.balanceConfirmed !== true || input.obligationsConfirmed !== true) throw new Error('CONFIRMATION_REQUIRED');
    if (!Array.isArray(events) || !Array.isArray(scenarios) || !scenarios.length) throw new Error('INPUT_ARRAYS');
    const ids = new Set(), keys = new Set(), byId = new Map();
    for (const event of events) {
      if (!event.id || ids.has(event.id)) throw new Error('DUPLICATE_ID');
      if (!event.cashKey || keys.has(event.cashKey)) throw new Error('DUPLICATE_CASH_MOVEMENT');
      ids.add(event.id); keys.add(event.cashKey); byId.set(event.id, event);
      if (!['scheduled', 'included_in_opening', 'cancelled'].includes(event.state)) throw new Error('UNKNOWN_STATE');
      if (event.state === 'cancelled') continue;
      safeInt(event.deltaCents, 'EVENT_AMOUNT'); safeInt(event.at, 'EVENT_TIME');
      if (event.currency !== 'CNY') throw new Error('EVENT_CURRENCY');
      if (event.confirmed !== true || !event.sourceRefs?.length) throw new Error('EVENT_UNCONFIRMED');
      if (event.state === 'scheduled' && event.at <= asOf) throw new Error('OPENING_CUTOFF_CONFLICT');
      if (event.state === 'included_in_opening' && event.at > asOf) throw new Error('FUTURE_IN_OPENING');
    }
    const reserveChanges = input.reserveChanges ?? [];
    const reserveTimes = new Set();
    for (const r of reserveChanges) {
      safeInt(r.at, 'RESERVE_TIME'); safeInt(r.cents, 'RESERVE_CHANGE', 0);
      if (r.at <= asOf || r.at > end || reserveTimes.has(r.at)) throw new Error('RESERVE_SCHEDULE');
      reserveTimes.add(r.at);
    }
    const scenarioIds = new Set();
    const results = scenarios.map(scenario => {
      if (!scenario.id || scenarioIds.has(scenario.id)) throw new Error('SCENARIO_ID');
      scenarioIds.add(scenario.id);
      const overrides = scenario.timeOverrides ?? {};
      for (const [id, at] of Object.entries(overrides)) {
        if (!byId.has(id) || byId.get(id).state !== 'scheduled') throw new Error('OVERRIDE_TARGET');
        safeInt(at, 'OVERRIDE_TIME'); if (at <= asOf) throw new Error('OVERRIDE_BEFORE_ASOF');
      }
      const rows = events.filter(e => e.state === 'scheduled').map(e => ({
        ...e, at: overrides[e.id] ?? e.at, type: 'cash', order: e.deltaCents < 0 ? 1 : 2
      })).filter(e => e.at <= end);
      rows.push(...reserveChanges.map(r => ({...r, type: 'reserve', order: 0, id: 'reserve:' + r.at})));
      // Same timestamp, no verified intra-timestamp sequence: debit before credit (conservative assumption).
      rows.sort((a,b) => a.at - b.at || a.order - b.order || a.id.localeCompare(b.id));
      let balance = openingCents, reserve = reserveCents;
      const causalEventIds = [], points = [];
      function point(at, eventId, sourceRefs = []) {
        const headroomCents = add(balance, -reserve);
        points.push({at, eventId, sourceRefs, balanceCents:balance, reserveCents:reserve,
          headroomCents, paymentGapCents:Math.max(0, -balance),
          bufferGapCents:Math.max(0, -headroomCents), appliedCount:causalEventIds.length});
      }
      point(asOf, 'opening');
      for (const row of rows) {
        if (row.type === 'reserve') reserve = row.cents;
        else { balance = add(balance, row.deltaCents); causalEventIds.push(row.id); }
        point(row.at, row.id, row.sourceRefs ?? []);
      }
      point(end, 'horizon_end');
      const limitingPoint = points.reduce((a,b) => b.headroomCents < a.headroomCents ? b : a);
      limitingPoint.causalEventIds = causalEventIds.slice(0, limitingPoint.appliedCount);
      const appliedAtLimit = new Set(limitingPoint.causalEventIds);
      // A late receipt is absent from cash already received at the limiting point.
      // Preserve it separately so the explanation can show why expected money was not yet usable.
      const pendingAtLimit = events.filter(e => e.state === 'scheduled' && e.deltaCents > 0 &&
        !appliedAtLimit.has(e.id)).map(e => ({
          eventId:e.id, expectedAt:overrides[e.id] ?? e.at, amountCents:e.deltaCents, sourceRefs:e.sourceRefs
        }));
      return {id:scenario.id, points, limitingPoint, minHeadroomCents:limitingPoint.headroomCents,
        pendingAtLimit, orderedCashEventIds:causalEventIds,
        endingBeforeWithdrawalCents:balance,
        paymentGapCents:Math.max(...points.map(p=>p.paymentGapCents)),
        bufferGapCents:Math.max(...points.map(p=>p.bufferGapCents)),
        gaps:points.filter(p=>p.bufferGapCents > 0)};
    });
    const limitingScenario = results.reduce((a,b) => b.minHeadroomCents < a.minHeadroomCents ? b : a);
    const paymentGapCents = Math.max(...results.map(s=>s.paymentGapCents));
    const bufferGapCents = Math.max(...results.map(s=>s.bufferGapCents));
    const feasible = limitingScenario.minHeadroomCents >= 0;
    return {status:feasible ? 'FEASIBLE' : paymentGapCents > 0 ? 'PAYMENT_GAP' : 'BELOW_BUFFER',
      feasible, maxWithdrawalCents:feasible ? limitingScenario.minHeadroomCents : 0,
      paymentGapCents, bufferGapCents, limitingScenarioId:limitingScenario.id,
      limitingPoint:limitingScenario.limitingPoint, scenarios:results,
      basis:'Only the supplied confirmed inputs and selected scenarios; no forecast guarantee.'};
  } catch (error) {
    return {status:'INPUT_INCOMPLETE', feasible:null, maxWithdrawalCents:null, errors:[error.message]};
  }
}

/** Minimal consultation DTO: intentionally excludes opening balance, withdrawal, reserve and full curves. */
function makeConsultationPacket(facts, selectedIds, question, revision) {
  if (typeof question !== 'string' || !question.trim()) throw new Error('QUESTION_REQUIRED');
  const selected = new Set(selectedIds);
  return {schemaVersion:1, revision, question, facts:facts.filter(f=>selected.has(f.id) && f.scope === 'business').map(f=>({
    id:f.id, kind:f.kind, amountCents:f.amountCents, expectedAt:f.expectedAt, status:f.status,
    sourceLabel:f.sourceLabel
  }))};
  // Free text can itself contain private data: preview/redaction is still required before a real submission.
}

function demoInput() {
  const t = Date.parse('2026-10-01T20:00:00+08:00');
  const at = (day, clock='12:00') => Date.parse(`2026-10-0${1+day}T${clock}:00+08:00`);
  const event = (id, day, deltaCents, clock) => ({id, cashKey:id, at:at(day,clock), deltaCents,
    state:'scheduled', currency:'CNY', confirmed:true, sourceRefs:['simulated:'+id]});
  return {asOf:t, end:t+7*86400000, openingCents:360000, reserveCents:60000,
    currency:'CNY', balanceConfirmed:true, obligationsConfirmed:true,
    events:[event('purchase',1,-140000,'08:00'),event('settlement',2,200000,'09:00'),
      event('rent',2,-180000,'18:00'),event('refund',3,-60000,'10:00')],
    scenarios:[{id:'on_time'},{id:'delay_to_D4',timeOverrides:{settlement:at(4,'09:00')}}]};
}

return {yuanToCents,evaluate,makeConsultationPacket,demoInput};
})();
M["core/forecast.mjs"] = (() => {
const { assert, integer, sum, roundedRatio, dayIndex, addDays, atBeijing, median } = M["core/common.mjs"];
const METHODS = ['seasonal_naive', 'weekday_median', 'ses'];
const METHOD_LABELS = { seasonal_naive:'上周同一天', weekday_median:'近四周同星期中位数', ses:'简单指数平滑' };
const FIELDS = ['inflowCents', 'outflowCents'];

/** One merchant, complete calendar days; a missing day is never silently interpreted as zero. */
function validateDaily(rows) {
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
function aggregateDaily(transactions, calendar, merchantId, asOf) {
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
function predict(rows, {method='seasonal_naive', field='inflowCents', horizon=7, alphaBps=3000}={}) {
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

function metrics(actual, predicted) {
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
function rollingBacktest(rows, {method='seasonal_naive', field='inflowCents', startOrigin=28,
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
function evaluateForecasts(rows, {selectionDays=56,horizon=7,alphaBps=3000}={}) {
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

return {METHODS,METHOD_LABELS,FIELDS,validateDaily,aggregateDaily,predict,metrics,rollingBacktest,evaluateForecasts};
})();
M["core/settlement.mjs"] = (() => {
const { assert, integer, quantile, DAY_MS } = M["core/common.mjs"];
/** A completed-pair descriptive statistic, not a survival model; open items are explicitly counted. */
function settlementStats(pairs, {merchantId,channel,asOf,q=0.9,minSamples=12}) {
  integer(asOf,'ASOF'); integer(minSamples,'MIN_SAMPLES',1);
  assert(merchantId && channel,'SETTLEMENT_SCOPE');
  assert(Number.isFinite(q)&&q>0&&q<=1,'QUANTILE');
  const completed=[],open=[]; const ids=new Set();
  for(const p of pairs.filter(p=>p.merchantId===merchantId && p.channel===channel)) {
    assert(p.id&&!ids.has(p.id),'DUPLICATE_SETTLEMENT');ids.add(p.id);
    integer(p.scheduledAt,'SCHEDULED_AT');integer(p.knownAt,'KNOWN_AT');
    assert(p.sourceRef,'SETTLEMENT_SOURCE');
    if(p.knownAt>asOf || p.scheduledAt>asOf) continue;
    if(p.status==='cancelled') continue;
    assert(['open','completed'].includes(p.status),'SETTLEMENT_STATUS');
    if(p.status==='completed') integer(p.actualAt,'ACTUAL_AT');
    if(p.status==='completed' && p.actualAt<=asOf) {
      completed.push({...p,delayMs:Math.max(0,p.actualAt-p.scheduledAt)});
    } else open.push(p);
  }
  const enough=completed.length>=minSamples;
  const delayMs=enough?quantile(completed.map(p=>p.delayMs),q):null;
  return {merchantId,channel,completedCount:completed.length,openCount:open.length,
    overdueOpenCount:open.filter(p=>p.scheduledAt<asOf).length,
    status:enough?'DESCRIPTIVE_SAMPLE':'INSUFFICIENT_SAMPLE',q,minSamples,
    empiricalDelayDays:delayMs===null?null:Math.ceil(delayMs/DAY_MS),
    sourceRefs:completed.map(p=>p.sourceRef),openSourceRefs:open.map(p=>p.sourceRef),
    disclosure:'仅描述已完成结算样本，未完成项存在删失；不能解释为未来到账概率。自然日压力，不推断银行T+1或节假日规则。'};
}

/** selectedIds must be a single channel/cohort. Defaults remain visible policy assumptions. */
function buildDelayScenarios(events, {selectedIds,manualDelayDays=2,stats=null}) {
  integer(manualDelayDays,'DELAY_DAYS');assert(manualDelayDays<=30,'DELAY_LIMIT');
  assert(Array.isArray(selectedIds)&&selectedIds.length>0,'SELECT_RECEIPTS');
  assert(new Set(selectedIds).size===selectedIds.length,'DUPLICATE_SELECTION');
  const selected=selectedIds.map(id=>{
    const e=events.find(e=>e.id===id);
    assert(e&&e.kind==='settlement'&&e.direction==='in'&&['planned','pending'].includes(e.status)
      &&e.confirmed===true&&e.provenance!=='forecast','DELAY_TARGET');
    integer(e.at,'EVENT_TIME');
    if(stats) assert(e.merchantId===stats.merchantId&&e.channel===stats.channel,'CHANNEL_MISMATCH');
    return e;
  });
  assert(selected.every(e=>e.merchantId===selected[0].merchantId&&e.channel===selected[0].channel),'MIXED_COHORT');
  const make=(id,days,basis)=>({id,label:days===0?'按当前计划':`指定结算延迟${days}个自然日`,
    timeOverrides:Object.fromEntries(selected.map(e=>[e.id,integer(e.at+days*DAY_MS,'DELAY_OVERFLOW')])),
    delayDays:days,basis,sourceRefs:stats?.sourceRefs??[],probability:null});
  const scenarios=[make('on_time',0,'当前已确认预计时间'),make('manual_delay',manualDelayDays,'用户设定压力，不是概率预测')];
  if(stats?.empiricalDelayDays!==null && stats?.empiricalDelayDays!==undefined) {
    scenarios.push(make('sample_delay',stats.empiricalDelayDays,
      `已完成样本经验分位数q=${stats.q}，n=${stats.completedCount}；存在${stats.openCount}笔未完成项`));
  }
  return scenarios;
}

return {settlementStats,buildDelayScenarios};
})();
M["core/reserve.mjs"] = (() => {
const { assert, integer, sum, quantile } = M["core/common.mjs"];
/** Extra cash needed to cover the largest adverse cumulative error within each 7-day forecast block. */
function bufferBlocks(incomeFolds,expenseFolds) {
  assert(incomeFolds.length===expenseFolds.length&&incomeFolds.length>0,'FOLD_PAIRING');
  return incomeFolds.map((a,i)=>{
    const b=expenseFolds[i];
    assert(a.origin===b.origin&&JSON.stringify(a.days)===JSON.stringify(b.days),'FOLD_ALIGNMENT');
    assert(a.actual.length===a.predictions.length&&a.actual.length===b.actual.length
      &&b.actual.length===b.predictions.length&&a.actual.length===a.days.length,'FOLD_LENGTH');
    let cumulative=0,maximum=0;
    const prefixErrors=a.actual.map((v,h)=>{
      // Optimistic forecast error: predicted receipts too high OR predicted payments too low.
      const e=sum([integer(a.predictions[h]),-integer(v),integer(b.actual[h]),-integer(b.predictions[h])]);
      cumulative=sum([cumulative,e]);maximum=Math.max(maximum,cumulative);return cumulative;
    });
    return {origin:a.origin,trainingEnd:a.trainingEnd,days:a.days,
      stressCents:maximum,prefixErrors,sourceRefs:[...new Set([...(a.sourceRefs??[]),...(b.sourceRefs??[])])]};
  });
}

/** Transparent heuristic, NOT Miller–Orr optimal control limits, confidence intervals, or VaR. */
function recommendReserve(blocks,{currentReserveCents,q=0.9,roundingCents=10000,minBlocks=8}) {
  integer(currentReserveCents,'RESERVE');integer(roundingCents,'ROUNDING',1);integer(minBlocks,'MIN_BLOCKS',1);
  assert(Number.isFinite(q)&&q>0&&q<=1,'QUANTILE');
  blocks.forEach(b=>integer(b.stressCents,'STRESS_AMOUNT'));
  const enough=blocks.length>=minBlocks;
  const raw=enough?quantile(blocks.map(b=>b.stressCents),q):null;
  const rounded=raw===null?null:integer(Math.ceil(raw/roundingCents)*roundingCents,'RESERVE_OVERFLOW');
  const suggestion=rounded===null?currentReserveCents:Math.max(currentReserveCents,rounded);
  return {status:enough?'SUGGESTION':'INSUFFICIENT_SAMPLE',currentReserveCents,suggestedReserveCents:suggestion,
    extraCents:suggestion-currentReserveCents,empiricalErrorCents:raw,blockCount:blocks.length,q,roundingCents,
    minBlocks,requiresConfirmation:true,sourceRefs:[...new Set(blocks.flatMap(b=>b.sourceRefs??[]))],
    disclosure:'滚动误差窗口相互重叠；样本经验分位数不是未来覆盖保证。建议不自动生效，不自动降低既有留底，也不额外叠加第二份同类误差缓冲。'};
}

function applyReserveAdvice(engineInput,advice,confirmed=false) {
  assert(confirmed===true,'USER_CONFIRMATION_REQUIRED');
  assert(advice.currentReserveCents===engineInput.reserveCents,'STALE_RESERVE_ADVICE');
  integer(advice.suggestedReserveCents,'SUGGESTED_RESERVE');
  assert(advice.suggestedReserveCents>=engineInput.reserveCents,'NO_AUTO_LOWERING');
  return {...engineInput,reserveCents:advice.suggestedReserveCents,
    reserveChanges:(engineInput.reserveChanges??[]).map(r=>({...r,cents:Math.max(r.cents,advice.suggestedReserveCents)}))};
}

return {bufferBlocks,recommendReserve,applyReserveAdvice};
})();
M["core/adapter.mjs"] = (() => {
const { assert, integer } = M["core/common.mjs"];
const { evaluate } = M["core/cash-engine.mjs"];
const { evaluateForecasts } = M["core/forecast.mjs"];
const { settlementStats, buildDelayScenarios } = M["core/settlement.mjs"];
const { bufferBlocks, recommendReserve, applyReserveAdvice } = M["core/reserve.mjs"];
const KINDS=['settlement','purchase','rent','refund','tax','transfer','ownerDraw'];
/** Explicit bridge to the supplied reference engine. Never silently turn a forecast into confirmed cash. */
function toEngineEvent(event,merchantId) {
  assert(event.provenance!=='forecast','FORECAST_NOT_CONFIRMED_CASH');
  assert(event.merchantId===merchantId,'CROSS_MERCHANT_EVENT');
  assert(KINDS.includes(event.kind),'EVENT_KIND');
  assert(['planned','pending','posted','voided'].includes(event.status),'EVENT_STATUS');
  assert(event.id&&event.cashKey,'EVENT_ID'); integer(event.version,'EVENT_VERSION',1);
  if(event.status==='voided') return {...event,state:'cancelled'};
  integer(event.amountCents,'AMOUNT');integer(event.at,'EVENT_AT');
  assert(['in','out'].includes(event.direction),'DIRECTION');
  assert(event.currency==='CNY'&&event.scope==='business','CURRENCY_OR_SCOPE');
  assert(event.confirmed===true&&event.sourceRefs?.length>0,'EVENT_CONFIRMATION');
  if(event.status==='posted') assert(event.includedInOpening===true,'POSTED_OPENING_RECONCILIATION');
  if(event.kind==='transfer') assert(['inside','outside'].includes(event.transferScope),'TRANSFER_SCOPE');
  const delta=event.kind==='transfer'&&event.transferScope==='inside'?0:
    event.amountCents*(event.direction==='in'?1:-1);
  return {...event,deltaCents:delta,state:event.status==='posted'?'included_in_opening':'scheduled'};
}

function toEngineInput(bundle,scenarios) {
  assert(bundle.balanceConfirmed===true&&bundle.obligationsConfirmed===true,'CONFIRMATION_REQUIRED');
  integer(bundle.revision,'LEDGER_REVISION',1);integer(bundle.historyRevision,'HISTORY_REVISION',1);
  return {asOf:bundle.asOf,end:bundle.end,openingCents:bundle.openingCents,reserveCents:bundle.reserveCents,
    reserveChanges:bundle.reserveChanges??[],currency:'CNY',balanceConfirmed:true,obligationsConfirmed:true,
    events:bundle.events.map(e=>toEngineEvent(e,bundle.merchantId)),scenarios};
}

/** Exact internal signature, not a cryptographic identifier; do not expose it in shared DTOs. */
function makeBasisKey(bundle,options={}) { return JSON.stringify({bundle,options}); }
function isResultCurrent(result,bundle,options={}) { return result.basisKey===makeBasisKey(bundle,options); }

function computeEnhancements(bundle,options={}) {
  assert(bundle.history.every(r=>r.merchantId===bundle.merchantId),'CROSS_MERCHANT_HISTORY');
  const chosen=bundle.events.find(e=>e.id===bundle.delayTargetIds[0]);assert(chosen,'DELAY_TARGET');
  const stats=settlementStats(bundle.settlements,{merchantId:bundle.merchantId,channel:chosen.channel,
    asOf:bundle.asOf,q:options.delayQuantile??0.9,minSamples:12});
  const scenarios=buildDelayScenarios(bundle.events,{selectedIds:bundle.delayTargetIds,
    manualDelayDays:options.delayDays??2,stats});
  const input=toEngineInput(bundle,scenarios);
  const baseline=evaluate({...input,scenarios:[scenarios[0]]});
  const stress=evaluate(input);
  let forecast=null,reserveAdvice=null,blocks=[],forecastUnavailable=null;
  try {
    // Closed historical dates must end the day before the prediction period; no forward leakage.
    const cutoff=new Date(bundle.asOf+8*3600000).toISOString().slice(0,10);
    assert(bundle.history.at(-1).day < cutoff,'HISTORY_NOT_CLOSED');
    const next=new Date(Date.parse(bundle.history.at(-1).day+'T00:00:00Z')+86400000).toISOString().slice(0,10);
    assert(next===cutoff,'STALE_HISTORY');
    forecast=evaluateForecasts(bundle.history);
    const inc=forecast.results.inflowCents.find(r=>r.method===forecast.selected.inflowCents).holdout.folds;
    const out=forecast.results.outflowCents.find(r=>r.method===forecast.selected.outflowCents).holdout.folds;
    blocks=bufferBlocks(inc,out);
    reserveAdvice=recommendReserve(blocks,{currentReserveCents:bundle.reserveCents,q:options.reserveQuantile??0.9,
      roundingCents:10000,minBlocks:8});
  } catch(error) { forecast=null;reserveAdvice=null;blocks=[];forecastUnavailable=error.message; }
  const applied=options.reserveConfirmed===true&&reserveAdvice
    ?applyReserveAdvice(input,reserveAdvice,true):input;
  return {basisKey:makeBasisKey(bundle,options),ledgerRevision:bundle.revision,historyRevision:bundle.historyRevision,
    forecast,forecastUnavailable,blocks,reserveAdvice,settlement:stats,scenarios,baseline,stress,
    withReserve:evaluate(applied),reserveApplied:options.reserveConfirmed===true&&reserveAdvice!==null,
    forecastAffectsWithdrawable:false,
    disclosure:'所有商户、交易、延期和误差数据均为模拟；不代表真实准确率。金额结果仅在输入和选定情景成立时有效。'};
}

return {KINDS,toEngineEvent,toEngineInput,makeBasisKey,isResultCurrent,computeEnhancements};
})();
M["data/fixtures.mjs"] = (() => {
const { addDays, atBeijing, DAY_MS } = M["core/common.mjs"];
const { aggregateDaily } = M["core/forecast.mjs"];
/** Fixed seed, never tuned to make an enhanced method win. Algorithmic synthetic data, not real merchants. */
function rng(seed) { let x=seed>>>0;return ()=>{x=(Math.imul(1664525,x)+1013904223)>>>0;return x/4294967296;}; }
function createMerchants() {
  const asOf=atBeijing('2026-10-02'),end=asOf+7*DAY_MS-1;
  return [
    {id:'SIM-NOODLES',name:'模拟·面馆',seed:137,mode:'weekly'},
    {id:'SIM-GROCERY',name:'模拟·便利店',seed:811,mode:'trend'},
    {id:'SIM-FRUIT',name:'模拟·水果店',seed:2026,mode:'break'}
  ].map((spec,mi)=>{
    const random=rng(spec.seed),calendar=[],transactions=[];
    for(let i=0;i<84;i++) {
      const day=addDays('2026-07-10',i),weekday=new Date(day+'T00:00:00Z').getUTCDay();
      let inflow=0,outflow=0;
      if(spec.mode==='weekly') {
        inflow=[1900,1050,1150,1250,1300,1500,2200][weekday]+Math.round((random()-.5)*300);
        outflow=[1050,600,650,700,800,900,1300][weekday]+Math.round((random()-.5)*220);
        if(i%19===4) outflow+=650;
      } else if(spec.mode==='trend') {
        inflow=900+i*9+Math.round((random()-.5)*180);
        outflow=550+i*5+Math.round((random()-.5)*160);
      } else {
        inflow=1400+(weekday===6?500:0)+Math.round((random()-.5)*650);
        outflow=800+Math.round((random()-.5)*300);
        if(i>=63){inflow=Math.round(inflow*.55);outflow+=350;}
        if(i%13===0){inflow=0;outflow=0;}
      }
      inflow=Math.max(0,inflow)*100;outflow=Math.max(0,outflow)*100;
      calendar.push({day,complete:true,sourceRef:`sim:${spec.id}:day:${day}`});
      for(const [kind,cents,clock] of [['settlement',inflow,'20:00'],['purchase',outflow,'08:00']]) {
        if(cents===0) continue;
        const id=`${spec.id}-${day}-${kind}`;
        transactions.push({id,cashKey:id,merchantId:spec.id,kind,amountCents:cents,at:atBeijing(day,clock),
          availableAt:atBeijing(addDays(day,1)),status:'posted',scope:'business',currency:'CNY',
          version:1,sourceRef:`sim:${id}`});
      }
    }
    const make=(id,day,clock,amountCents,kind,direction)=>({id:`${spec.id}-${id}`,cashKey:`${spec.id}-${id}`,
      merchantId:spec.id,title:{purchase:'明早进货',settlement:'平台结算款',rent:'门店租金',refund:'已确认退款'}[kind],
      at:atBeijing(addDays('2026-10-02',day),clock),amountCents,kind,direction,
      status:kind==='settlement'?'pending':'planned',scope:'business',currency:'CNY',
      confirmed:true,version:1,provenance:'confirmed',sourceRefs:[`sim:future:${spec.id}:${id}`],channel:'SIM-CHANNEL-A'});
    const events=[make('purchase',0,'08:00',140000,'purchase','out'),
      make('settlement',1,'09:00',200000,'settlement','in'),
      make('rent',1,'18:00',180000,'rent','out'),make('refund',2,'10:00',60000,'refund','out')];
    const delays=[0,0,0,0,0,0,0,0,0,0,1,1,1,1,1,1,2,2,2,3];
    const settlements=delays.map((d,i)=>({id:`pair-${spec.id}-${i}`,merchantId:spec.id,channel:'SIM-CHANNEL-A',
      status:'completed',scheduledAt:atBeijing(addDays('2026-08-01',i*2),'09:00'),
      knownAt:atBeijing(addDays('2026-08-01',i*2-1),'18:00'),
      actualAt:atBeijing(addDays('2026-08-01',i*2+d),'09:00'),sourceRef:`sim:pair:${spec.id}:${i}`}));
    settlements.push({id:`pair-${spec.id}-open`,merchantId:spec.id,channel:'SIM-CHANNEL-A',status:'open',
      scheduledAt:atBeijing('2026-09-30','09:00'),knownAt:atBeijing('2026-09-29','18:00'),sourceRef:`sim:pair:${spec.id}:open`});
    return {merchantId:spec.id,name:spec.name,synthetic:true,seed:spec.seed,revision:1,historyRevision:1,
      asOf,end,openingCents:360000,reserveCents:60000,balanceConfirmed:true,obligationsConfirmed:true,
      events,delayTargetIds:[events[1].id],settlements,transactions,calendar,
      history:aggregateDaily(transactions,calendar,spec.id,asOf)};
  });
}

return {createMerchants};
})();
globalThis.WendaiModules = M;
