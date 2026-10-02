import test from 'node:test';
import assert from 'node:assert/strict';
import { addDays, atBeijing, dayIndex, median, quantile, DAY_MS } from '../core/common.mjs';
import { aggregateDaily, predict, metrics, rollingBacktest, evaluateForecasts, validateDaily } from '../core/forecast.mjs';
import { settlementStats, buildDelayScenarios } from '../core/settlement.mjs';
import { bufferBlocks, recommendReserve, applyReserveAdvice } from '../core/reserve.mjs';
import { toEngineEvent, toEngineInput, computeEnhancements, isResultCurrent } from '../core/adapter.mjs';
import { evaluate } from '../core/cash-engine.mjs';
import { createMerchants } from '../data/fixtures.mjs';

const clone=x=>structuredClone(x);
const makeRows=(n=84,value=i=>(i%7+1)*10000)=>Array.from({length:n},(_,i)=>({
  merchantId:'M',day:addDays('2026-01-01',i),complete:true,inflowCents:value(i),outflowCents:5000,
  sourceRefs:[`manual:${i}`]}));
const fixtures=createMerchants();
const sample=()=>clone(fixtures[0]);
const pair=(id,delay,status='completed')=>({id,merchantId:'M',channel:'C',status,
  knownAt:atBeijing('2026-09-01'),scheduledAt:atBeijing('2026-09-02'),
  actualAt:atBeijing(addDays('2026-09-02',delay)),sourceRef:'manual:'+id});
const pairOptions={merchantId:'M',channel:'C',asOf:atBeijing('2026-10-02'),q:.9,minSamples:5};
const blocks=[10000,20000,30000,50000,90000].map((stressCents,i)=>({stressCents,sourceRefs:['block:'+i]}));

