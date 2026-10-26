const { DateTime } = require('./luxonpkg/package/build/node/luxon.js');
const out = { zone: process.env.TZ, format: [], relative: [], diffMonths: [] };
const stamps = [1736750866, 1736754130, 1709164800, 1704067199, 1711846800, 1730000000, 0, 1761465600];
const fmts = ["yyyy-MM-dd HH:mm:ss", "yyyyMMdd", "yyyy/MM/dd HH时mm分", "yyyy/MM/dd HH:mm", "yyyy-MM-dd HH:mm", "yyyy年L月d日 cccc", "cccc, LLLL d, yyyy", "yy-M-d h:mm a", "'quoted' yyyy", "EEE MMM dd"];
for (const s of stamps) for (const f of fmts) for (const loc of ['zh-CN', 'en-US'])
  out.format.push([s, f, loc, DateTime.fromSeconds(s).setLocale(loc).toFormat(f)]);
const base = DateTime.fromSeconds(1761480000); // 2025-10-26
const deltas = [5, 59, 61, 3599, 3601, 86399, 86401, 86400*2.5, 86400*27, 86400*29, 86400*30, 86400*31, 86400*45, 86400*60, 86400*200, 86400*364, 86400*366, 86400*400, 86400*800, 86400*1000];
for (const d of deltas) for (const loc of ['zh-cn','en-us']) {
  const t = base.minus({ seconds: d });
  out.relative.push([d, loc, t.setLocale(loc).toRelative({ base })]);
  out.diffMonths.push([d, base.diff(t, 'months').get('months')]);
}
// month-end / leap cases relative to explicit bases
const pairs = [[1709164800, 1711846800], [1706659200, 1709164800], [1704067199, 1706745599], [1735660800, 1738339200], [1709251200, 1740787200]];
out.pairs = [];
for (const [a, b] of pairs) { const ta = DateTime.fromSeconds(a), tb = DateTime.fromSeconds(b);
  out.pairs.push([a, b, tb.diff(ta, 'months').get('months'), tb.diff(ta, 'days').get('days'), tb.diff(ta, 'years').get('years'), ta.setLocale('zh-cn').toRelative({base: tb}), ta.setLocale('en-us').toRelative({base: tb})]); }
console.log(JSON.stringify(out));
