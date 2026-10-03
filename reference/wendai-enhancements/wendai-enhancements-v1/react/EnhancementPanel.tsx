import { useMemo, useState } from 'react';
import { computeEnhancements } from '../core/adapter.mjs';
import type { EnhancementBundle, EngineResult, Field, Method } from '../core/adapter.mjs';
import './enhancements.css';

/** No persistence or permissions here. Parent owns the ledger, revisions, and trusted authorization. */
export interface EnhancementPanelProps {
  bundle:EnhancementBundle;
  onOpenSource?:(sourceRef:string)=>void;
  /** Parent must compare ALL revisions and basisKey before accepting. Do not pass this payload to a bank. */
  onConfirmReserve?:(change:{reserveCents:number;expectedRevision:number;expectedHistoryRevision:number;basisKey:string;delayDays:number})=>void;
}
const labels:Record<Method,string>={seasonal_naive:'上周同一天',weekday_median:'近四周同星期中位数',ses:'简单指数平滑'};
const money=(cents:number|null|undefined)=>cents==null?'暂不能计算':`¥${(cents/100).toLocaleString('zh-CN',{minimumFractionDigits:2,maximumFractionDigits:2})}`;
function description(r:EngineResult) {
  if(r.status==='INPUT_INCOMPLETE')return '资料不足，请先核对';
  if(r.status==='PAYMENT_GAP')return '即使不提用，也有付款缺口';
  if(r.status==='BELOW_BUFFER')return '能付款，但不足留底';
  return r.maxWithdrawalCents===0?'满足留底，暂无可提用余量':'仅在当前输入和所选情景成立时有效';
}

