import { assert, integer, quantile, DAY_MS } from './common.mjs';

/** A completed-pair descriptive statistic, not a survival model; open items are explicitly counted. */
export function settlementStats(pairs, {merchantId,channel,asOf,q=0.9,minSamples=12}) {
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
export function buildDelayScenarios(events, {selectedIds,manualDelayDays=2,stats=null}) {
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
