export const routes=['到发台账','晨报预览','订阅计划','追补账本','分发回执'];export const page=()=>{let n=Number(location.hash.slice(1)||0);return Number.isInteger(n)&&n>=0&&n<5?n:0};
