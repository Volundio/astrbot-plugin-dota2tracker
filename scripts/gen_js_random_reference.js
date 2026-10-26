// Copy of inaba@1.1.1 Random (Koishi) and the plugin's enhancedSimpleHashToSeed.
class Random {
  constructor(get = Math.random) { this.get = get; }
  bool(p) { if (p >= 1) return true; if (p <= 0) return false; return this.get() < p; }
  real(...args) { const lower = args.length > 1 ? args[0] : 0; const upper = args[args.length - 1]; return this.get() * (upper - lower) + lower; }
  int(...args) { return Math.floor(this.real(...args)); }
  splice(source) { return source.splice(Math.floor(this.get() * source.length), 1)[0]; }
  pick(source, count) { if (count === void 0) return this.pick(source, 1)[0]; const copy = source.slice(); const result = []; count = Math.min(copy.length, count); for (let i = 0; i < count; i += 1) result.push(this.splice(copy)); return result; }
  weightedPick(weights) { const total = Object.entries(weights).reduce((prev, [, curr]) => prev + curr, 0); const pointer = this.get() * total; let counter = 0; for (const key in weights) { counter += weights[key]; if (pointer < counter) return key; } }
}
function enhancedSimpleHashToSeed(inputString) {
  const encoded = btoa(inputString);
  let total = 0; let complexFactor = 1;
  for (let i = 0; i < encoded.length; i++) { total += encoded.charCodeAt(i) * complexFactor; complexFactor++; total %= 9973; }
  total = ((total % 9973) * (total % 9973)) % 9973;
  return (total % 1000) / 1000;
}
const strings = ["123456789-0", "123456789-24", "20251026-123456789-0", "20251026-123456789-1", "8127571787-139876543-128", "", "a", "abc-def", "1000000000-5"];
const out = { seeds: strings.map(s => [s, enhancedSimpleHashToSeed(s)]), picks: [] };
const heroIds = Object.keys({1:1,2:1,3:1,5:1,10:1,100:1,145:1,155:1,23:1});
for (const s of strings) {
  const seed = enhancedSimpleHashToSeed(s);
  const r = new Random(() => seed);
  out.picks.push({ s, pick: r.pick(heroIds), bool: r.bool(0.5), int08: r.int(0, 8), int030: r.int(0, 30), int020: r.int(0, 20),
    weighted: r.weightedPick({"3": 2.5, "10": 7.25, "1": 0.5, "100": 12}), pickArr: r.pick(["a","b","c","d"], 2) });
}
console.log(JSON.stringify(out));