test('calendar: Beijing timezone and leap day are deterministic',()=>{
  assert.equal(addDays('2024-02-28',1),'2024-02-29');
  assert.equal(atBeijing('2026-10-02'),Date.parse('2026-10-01T16:00:00Z'));
});
test('invalid calendar date is refused',()=>assert.throws(()=>dayIndex('2026-02-30'),/INVALID_DAY/));
test('even median uses half-up cents without binary rounding loss',()=>assert.equal(median([100,101]),101));
test('nearest-rank quantile has a hand-checkable answer',()=>assert.equal(quantile([0,0,1,2,4],.8),2));
test('quantile rejects missing sample and invalid q',()=>{
  assert.throws(()=>quantile([], .9));assert.throws(()=>quantile([1],0));
});
test('same-weekday baseline forecasts all seven days without recursion leakage',()=>{
  assert.deepEqual(predict(makeRows(28)).map(r=>r.cents),[10000,20000,30000,40000,50000,60000,70000]);
});
test('four-week median resists one historical spike: 100,200,300,1000 -> 250 yuan',()=>{
  const rows=makeRows(28,i=>[10000,20000,30000,100000][Math.floor(i/7)]);
  assert.equal(predict(rows,{method:'weekday_median'})[0].cents,25000);
  assert.equal(predict(rows,{method:'seasonal_naive'})[0].cents,100000);
});
test('SES alpha .5 manual recurrence produces 19844 cents',()=>{
  assert.equal(predict(makeRows(7,i=>i?20000:10000),{method:'ses',alphaBps:5000})[0].cents,19844);
});
test('SES alpha 1 collapses to last observation',()=>{
  assert.equal(predict(makeRows(28),{method:'ses',alphaBps:10000})[0].cents,70000);
});
test('forecast outputs are integer cents and explicitly unconfirmed',()=>{
  const rows=predict(makeRows(28),{method:'ses'});
  assert.ok(rows.every(r=>Number.isSafeInteger(r.cents)&&r.confirmed===false&&r.provenance==='forecast'));
});
test('all-zero observed history remains zero; no percentage metric',()=>{
  const r=evaluateForecasts(makeRows(84,()=>0));
  assert.equal(r.results.inflowCents[0].holdout.metrics.maeCents,0);
  assert.ok(!('mape' in r.results.inflowCents[0].holdout.metrics));
});
test('missing day is rejected rather than imputed as zero',()=>{
  const rows=makeRows(28);rows.splice(6,1);assert.throws(()=>predict(rows),/MISSING_OR_DUPLICATE_DAY/);
});
test('incomplete day is rejected',()=>{
  const rows=makeRows(28);rows[3].complete=false;assert.throws(()=>predict(rows),/INCOMPLETE_DAY/);
});
test('mixed merchant history is rejected',()=>{
  const rows=makeRows(28);rows[0].merchantId='OTHER';assert.throws(()=>predict(rows),/MIXED_MERCHANTS/);
});
test('fractional cents and negative gross receipts are rejected',()=>{
  for(const value of [1.2,-1]) assert.throws(()=>predict(makeRows(28,()=>value)),/HISTORY_AMOUNT/);
});
test('insufficient weekday history is refused',()=>assert.throws(()=>predict(makeRows(27),{method:'weekday_median'}),/INSUFFICIENT_HISTORY/));
test('invalid forecast horizon and alpha are refused',()=>{
  assert.throws(()=>predict(makeRows(28),{horizon:8}));assert.throws(()=>predict(makeRows(28),{alphaBps:0}));
});
test('MAE and RMSE handle negative or zero net cash without MAPE division',()=>{
  const r=metrics([100,0,-100],[0,0,0]);
  assert.equal(r.maeCents,200/3);assert.ok(Math.abs(r.rmseCents-Math.sqrt(20000/3))<1e-9);
});
test('each rolling origin sees only prior observations',()=>{
  const bt=rollingBacktest(makeRows(42));
  assert.ok(bt.folds.every(f=>f.trainingEnd<f.days[0]));assert.equal(bt.folds[0].origin,28);
  assert.equal(bt.metrics.count,8*7);
});
test('late target changes cannot change previously issued forecasts',()=>{
  const a=makeRows(42),b=clone(a);b[35].inflowCents=999999;
  assert.deepEqual(rollingBacktest(a).folds[0].predictions,rollingBacktest(b).folds[0].predictions);
});
test('holdout data cannot influence model selection',()=>{
  const a=makeRows(84),b=clone(a);b.slice(56).forEach(r=>r.inflowCents=999999);
  assert.deepEqual(evaluateForecasts(a).selected,evaluateForecasts(b).selected);
});
test('test target dates never overlap model-selection target dates',()=>{
  const r=evaluateForecasts(makeRows(84));
  for(const c of r.results.inflowCents) {
    assert.equal(c.holdout.folds.length,22);
    assert.ok(c.validation.folds.at(-1).days.at(-1)<c.holdout.folds[0].days[0]);
  }
});
test('forecast functions do not mutate historical input',()=>{
  const rows=makeRows(),before=JSON.stringify(rows);evaluateForecasts(rows);assert.equal(JSON.stringify(rows),before);
});
test('aggregation preserves explicitly observed zero cash days',()=>{
  const r=aggregateDaily([],[{day:'2026-10-01',complete:true,sourceRef:'zero-close'}],'M',atBeijing('2026-10-02'));
  assert.equal(r[0].inflowCents,0);assert.equal(r[0].outflowCents,0);
});
test('aggregation rejects unclosed days',()=>assert.throws(()=>aggregateDaily([],
  [{day:'2026-10-02',complete:true,sourceRef:'x'}],'M',atBeijing('2026-10-02')),/DAY_NOT_CLOSED/));
