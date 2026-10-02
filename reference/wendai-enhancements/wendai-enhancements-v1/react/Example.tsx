import { useState } from 'react';
import { createMerchants } from '../data/fixtures.mjs';
import { EnhancementPanel } from './EnhancementPanel';
export default function Example(){
  const [merchants]=useState(()=>createMerchants());
  const [selected,setSelected]=useState(0);
  return <main><label>模拟商户 <select value={selected} onChange={e=>setSelected(Number(e.target.value))}>
    {merchants.map((m,i)=><option key={m.merchantId} value={i}>{m.name}</option>)}
  </select></label><EnhancementPanel key={merchants[selected].merchantId} bundle={merchants[selected]}
    onOpenSource={ref=>window.alert('来源定位占位：'+ref+'\n请接入现有来源抽屉；不向服务器发送。')}/></main>;
}
