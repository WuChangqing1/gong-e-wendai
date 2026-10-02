/** Compile-time contract for the dependency-free ES module. Amounts are integer cents at runtime. */
export type EventKind='settlement'|'purchase'|'rent'|'refund'|'tax'|'transfer'|'ownerDraw';
export type Method='seasonal_naive'|'weekday_median'|'ses';
export type Field='inflowCents'|'outflowCents';
export interface CashEvent {
  id:string;cashKey:string;merchantId:string;kind:EventKind;direction:'in'|'out';
  amountCents:number;at:number;status:'planned'|'pending'|'posted'|'voided';currency:'CNY';
  scope:'business';confirmed:boolean;version:number;sourceRefs:string[];
  provenance?:'confirmed'|'forecast';includedInOpening?:boolean;transferScope?:'inside'|'outside';
  channel?:string;title?:string;
}
export interface DailyCash {merchantId:string;day:string;complete:true;inflowCents:number;outflowCents:number;sourceRefs:string[]}
export interface SettlementPair {id:string;merchantId:string;channel:string;scheduledAt:number;knownAt:number;actualAt?:number;status:'open'|'completed'|'cancelled';sourceRef:string}
export interface EnhancementBundle {
  merchantId:string;name?:string;revision:number;historyRevision:number;asOf:number;end:number;
  openingCents:number;reserveCents:number;reserveChanges?:{at:number;cents:number}[];
  balanceConfirmed:boolean;obligationsConfirmed:boolean;events:CashEvent[];history:DailyCash[];
  settlements:SettlementPair[];delayTargetIds:string[];
}
export interface EnhancementOptions {delayDays?:number;delayQuantile?:number;reserveQuantile?:number;reserveConfirmed?:boolean}
export interface CashPoint {at:number;eventId:string;sourceRefs:string[];balanceCents:number;reserveCents:number;headroomCents:number;paymentGapCents:number;bufferGapCents:number;appliedCount:number;causalEventIds?:string[]}
export interface ScenarioResult {id:string;points:CashPoint[];minHeadroomCents:number;endingBeforeWithdrawalCents:number;paymentGapCents:number;bufferGapCents:number;gaps:CashPoint[];limitingPoint:CashPoint;orderedCashEventIds:string[];pendingAtLimit:{eventId:string;expectedAt:number;amountCents:number;sourceRefs:string[]}[]}
export interface EngineResult {
  status:'FEASIBLE'|'PAYMENT_GAP'|'BELOW_BUFFER'|'INPUT_INCOMPLETE';feasible:boolean|null;
  maxWithdrawalCents:number|null;paymentGapCents?:number;bufferGapCents?:number;
  scenarios?:ScenarioResult[];limitingScenarioId?:string;limitingPoint?:CashPoint;errors?:string[];basis?:string;
}
export interface Scenario {id:string;label:string;timeOverrides:Record<string,number>;delayDays:number;basis:string;sourceRefs:string[];probability:null}
export interface EngineInput {asOf:number;end:number;openingCents:number;reserveCents:number;currency:'CNY';reserveChanges:{at:number;cents:number}[];balanceConfirmed:true;obligationsConfirmed:true;events:(CashEvent & {state:'scheduled'|'included_in_opening'|'cancelled';deltaCents?:number})[];scenarios:Scenario[]}
export interface ErrorMetrics {count:number;maeCents:number;rmseCents:number}
export interface ForecastFold {origin:number;trainingEnd:string;days:string[];predictions:number[];actual:number[];sourceRefs:string[]}
export interface Backtest {method:Method;field:Field;horizon:number;folds:ForecastFold[];metrics:ErrorMetrics;byHorizon:(ErrorMetrics&{horizon:number})[];disclosure:string}
export interface ForecastResult {
  selected:Record<Field,Method>;
  results:Record<Field,{method:Method;validation:Backtest;holdout:Backtest}[]>;
  diagnostics:Record<Field,{needsReview:boolean;selectedHoldoutMaeCents:number;baselineHoldoutMaeCents:number;explanation:string}>;
  selectionDays:number;alphaBps:number;trainingEnd:string;historyDays:number;
  daily:{day:string;inflowCents:number;outflowCents:number;netCents:number;provenance:'forecast';confirmed:false;sourceRefs:string[]}[];
  disclosure:string;
}
export interface ErrorBlock {origin:number;trainingEnd:string;days:string[];stressCents:number;prefixErrors:number[];sourceRefs:string[]}
export interface ReserveAdvice {status:'SUGGESTION'|'INSUFFICIENT_SAMPLE';currentReserveCents:number;suggestedReserveCents:number;extraCents:number;empiricalErrorCents:number|null;blockCount:number;q:number;roundingCents:number;minBlocks:number;requiresConfirmation:true;sourceRefs:string[];disclosure:string}
export interface SettlementStats {merchantId:string;channel:string;completedCount:number;openCount:number;overdueOpenCount:number;status:'DESCRIPTIVE_SAMPLE'|'INSUFFICIENT_SAMPLE';q:number;minSamples:number;empiricalDelayDays:number|null;sourceRefs:string[];openSourceRefs:string[];disclosure:string}
export interface EnhancementResult {
  basisKey:string;ledgerRevision:number;historyRevision:number;forecast:ForecastResult|null;
  forecastUnavailable:string|null;blocks:ErrorBlock[];reserveAdvice:ReserveAdvice|null;
  settlement:SettlementStats;scenarios:Scenario[];baseline:EngineResult;stress:EngineResult;
  withReserve:EngineResult;reserveApplied:boolean;forecastAffectsWithdrawable:false;disclosure:string;
}
export const KINDS:EventKind[];
export function toEngineEvent(event:CashEvent,merchantId:string):EngineInput['events'][number];
export function toEngineInput(bundle:EnhancementBundle,scenarios:Scenario[]):EngineInput;
export function computeEnhancements(bundle:EnhancementBundle,options?:EnhancementOptions):EnhancementResult;
export function makeBasisKey(bundle:EnhancementBundle,options?:EnhancementOptions):string;
export function isResultCurrent(result:EnhancementResult,bundle:EnhancementBundle,options?:EnhancementOptions):boolean;
