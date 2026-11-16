import {esc} from '../../utils/html.js';import {LEDGER_STATE_TEXT} from '../../constants/enums.js';
const RECEIPT_TEXT={delivered:'已入箱',reviewed:'已复核',empty:'空日留痕'};
export const badge=s=>`<span class=tag>${esc(LEDGER_STATE_TEXT[s]||RECEIPT_TEXT[s]||s)}</span>`;