export function EnhancementPanel({bundle,onOpenSource,onConfirmReserve}:EnhancementPanelProps) {
  const [delayDays,setDelayDays]=useState(2);
  const [acceptedBasis,setAcceptedBasis]=useState<string|null>(null);
  // Immutable bundle replacement required; parent increments revision/historyRevision on edits.
  const calculated=useMemo(()=>{
    try{return {result:computeEnhancements(bundle,{delayDays}),error:null};}
    catch(error){return {result:null,error:error instanceof Error?error.message:'INPUT_ERROR'};}
  },[bundle,delayDays]);
  const r=calculated.result;
  const locallyConfirmed=!!r && acceptedBasis===r.basisKey;
  const preview=useMemo(()=>{
    if(!locallyConfirmed)return null;
    try{return computeEnhancements(bundle,{delayDays,reserveConfirmed:true});}catch{return null;}
  },[bundle,delayDays,locallyConfirmed]);
  if(!r)return <section className="wd-enhance"><p role="alert">暂不能计算：{calculated.error}。不显示可提用数字。</p></section>;
  const f=r.forecast,a=r.reserveAdvice,shown=preview??r;
  const canConfirm=a?.status==='SUGGESTION' && r.stress.status!=='INPUT_INCOMPLETE';
  function confirmReserve(){
    if(!r || !a || !canConfirm)return;
    if(onConfirmReserve)onConfirmReserve({reserveCents:a.suggestedReserveCents,
      expectedRevision:bundle.revision,expectedHistoryRevision:bundle.historyRevision,basisKey:r.basisKey,delayDays});
    else setAcceptedBasis(r.basisKey); // Demo-only local application, invalidated automatically by any basis change.
  }
  return <section className="wd-enhance" aria-label="工e稳袋预测与留底增强">
    <header><p className="wd-eyebrow">工e稳袋 · 模拟数据增强模块</p><h2>钱会怎么走，留多少更稳妥？</h2>
      <p>预测只是参考，不计入已到账余额，也不增加今日可提用金额。</p></header>
    <label className="wd-control">指定结算款再晚几天？
      <select value={delayDays} onChange={e=>setDelayDays(Number(e.target.value))}>
        {[0,1,2,3,4,7].map(n=><option key={n} value={n}>{n}个自然日</option>)}
      </select>
    </label>
    <p className="wd-muted">同时保留历史样本压力情景；将手动延期调为0，不会自动删除历史压力。</p>
    <div className="wd-cards">
      <article><h3>按当前计划可提用</h3><strong>{money(r.baseline.maxWithdrawalCents)}</strong><p>{description(r.baseline)}</p></article>
      <article><h3>同时检查延期后</h3><strong>{money(shown.withReserve.maxWithdrawalCents)}</strong><p>{description(shown.withReserve)}</p></article>
      <article><h3>建议留底，须本人确认</h3><strong>{money(a?.suggestedReserveCents)}</strong>
        <p>当前留底 {money(bundle.reserveCents)} · 历史误差窗口 {a?.blockCount??0}个</p>
        <button type="button" disabled={!canConfirm||locallyConfirmed} onClick={confirmReserve}>
          {locallyConfirmed?'本次已模拟确认':onConfirmReserve?'确认采用建议留底':'仅在本页模拟采用'}
        </button>
      </article>
    </div>
    <p className="wd-notice">{r.settlement.disclosure} 已完成{r.settlement.completedCount}笔；未完成{r.settlement.openCount}笔。</p>
    <h3>日历日预测 · {f?.daily[0]?.day??'待补历史'}起</h3>
    {!f?<p role="status">暂不预测：{r.forecastUnavailable}。已确认事件的时点推演仍可使用。</p>:<>
      {(Object.keys(f.diagnostics) as Field[]).some(k=>f.diagnostics[k].needsReview)&&
        <p className="wd-notice" role="status">需要复核：事先选中的方法在留出段比简单基线误差更大，不能保证更准。</p>}
      <div className="wd-scroll"><table><caption>每日预测总额；不可再叠加同口径已知结算。不是利润、不是剩余日内现金预测。</caption>
        <thead><tr><th>日期</th><th>预计到账</th><th>预计日常采购</th><th>来源</th></tr></thead>
        <tbody>{f.daily.map(p=><tr key={p.day}><th scope="row">{p.day}</th><td>{money(p.inflowCents)}</td><td>{money(p.outflowCents)}</td>
          <td><button type="button" disabled={!onOpenSource} onClick={()=>onOpenSource?.(p.sourceRefs[0])}>查看依据</button></td></tr>)}</tbody>
      </table></div>
      <details><summary>方法、检验误差与参数</summary><p>前56天选方法，后续时段检验。MAE、RMSE均为模拟数据上的元金额误差；不是准确率。重叠窗口不独立。</p>
        <div className="wd-scroll"><table><thead><tr><th>项目</th><th>方法</th><th>留出MAE</th><th>留出RMSE</th></tr></thead>
          <tbody>{(['inflowCents','outflowCents'] as Field[]).flatMap(field=>f.results[field].map(c=><tr key={field+c.method}>
            <th scope="row">{field==='inflowCents'?'到账':'采购'}</th><td>{labels[c.method]}{f.selected[field]===c.method?' · 事先选定':''}</td>
            <td>{money(c.holdout.metrics.maeCents)}</td><td>{money(c.holdout.metrics.rmseCents)}</td></tr>))}</tbody></table></div>
        <p>α=0.3；中位数窗口28天；留底经验分位数q=0.9；向上取整100元。均为设计参数，不是概率保证。</p>
      </details></>}
    <details><summary>留底依据与来源</summary><p>{a?.disclosure??'暂无建议'}</p>
      <p>经验不利累计误差 {money(a?.empiricalErrorCents)}；仅建议，不保证未来安全。</p>
      <ul>{(a?.sourceRefs??[]).slice(0,8).map(ref=><li key={ref}><button type="button" disabled={!onOpenSource} onClick={()=>onOpenSource?.(ref)}>{ref}</button></li>)}</ul>
      <p>完整依据共{a?.sourceRefs.length??0}条，可从返回值 sourceRefs 检索。账本v{r.ledgerRevision} · 历史v{r.historyRevision}。</p>
    </details>
    <footer>{r.disclosure} 本组件不实现账户权限、银行提交、转账或授信。</footer>
  </section>;
}
