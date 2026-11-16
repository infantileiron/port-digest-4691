/** @typedef {{id:number,code:string,station:string,planned_at:string|null,actual_at:string|null,note:string}} Arrival */
/** @typedef {{id:number,name:string,station:string,daily_at:string,recipient:string,enabled:boolean,next_run_at:string}} Subscription */
/** @typedef {{id:number,subscription_id:number,report_day:string,state:string,payload:object,review_note:string}} Receipt */
/** @typedef {{id:number,subscription_id:number,snapshot_id:number,due_at:string,report_day:string,station:string,recipient:string,state:string,receipt_id:number|null,attempts:number,basis:string}} LedgerEntry */
