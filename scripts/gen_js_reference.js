const out = {};
const nums = [0, -0, 1, -1, 0.5, 1.5, 2.5, -2.5, 0.25, 0.75, 1.005, 1.045, 12.345, 123.456, 1e21, 1.5e16, 1e-7, 1.234e-7, 0.000001, 0.0000012, 123456789.123, 1/3, 2/3, -1/3, 87.33333333333333, 112.5, NaN, Infinity, -Infinity, 0.1+0.2, 255 - (51*255)/100, 1234.5678, 9999.995, 0.045, 5e-324, 1.7976931348623157e308, 100, 1e20, 123e18, 0.30000001192092896];
out.toString = nums.map(n => [String(n), n]);
out.toFixed = [];
for (const n of nums) for (const d of [0,1,2,3]) out.toFixed.push([n, d, n.toFixed(d)]);
out.round = [0.49999999999999994, 0.5, 1.5, 2.5, -0.5, -1.5, -2.5, 1234.4999999999998, 3.7, -3.7, 12.345*100].map(n => [n, Math.round(n)]);
out.locale = [0, 5, 1234, -1234, 1234567, 1234.5678, 0.12345, 1e21].map(n => [n, n.toLocaleString(), n.toLocaleString('en-US'), n.toLocaleString('zh-CN')]);
const units = ['year','month','day','hour','minute','second'];
out.rel = [];
for (const loc of ['zh-cn','en-us']) {
  const f = new Intl.RelativeTimeFormat(loc, {numeric: 'always'});
  for (const u of units) for (const v of [-0, -1, -2, -11, 1]) out.rel.push([loc, u, Object.is(v,-0) ? '-0' : v, f.format(v, u)]);
}
out.weekday = [];
for (const loc of ['zh-CN','en-US']) {
  for (let i=0;i<7;i++) { const d = new Date(Date.UTC(2024,0,1+i,12)); out.weekday.push([loc, d.getUTCDay(), d.toLocaleDateString(loc,{weekday:'long', timeZone:'UTC'}), d.toLocaleDateString(loc,{weekday:'short', timeZone:'UTC'})]); }
  for (let m=0;m<12;m++) { const d = new Date(Date.UTC(2024,m,15,12)); out.weekday.push([loc, 'm'+m, d.toLocaleDateString(loc,{month:'long', timeZone:'UTC'}), d.toLocaleDateString(loc,{month:'short', timeZone:'UTC'})]); }
}
console.log(JSON.stringify(out));
