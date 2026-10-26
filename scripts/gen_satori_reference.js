// Reference renders using the real @satorijs/element parser (Koishi i18n backend).
const Element = require('./package/lib/index.cjs');
const cases = [
  ["找到以下物品：{#each items as item}{item.name_loc}{#if item !== items[items.length - 1]}、{/if}{/each}", {items: [{name_loc: "A"}, {name_loc: "B&C"}, {name_loc: "<D>"}]}],
  ["Matching items: \n{#each items as item}\n{item.name_loc}{#if item !== items[items.length - 1]}, {/if}\n{/each}", {items: [{name_loc: "A"}, {name_loc: "B"}]}],
  ["No keywords provided. \n{#if show}Displaying full item list per current configuration\n{:else}No content available\n{/if}", {}],
  ["No keywords provided. \n{#if show}Displaying full item list per current configuration\n{:else}No content available\n{/if}", {show: true}],
  ["未输入关键字参数。根据当前配置{#if show}，将返回全部物品列表{:else}无内容可发送{/if}。", {}],
  ["Found {count} items, exceeding maximum display limit ({max} items)\n{#if show}(Displaying item list){/if}", {count: 12, max: 5}],
  ["群友 {name} 段位变动：{prev.medal}{prev.star} → {curr.medal}{curr.star}", {name: "张&三", prev: {medal: "传奇", star: 3}, curr: {medal: "万古", star: 0}}],
  ["比赛编号：{0}", {}],
  ["比赛编号：{0}", [8127571787]],
  ["第{0}手", ["?"]],
  ["生涯胜场分<br>(对数)", {}],
  ["一周内，基于您段位±1 ({tiers}) 范围内各位置<b>选择率≥2%</b>按胜率从高到低前三名优势英雄：", {tiers: "传奇, 万古"}],
  ["热烈祝贺群友 AVATAR_PLACEHOLDER{name} 在天梯中再获进步，<br/>由 PREV_PLACEHOLDER 升为 CURR_PLACEHOLDER，再接再厉，再创辉煌！", {name: "<b>x</b>"}],
  ["比赛 {matchId} 已{#if odParsed}由 OpenDota {/if}解析，生成图片并发布于{#each guilds as guild}{guild.platform}:{guild.guildId}{#if guild !== guilds[guilds.length - 1]}、{/if}{/each}。", {matchId: 1, odParsed: true, guilds: [{platform: "p", guildId: "g1"}, {platform: "p", guildId: "g2"}]}],
  ["追踪到最新比赛 {match.id} 来自{#each messageToLogger as item}群组 {item.platform}:{item.guildId} 的玩家 [{#each item.players as player}{player.nickname}({player.steamId}){#if player !== item.players[item.players.length - 1]}、{/if}{/each}]{#if item !== messageToLogger[messageToLogger.length - 1]}、{/if}{/each}。", {match: {id: 99}, messageToLogger: [{platform: "a", guildId: "1", players: [{nickname: "x", steamId: 1}, {nickname: "y", steamId: 2}]}, {platform: "b", guildId: "2", players: [{nickname: "z", steamId: 3}]}]}],
  ["  \n  leading and trailing  \n  ", {}],
  ["{#each heroes as hero}{hero}{#if hero !== heroes[heroes.length - 1]}, {/if}{/each}", {heroes: ["Axe", "Axe", "Lina"]}],
  ["绑定成功，\nID：{userId}\n别名：{nickName}\nSteamID：{steamId}", {userId: "10001", nickName: "", steamId: "123"}],
  ["A <font color='#7d7d7d'>(Melee & Ranged)</font>", {}],
  ["{missing.deep.path} ok", {}],
  ["value {count}", {count: 1.5}],
];
const out = cases.map(([src, ctx]) => {
  const els = Element.parse(src, ctx);
  return { src, ctx, markup: els.map(e => e.toString()).join(""), plain: els.map(e => e.toString(true)).join("") };
});
console.log(JSON.stringify(out));
