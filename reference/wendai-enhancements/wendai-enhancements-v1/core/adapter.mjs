import { assert, integer } from './common.mjs';
import { evaluate } from './cash-engine.mjs';
import { evaluateForecasts } from './forecast.mjs';
import { settlementStats, buildDelayScenarios } from './settlement.mjs';
import { bufferBlocks, recommendReserve, applyReserveAdvice } from './reserve.mjs';

export const KINDS=['settlement','purchase','rent','refund','tax','transfer','ownerDraw'];
/** Explicit bridge to the supplied reference engine. Never silently turn a forecast into confirmed cash. */
export function toEngineEvent(event,merchantId) {
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

export function toEngineInput(bundle,scenarios) {
  assert(bundle.balanceConfirmed===true&&bundle.obligationsConfirmed===true,'CONFIRMATION_REQUIRED');
  integer(bundle.revision,'LEDGER_REVISION',1);integer(bundle.historyRevision,'HISTORY_REVISION',1);
  return {asOf:bundle.asOf,end:bundle.end,openingCents:bundle.openingCents,reserveCents:bundle.reserveCents,
    reserveChanges:bundle.reserveChanges??[],currency:'CNY',balanceConfirmed:true,obligationsConfirmed:true,
    events:bundle.events.map(e=>toEngineEvent(e,bundle.merchantId)),scenarios};
}

/** Exact internal signature, not a cryptographic identifier; do not expose it in shared DTOs. */
export function makeBasisKey(bundle,options={}) { return JSON.stringify({bundle,options}); }
export function isResultCurrent(result,bundle,options={}) { return result.basisKey===makeBasisKey(bundle,options); }

export function computeEnhancements(bundle,options={}) {
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
