/** 工e稳袋参考计算内核。仅处理已规范化的模拟现金事件；无银行接口、预测或授信。 */
export function yuanToCents(text) {
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
export function evaluate(input) {
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
export function makeConsultationPacket(facts, selectedIds, question, revision) {
  if (typeof question !== 'string' || !question.trim()) throw new Error('QUESTION_REQUIRED');
  const selected = new Set(selectedIds);
  return {schemaVersion:1, revision, question, facts:facts.filter(f=>selected.has(f.id) && f.scope === 'business').map(f=>({
    id:f.id, kind:f.kind, amountCents:f.amountCents, expectedAt:f.expectedAt, status:f.status,
    sourceLabel:f.sourceLabel
  }))};
  // Free text can itself contain private data: preview/redaction is still required before a real submission.
}

export function demoInput() {
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
