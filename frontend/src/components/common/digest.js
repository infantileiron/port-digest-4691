import {numeric} from '../../utils/dates.js';import {esc} from '../../utils/html.js';
export function digest(r){
 let prov=r.provenance?`<p class="eyebrow">账本溯源 #${r.provenance.ledger_id} · 快照 #${r.provenance.snapshot_id} · 应发 ${esc(r.provenance.due_at)} · 当时港站 ${esc(r.provenance.station_at_due)} · 收件箱 ${esc(r.provenance.recipient_at_due)}</p>`:'';
 let empty=r.empty_day?`<p class="notice">空日：该报告日在当时港站无到发记录，仍按规则留回执，不伪造样本与0值。</p>`:'';
 return `${prov}${empty}<div class="metrics"><div><strong>${r.completed} / ${r.scheduled}</strong>已到发 / 计划</div><div><strong>${numeric(r.p50)}</strong>P50延误 / 分</div><div><strong>${numeric(r.p95)}</strong>P95延误 / 分</div></div><p>未到发 ${r.pending} · 延误 ${r.late} · 延误率 ${r.late_rate===null?'未定义':(r.late_rate*100).toFixed(1)+'%'} · 来源 ${r.source_count ?? r.rows.length} 条</p><p class="notice">${esc(r.method)}</p>`}
