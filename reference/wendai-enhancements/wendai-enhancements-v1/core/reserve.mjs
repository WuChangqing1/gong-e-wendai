import { assert, integer, sum, quantile } from './common.mjs';

/** Extra cash needed to cover the largest adverse cumulative error within each 7-day forecast block. */
export function bufferBlocks(incomeFolds,expenseFolds) {
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
export function recommendReserve(blocks,{currentReserveCents,q=0.9,roundingCents=10000,minBlocks=8}) {
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

export function applyReserveAdvice(engineInput,advice,confirmed=false) {
  assert(confirmed===true,'USER_CONFIRMATION_REQUIRED');
  assert(advice.currentReserveCents===engineInput.reserveCents,'STALE_RESERVE_ADVICE');
  integer(advice.suggestedReserveCents,'SUGGESTED_RESERVE');
  assert(advice.suggestedReserveCents>=engineInput.reserveCents,'NO_AUTO_LOWERING');
  return {...engineInput,reserveCents:advice.suggestedReserveCents,
    reserveChanges:(engineInput.reserveChanges??[]).map(r=>({...r,cents:Math.max(r.cents,advice.suggestedReserveCents)}))};
}
