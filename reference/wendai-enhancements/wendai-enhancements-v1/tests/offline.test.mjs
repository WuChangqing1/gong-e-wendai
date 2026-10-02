import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import {createMerchants} from '../data/fixtures.mjs';
import {computeEnhancements} from '../core/adapter.mjs';
const root=new URL('../',import.meta.url);
const bundle=readFileSync(new URL('demo/bundle.js',root),'utf8');
const ui=readFileSync(new URL('demo/ui.js',root),'utf8');
function mount(){
  const nodes=new Map();
  const get=id=>{
    if(!nodes.has(id))nodes.set(id,{value:({merchant:'0',opening:'3600',delay:'2'})[id]??'',innerHTML:'',
      handlers:{},addEventListener(event,fn){this.handlers[event]=fn;}});
    return nodes.get(id);
  };
  const context=vm.createContext({structuredClone,document:{getElementById:get}});
  vm.runInContext(bundle,context);vm.runInContext(ui,context);
  return {context,get,change(id,value){get(id).value=value;get(id).handlers.change();}};
}
test('offline: packed core agrees with source on three merchant calculations',()=>{
  const context=vm.createContext({});vm.runInContext(bundle,context);
  const p=context.WendaiModules['core/adapter.mjs'];
  for(const m of createMerchants())assert.deepEqual(JSON.parse(JSON.stringify(p.computeEnhancements(m))),JSON.parse(JSON.stringify(computeEnhancements(m))));
});
test('offline: initial UI renders the 1200 to zero demonstration',()=>{
  const {get}=mount();assert.match(get('app').innerHTML,/1,200.00/);assert.match(get('app').innerHTML,/付款缺口/);
  assert.doesNotMatch(get('app').innerHTML,/暂不能计算/);
});
test('offline: decimal opening balance is parsed from a string, without binary rounding rejection',()=>{
  const {get,change}=mount();change('opening','0.29');assert.doesNotMatch(get('app').innerHTML,/暂不能计算/);
  assert.match(get('app').innerHTML,/即使不提用/);
});
test('offline: invalid input shows an error instead of a stale result',()=>{
  const {get,change}=mount();change('opening','12.001');assert.match(get('app').innerHTML,/暂不能计算/);
  assert.doesNotMatch(get('app').innerHTML,/1,200.00/);
});
test('offline: changing merchant reveals the synthetic regime-break warning',()=>{
  const {get,change}=mount();change('merchant','2');assert.match(get('app').innerHTML,/留出段变差，需复核/);
  assert.match(get('app').innerHTML,/6,900.00/);
});
test('offline: changing a basis input invalidates local reserve confirmation',()=>{
  const {get,change}=mount();get('reserve').handlers.click();assert.match(get('app').innerHTML,/已在本页模拟确认/);
  change('opening','600');assert.match(get('app').innerHTML,/尚未确认，不改变原留底/);
});
test('diagnostic: poor holdout performance is disclosed without reselecting on holdout',()=>{
  const r=computeEnhancements(createMerchants()[2]);
  assert.equal(r.forecast.selected.inflowCents,'weekday_median');
  assert.equal(r.forecast.diagnostics.inflowCents.needsReview,true);
  assert.ok(r.forecast.diagnostics.inflowCents.selectedHoldoutMaeCents>r.forecast.diagnostics.inflowCents.baselineHoldoutMaeCents);
});