test('aggregation rejects duplicated cash identities',()=>{
  const m=sample(),t=clone(m.transactions[0]);t.id='other-id';
  assert.throws(()=>aggregateDaily([...m.transactions,t],m.calendar,m.merchantId,m.asOf),/DUPLICATE_CASH_MOVEMENT/);
});
test('transfers and household draws cannot inflate operational forecast history',()=>{
  const m=sample(),extras=['transfer','ownerDraw'].map((kind,i)=>({...m.transactions[0],id:'extra'+i,cashKey:'extra'+i,kind}));
  assert.deepEqual(aggregateDaily([...m.transactions,...extras],m.calendar,m.merchantId,m.asOf),m.history);
});
test('unposted and not-yet-known transactions are not historical receipts',()=>{
  const m=sample(),t=clone(m.transactions[0]);t.id='x';t.cashKey='x';t.availableAt=m.asOf+1;
  const p={...t,id:'p',cashKey:'p',status:'pending',availableAt:m.asOf-1};
  assert.deepEqual(aggregateDaily([...m.transactions,t,p],m.calendar,m.merchantId,m.asOf),m.history);
});
test('settlement percentile and censored count are separate',()=>{
  const r=settlementStats([0,0,1,2,4].map((d,i)=>pair('p'+i,d)).concat(pair('open',0,'open')),pairOptions);
  assert.equal(r.empiricalDelayDays,4);assert.equal(r.completedCount,5);assert.equal(r.openCount,1);
});
test('pending settlement is not classified as zero-delay completion',()=>{
  const r=settlementStats([pair('p',0,'open')],pairOptions);
  assert.equal(r.completedCount,0);assert.equal(r.empiricalDelayDays,null);
});
test('early settlement lateness is zero, never negative',()=>{
  const r=settlementStats([pair('p',-1)],{...pairOptions,minSamples:1});assert.equal(r.empiricalDelayDays,0);
});
test('different channel and different merchant do not contaminate delay sample',()=>{
  const r=settlementStats([{...pair('a',9),channel:'D'},{...pair('b',9),merchantId:'OTHER'}],pairOptions);
  assert.equal(r.completedCount,0);
});
test('future completion at historical as-of remains open',()=>{
  const r=settlementStats([pair('p',60)],pairOptions);assert.equal(r.openCount,1);
});
test('delay scenarios cannot target payments or posted receipts',()=>{
  const m=sample();assert.throws(()=>buildDelayScenarios(m.events,{selectedIds:[m.events[0].id]}),/DELAY_TARGET/);
  m.events[1].status='posted';assert.throws(()=>buildDelayScenarios(m.events,{selectedIds:m.delayTargetIds}),/DELAY_TARGET/);
});
test('manual delay has no invented probability and does not mutate events',()=>{
  const m=sample(),before=JSON.stringify(m.events);
  const s=buildDelayScenarios(m.events,{selectedIds:m.delayTargetIds,manualDelayDays:2});
  assert.equal(s[1].timeOverrides[m.events[1].id],m.events[1].at+2*DAY_MS);
  assert.equal(s[1].probability,null);assert.equal(JSON.stringify(m.events),before);
});
test('receipt moved beyond horizon is not forced into horizon-end cash',()=>{
  const m=sample(),r=computeEnhancements(m,{delayDays:8});
  const late=r.stress.scenarios.find(s=>s.id==='manual_delay');
  assert.equal(late.endingBeforeWithdrawalCents,-20000);
});
test('same-timestamp cash is scanned debit-first conservatively',()=>{
  const m=sample();m.events[1].at=m.events[2].at;
  const r=evaluate(toEngineInput(m,[{id:'base'}]));assert.equal(r.maxWithdrawalCents,0);
});
test('reserve block detects temporary error even if end-of-block error recovers',()=>{
  const base={origin:1,trainingEnd:'2026-01-01',days:['2026-01-02','2026-01-03'],sourceRefs:['hand']};
  const inc={...base,predictions:[100000,100000],actual:[90000,130000]};
  const out={...base,predictions:[50000,50000],actual:[60000,60000]};
  const [b]=bufferBlocks([inc],[out]);assert.deepEqual(b.prefixErrors,[20000,0]);assert.equal(b.stressCents,20000);
});
test('reserve errors from unaligned origins are refused',()=>{
  assert.throws(()=>bufferBlocks([{origin:1,days:[]}],[{origin:2,days:[]}]),/FOLD_ALIGNMENT/);
});
test('reserve advice hand case q .8 => 500 yuan, 100 yuan additional',()=>{
  const r=recommendReserve(blocks,{currentReserveCents:40000,q:.8,roundingCents:10000,minBlocks:5});
  assert.equal(r.suggestedReserveCents,50000);assert.equal(r.extraCents,10000);
});
test('reserve recommendation cannot automatically lower existing reserve',()=>{
  const r=recommendReserve(blocks,{currentReserveCents:100000,q:.8,minBlocks:5});assert.equal(r.suggestedReserveCents,100000);
});
test('small reserve sample declines new advice',()=>{
  const r=recommendReserve([],{currentReserveCents:60000});
  assert.equal(r.status,'INSUFFICIENT_SAMPLE');assert.equal(r.suggestedReserveCents,60000);
});
test('reserve change requires explicit confirmation',()=>{
  const input=toEngineInput(sample(),[{id:'base'}]);
  assert.throws(()=>applyReserveAdvice(input,{currentReserveCents:60000,suggestedReserveCents:80000}),/USER_CONFIRMATION/);
});
test('reserve changes respect scheduled future floors',()=>{
  const m=sample(),input=toEngineInput(m,[{id:'base'}]);input.reserveChanges=[{at:m.asOf+DAY_MS,cents:90000}];
  const applied=applyReserveAdvice(input,{currentReserveCents:60000,suggestedReserveCents:80000},true);
  assert.equal(applied.reserveChanges[0].cents,90000);assert.equal(input.reserveCents,60000);
});
test('changed current reserve invalidates a stale reserve advice',()=>{
  assert.throws(()=>applyReserveAdvice({reserveCents:70000},{currentReserveCents:60000,suggestedReserveCents:80000},true),/STALE/);
});
test('adapter blocks unconfirmed forecast from the hard cash engine',()=>{
  const e=sample().events[1];e.provenance='forecast';assert.throws(()=>toEngineEvent(e,e.merchantId),/FORECAST_NOT_CONFIRMED/);
});
test('adapter requires posted cash to reconcile with opening balance',()=>{
  const e=sample().events[1];e.status='posted';assert.throws(()=>toEngineEvent(e,e.merchantId),/POSTED_OPENING/);
});
test('inside-scope transfer does not create cash',()=>{
  const e=sample().events[1];e.kind='transfer';e.transferScope='inside';assert.equal(toEngineEvent(e,e.merchantId).deltaCents,0);
});
test('cross-merchant ledger event is refused',()=>{
  const e=sample().events[1];assert.throws(()=>toEngineEvent(e,'OTHER'),/CROSS_MERCHANT/);
});
test('baseline controlled case: 1200; closing at x=0 is 1800',()=>{
  const r=computeEnhancements(sample());assert.equal(r.baseline.status,'FEASIBLE');
  assert.equal(r.baseline.maxWithdrawalCents,120000);assert.equal(r.baseline.scenarios[0].endingBeforeWithdrawalCents,180000);
});
test('delayed controlled case: infeasible, 200 payment gap, 800 reserve gap',()=>{
  const r=computeEnhancements(sample());assert.equal(r.stress.status,'PAYMENT_GAP');assert.equal(r.stress.feasible,false);
  assert.equal(r.stress.maxWithdrawalCents,0);assert.equal(r.stress.paymentGapCents,20000);assert.equal(r.stress.bufferGapCents,80000);
});
test('600 yuan opening regression: future receipts cannot finance an immediate draw',()=>{
  const m=sample();m.openingCents=60000;m.events=m.events.filter(e=>e.direction==='in');
  const r=computeEnhancements(m);assert.equal(r.baseline.maxWithdrawalCents,0);assert.equal(r.baseline.feasible,true);
});
test('forecast change cannot increase the hard withdrawal limit',()=>{
  const a=sample(),b=sample();b.history.forEach(r=>r.inflowCents*=10);
  assert.equal(computeEnhancements(a).baseline.maxWithdrawalCents,computeEnhancements(b).baseline.maxWithdrawalCents);
});
test('applying a higher reserve cannot increase the withdrawal limit',()=>{
  const m=sample(),r=computeEnhancements(m),s=computeEnhancements(m,{reserveConfirmed:true});
  assert.ok(s.withReserve.maxWithdrawalCents<=r.stress.maxWithdrawalCents);
  const input=toEngineInput(m,[{id:'base'}]),next=applyReserveAdvice(input,r.reserveAdvice,true);
  assert.equal(evaluate(next).maxWithdrawalCents,100000);
});
test('source revision or ledger date change invalidates cached enhancement results',()=>{
  const m=sample(),r=computeEnhancements(m);assert.equal(isResultCurrent(r,m),true);
  m.events[1].at+=DAY_MS;m.events[1].version++;m.revision++;assert.equal(isResultCurrent(r,m),false);
});
test('historical correction invalidates forecast and reserve advice together',()=>{
  const m=sample(),r=computeEnhancements(m);m.history[0].inflowCents++;m.historyRevision++;
  assert.equal(isResultCurrent(r,m),false);
});
test('missing prediction history degrades without disabling confirmed cash calculations',()=>{
  const m=sample();m.history=m.history.slice(-20);
  const r=computeEnhancements(m);assert.equal(r.forecast,null);assert.ok(r.forecastUnavailable);
  assert.equal(r.baseline.maxWithdrawalCents,120000);
});
test('stale history is visibly refused instead of forecasting wrong dates',()=>{
  const m=sample();m.history.pop();const r=computeEnhancements(m);assert.equal(r.forecastUnavailable,'STALE_HISTORY');
});
test('three fixtures have complete 84-day synthetic histories and immutable calculations',()=>{
  for(const m of fixtures){validateDaily(m.history);assert.equal(m.history.length,84);
    const before=JSON.stringify(m);computeEnhancements(m);assert.equal(JSON.stringify(m),before);}
});
