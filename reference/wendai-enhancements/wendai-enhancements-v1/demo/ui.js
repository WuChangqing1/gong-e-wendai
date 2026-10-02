(() => {
  const {createMerchants}=WendaiModules['data/fixtures.mjs'];
  const {computeEnhancements,toEngineInput}=WendaiModules['core/adapter.mjs'];
  const {applyReserveAdvice}=WendaiModules['core/reserve.mjs'];
  const {evaluate,yuanToCents}=WendaiModules['core/cash-engine.mjs'];
  const {METHOD_LABELS}=WendaiModules['core/forecast.mjs'];
  const merchants=createMerchants();let reserveConfirmed=false;
  const $=id=>document.getElementById(id);
  const money=c=>'¥'+(c/100).toLocaleString('zh-CN',{minimumFractionDigits:2,maximumFractionDigits:2});
  const esc=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  $('merchant').innerHTML=merchants.map((m,i)=>`<option value="${i}">${esc(m.name)}</option>`).join('');
  function state(r){return r.status==='FEASIBLE'?(r.maxWithdrawalCents>0?'所选条件下可提用':'满足留底，暂无可提用余量'):r.status==='PAYMENT_GAP'?'即使不提用，仍有付款缺口':r.status==='BELOW_BUFFER'?'能付款，但不足留底':'资料不足，暂不报金额';}
  function plot(paths,labels,{step=false,baseline=0}={}) {
    const W=680,H=225,L=65,R=16,T=15,B=36;
    const vals=paths.flatMap(a=>a.map(p=>p[1])).concat([0,baseline]);
    const lo=Math.min(...vals),hi=Math.max(...vals)+10000,span=Math.max(1,hi-lo);
    const xs=paths.flatMap(a=>a.map(p=>p[0])),xmin=Math.min(...xs),xmax=Math.max(...xs);
    const x=v=>L+(v-xmin)/(xmax-xmin||1)*(W-L-R),y=v=>T+(hi-v)/span*(H-T-B);
    let grid='';for(let i=0;i<4;i++){const v=lo+(hi-lo)*i/3;grid+=`<line x1="${L}" x2="${W-R}" y1="${y(v)}" y2="${y(v)}" stroke="#e6e8e7"/><text x="${L-8}" y="${y(v)+4}" text-anchor="end" fill="#677780" font-size="11">${(v/100).toFixed(0)}</text>`;}
    const colors=['#30638f','#a31827'];
    const curves=paths.map((p,i)=>{
      const d=p.map(([a,b],j)=>j?(step?`H ${x(a)} V ${y(b)}`:`L ${x(a)} ${y(b)}`):`M ${x(a)} ${y(b)}`).join(' ');
      return `<path d="${d}" fill="none" stroke="${colors[i]}" stroke-width="2.5"/>`;
    }).join('');
    return `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(labels.join('与'))}趋势；具体数值见下方表格">${grid}<line x1="${L}" x2="${W-R}" y1="${y(baseline)}" y2="${y(baseline)}" stroke="#955b10" stroke-dasharray="5 4"/>${curves}<text x="${L}" y="${H-8}" font-size="11">起点</text><text x="${W-R}" y="${H-8}" text-anchor="end" font-size="11">未来7天</text></svg><div class="key">${labels.map((v,i)=>`<span style="color:${colors[i]}">${esc(v)}</span>`).join('')}</div>`;
  }
  function render(){
    try{
      const m=structuredClone(merchants[Number($('merchant').value)]);
      const v=yuanToCents($('opening').value),d=Number($('delay').value);
      if(v<0||!Number.isInteger(d)||d<0||d>8)throw Error('请输入有效金额与0—8的整数延迟天数');
      m.openingCents=v;
      const r=computeEnhancements(m,{delayDays:d,reserveConfirmed}),f=r.forecast,a=r.reserveAdvice;
      const worst=r.stress.scenarios.find(s=>s.id===r.stress.limitingScenarioId);
      const chart=plot([r.baseline.scenarios[0].points.map(p=>[p.at,p.balanceCents]),worst.points.map(p=>[p.at,p.balanceCents])],['按计划，尚未提用','最紧情景，尚未提用'],{step:true,baseline:m.reserveCents});
      const changed=a?evaluate(applyReserveAdvice(toEngineInput(m,[r.scenarios[0]]),a,true)):r.baseline;
      const rows=f?.daily??[];
      const errors=f?['inflowCents','outflowCents'].flatMap(field=>f.results[field].map(c=>`<tr><td>${field==='inflowCents'?'到账':'日常采购'}</td><td>${METHOD_LABELS[c.method]}${f.selected[field]===c.method?' ✓':''}${f.selected[field]===c.method&&f.diagnostics[field].needsReview?' · 留出段变差，需复核':''}</td><td>${money(c.holdout.metrics.maeCents)}</td><td>${money(c.holdout.metrics.rmseCents)}</td></tr>`)).join(''):'';
      $('app').innerHTML=`<div class="grid"><section class="card"><h2>按当前计划</h2><div class="money">${money(r.baseline.maxWithdrawalCents??0)}</div><p class="${r.baseline.feasible?'ok':'bad'}">${state(r.baseline)}</p><p class="meta">仅使用已确认事件；先检查今天当下。</p></section><section class="card"><h2>同时检查延期情景</h2><div class="money">${money(r.stress.maxWithdrawalCents??0)}</div><p class="${r.stress.feasible?'ok':'bad'}">${state(r.stress)}</p><p class="meta">付款差${money(r.stress.paymentGapCents??0)} · 距留底差${money(r.stress.bufferGapCents??0)}。二者不能相加。</p></section><section class="card"><h2>历史误差建议留底</h2><div class="money">${money(a?.suggestedReserveCents??m.reserveCents)}</div><p class="meta">当前留底${money(m.reserveCents)}；${a?.blockCount??0}个重叠检验窗口。建议须本人确认。</p><button id="reserve">${reserveConfirmed?'撤销本次模拟确认':'模拟确认留底建议'}</button></section></div>
      <div class="two"><section class="card"><h2>同一笔钱，晚到账会怎样？</h2><p class="meta">两条曲线均按未提用 x=0 比较。已完成结算样本${r.settlement.completedCount}笔，未完成${r.settlement.openCount}笔；经验q90延期${r.settlement.empiricalDelayDays??'不足'}天。样本分位数不是未来概率。</p>${chart}<details><summary>逐情景结果与来源</summary><div class="scroll"><table><thead><tr><th>情景</th><th>可提用</th><th>期末余额 x=0</th><th>状态</th></tr></thead><tbody>${r.stress.scenarios.map(s=>`<tr><td>${esc(r.scenarios.find(x=>x.id===s.id).label)}</td><td>${money(s.minHeadroomCents>=0?s.minHeadroomCents:0)}</td><td>${money(s.endingBeforeWithdrawalCents)}</td><td>${s.minHeadroomCents<0?'不可行':'可行'}</td></tr>`).join('')}</tbody></table></div><p class="small">延期仅作用于指定结算款；未完成结算不冒充“按时到账”。未完成项的处理仍需人工核对。</p></details></section>
      <section class="card"><h2>未来7天日常收付预测</h2><p class="notice small">这是预测，未确认。图中到账收入不用于提高“今天可提用”；预测总额不得再叠加同一口径的已知结算款。</p>${rows.length?plot([rows.map((p,i)=>[i,p.inflowCents]),rows.map((p,i)=>[i,p.outflowCents])],['预计到账','预计日常采购']):'<p>历史不足，暂不预测。</p>'}<div class="scroll"><table><thead><tr><th>日期</th><th>预计到账</th><th>预计采购</th><th>预计日常净流入</th></tr></thead><tbody>${rows.map(p=>`<tr><td>${p.day}</td><td>${money(p.inflowCents)}</td><td>${money(p.outflowCents)}</td><td>${money(p.netCents)}</td></tr>`).join('')}</tbody></table></div><p class="meta">不等于利润；房租、税款、退款等仍按确认账本单独管理。</p></section></div>
      <section class="card"><h2>预测是否比简单办法更好？让留出的数据检验。</h2><p class="meta">前56天选择方法，后28天验证；每种方法22个预测起点、154个日误差。✓为事先选定方法，不根据检验结果重新挑选。全部为模拟。</p><div class="scroll"><table><thead><tr><th>预测目标</th><th>方法</th><th>留出段 MAE</th><th>留出段 RMSE</th></tr></thead><tbody>${errors}</tbody></table></div></section>
      <section class="card"><h2>把预测误差转成留底建议</h2><p>对每次过去的7日预测，找到“到账被高估或采购被低估”造成的最大中途累计差额。取历史样本较高水平，向上取整到100元，并保留原留底下限。</p><p class="notice">${esc(a?.disclosure??r.forecastUnavailable??'历史不足')}</p><p>按时情景：原留底${money(m.reserveCents)}对应可提用${money(r.baseline.maxWithdrawalCents??0)}；采用建议留底后${changed.feasible?'可提用'+money(changed.maxWithdrawalCents):state(changed)}。</p><p><strong>${reserveConfirmed?'已在本页模拟确认':'尚未确认，不改变原留底'}：</strong>共同约束结果为${state(r.withReserve)}，可提用显示${money(r.withReserve.maxWithdrawalCents??0)}。</p><details><summary>查看参数与来源</summary><p>中位数窗口28天；指数平滑α=0.3；留底经验q=0.9；不少于8个完整检验窗口；向上取整100元。这些是演示设计参数，不是行业标准或真实概率。</p><p>数据版本：账本v${m.revision}，历史v${m.historyRevision}。随机种子${m.seed}，历史${m.history.length}天、${m.transactions.length}笔。</p><pre>${esc((a?.sourceRefs??[]).slice(0,10).join('\n'))}\n…来源总数 ${(a?.sourceRefs??[]).length}。完整来源在模块返回结果中。</pre></details></section>`;
      $('reserve').addEventListener('click',()=>{reserveConfirmed=!reserveConfirmed;render();});
    }catch(e){$('app').innerHTML=`<div class="card bad">暂不能计算：${esc(e.message)}</div>`;}
  }
  ['merchant','opening','delay'].forEach(id=>$(id).addEventListener('change',()=>{reserveConfirmed=false;render();}));
  $('reset').addEventListener('click',()=>{$('opening').value='3600';$('delay').value='2';reserveConfirmed=false;render();});
  render();
})();
