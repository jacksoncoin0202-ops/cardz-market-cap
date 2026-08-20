#!/usr/bin/env node
/*
 * deploy literal 嘅契約閘。純靜態 + 一段 `sh` 執行證明，由 run_all_tests.py glob
 * `scripts/test-*.mjs` 收（唔開瀏覽器、唔使 dev server、唔連 network）。
 *
 * ── 點解要有呢個檔 ───────────────────────────────────────────────────────
 * 出街唔係由一個 flag、一個 branch、一個 tag 決定，係由**一段散文入面有冇一個七粒字
 * 嘅 literal** 決定：push 上 main，AWS 接收端睇 commit message，撞到個 literal 就
 * `git pull` + `docker compose up --build`，而且佢派嘅係 **branch tip**，唔係嗰粒
 * commit。即係話「喺 commit message 度講返呢件事」同「叫佢出街」係同一個動作。
 *
 * 2026-08-21 就係咁中招：`2851497c` 個 body 寫「所以呢粒唔帶 <literal>」去解釋自己
 * 唔出街，markdown 粗體仲將個 literal 迫咗落自己一行。個 literal 入咗 message，
 * AWS 側按契約會當佢要 deploy。（blast radius 係零：冇改 app 源碼，91 秒後 daily
 * release `ccd46928` 蓋過咗，而 AWS 派 tip。但呢個係彩數，唔係設計。）
 *
 * ── 個真根因唔係「我打錯字」，係「條規矩本身有分歧」───────────────────────
 * 究竟接收端睇 **subject** 定 **成個 message**？呢邊驗唔到：
 *   · docs/AWS_GITHUB_PULL_DEPLOY.md 同 WSL ~/cardz-aws/docs/DEPLOY_WEBHOOK_SMOKE.md
 *     兩份契約都只寫「commit message 含 <literal>」，冇分 subject／body；
 *   · AWS_GITHUB_PULL_DEPLOY.md §1–§3 自己聲明係**未安裝嘅提案**；
 *   · 接收端源碼喺 AWS，IT 管，任何一個 accessible repo 都冇；
 *   · webhook delivery 每次 `response.payload` 都係空，200 淨係個 ack —— `92dbae7a`
 *     subject body 都冇個 literal，一樣 200。即係 200 咩都證明唔到。
 *
 * 所以呢個檔**唔賭邊個讀法啱**，而係取消個分歧：
 *
 *     個 literal 一出現喺 message，就一定要出現喺 subject。
 *
 * 咁樣「淨睇 subject」同「睇全文」兩種讀法，對每一粒合法 commit 都會俾同一個答案，
 * AWS 側點實作都唔會再有意外。三層嘢守住呢句：
 *   防  scripts/githooks/commit-msg        —— commit 果一刻就擋（要裝）
 *   判  scripts/deploy_watch.ps1            —— 睇全文（保守嗰邊），模糊形態出黃 banner
 *   查  呢個檔嘅 R1 history ratchet         —— 已經溜咗入 history 嘅，只准減唔准加
 *
 * ── 邊啲紅、邊啲淨係出 note ───────────────────────────────────────────────
 * `scripts/daily_public_release.sh:94` 喺 `set -e` 之下跑 run_all_tests.py：呢度紅一條，
 * 當日 production release 就 abort。呢個 repo 出過一次「假閘擋住正確 release」。所以：
 *   紅   ＝ 睇 repo 入面嘅檔同 git history 就判到，同機器環境無關（W／H／C／R）
 *   note ＝ 要掂 filesystem／有冇 `sh`／人哋部機個 hooks dir 有咩（I／E）
 * hook 裝唔到唔會 abort release，但 R1 一定會喺下次見到條數升咗嗰陣嗌。
 *
 * ── 檢查 ─────────────────────────────────────────────────────────────────
 *  W  deploy_watch.ps1 用**全文**判（`--format=%B` / `$tagInFull`），唔准退返去
 *     subject-only；payload 側同樣睇 `$msgFull`，而且分得出「淨係喺 body」。
 *  H  hook 源碼真係做緊嗰件事：marker、literal、subject 比對、剝 scissors。
 *  C  .gitattributes 釘死 hook 做 LF（冇呢條 → CRLF → `bad interpreter: /bin/sh^M`
 *     → hook 靜靜唔行 = 裝咗等於冇裝）；AGENTS.md 有條規矩。
 *  I  真係 call installGitHooks() 去裝／對數（呢個就係 hook 嘅 call site）。
 *  E  攞 `sh` 真跑個 hook 五個 case，包括「literal 淨係喺 --verbose 個 diff 入面」
 *     同「淨係喺 # 注釋行」—— 呢兩個唔剝走就會自己咬自己。
 *  R  history ratchet：HEAD 行得到嘅 commit 入面「body 有、subject 冇」嘅，
 *     只准係下面 FROZEN 三粒。多一粒 = 紅。
 *  N  負控制：每一條 W／H／C 嘅 predicate 都剝走／種返個 needle 證實會反口，
 *     R 亦有合成 message 對照（規矩 9：加咗檢查要即場證明佢會 fire）。
 */
import { execFileSync } from "node:child_process";
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { installGitHooks, lf, SRC_DIR, MARKER } from "./install_githooks.mjs";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const failed = [];
const notes = [];
const check = (label, condition, detail) => { if (!condition) failed.push(detail ? `${label}: ${detail}` : label); };
const read = (rel) => readFileSync(join(ROOT, rel), "utf8");

/* 個 literal 唔准喺呢個檔度寫死做一串 —— 呢個檔自己個 diff 一入 --verbose message
   就會多咗幾個 literal 出嚟。hook 剝 scissors 擋得住，但唔好淨係靠嗰下。 */
const TAG = `[${"dep"}${"loy"}]`;

const WATCH_REL = "scripts/deploy_watch.ps1";
const HOOK_REL = "scripts/githooks/commit-msg";
const ATTR_REL = ".gitattributes";
const AGENTS_REL = "AGENTS.md";

/* ── predicate 工具 ─────────────────────────────────────────────────────
   present／absent 兩邊都要有得「反口」，先算證到條 check 會 fire。
   剝 needle 用 split/join（剝晒每一個），唔靠 anchor 唯一 —— `$tagInFull` 本來就
   出現幾次，用「剝第一個」嘅寫法會斬錯位變靜默假陰性。 */
const hasAll = (src, needles) => needles.every((n) => src.includes(n));
const hasNone = (src, needles) => needles.every((n) => !src.includes(n));
const strip = (src, needle) => src.split(needle).join("");

/** @type {{name:string, rel:string, present:string[], absent:string[]}[]} */
const CONTRACTS = [
  {
    name: "W1 deploy_watch 攞全文",
    rel: WATCH_REL,
    present: ["--format=%B", "$tagInFull", "$tagInSubject"],
    absent: [],
  },
  {
    name: "W2 deploy_watch 條拒絕閘睇全文（唔係 subject）",
    rel: WATCH_REL,
    present: ["if (-not $tagInFull) {"],
    absent: ["if (-not ($subject -match"],
  },
  {
    name: "W3 payload 側都係睇全文",
    rel: WATCH_REL,
    present: ["HasDeployTag = ($msgFull -match", "$msgFull = [string]$p.head_commit.message"],
    absent: ["HasDeployTag = ($msg -match"],
  },
  {
    name: "W4 deploy_watch 分得出「淨係喺 body」呢個模糊形態",
    rel: WATCH_REL,
    present: ["TagOnlyInBody"],
    absent: [],
  },
  {
    name: "H1 hook 有 marker（install_githooks 靠佢分得出邊個係我哋嗰個）",
    rel: HOOK_REL,
    present: [MARKER],
    absent: [],
  },
  {
    name: "H2 hook 認個 literal，而且比對緊 subject",
    rel: HOOK_REL,
    present: [`TAG='${TAG}'`, "SUBJECT=", "\"$SUBJECT\" | grep -qF -- \"$TAG\""],
    absent: [],
  },
  {
    name: "H3 hook 剝走 git 塞落尾嘅注釋同 --verbose diff",
    rel: HOOK_REL,
    present: [">8", "/^#/d"],
    absent: [],
  },
  {
    name: "C1 .gitattributes 釘死 hook 做 LF",
    rel: ATTR_REL,
    present: ["scripts/githooks/* text eol=lf"],
    absent: [],
  },
  {
    name: "C2 AGENTS.md 寫低咗條規矩",
    rel: AGENTS_REL,
    present: ["17. **個 deploy literal", "scripts/githooks/commit-msg"],
    absent: [],
  },
];

for (const c of CONTRACTS) {
  const src = read(c.rel);
  const ok = (s) => hasAll(s, c.present) && hasNone(s, c.absent);
  check(c.name, ok(src),
    `${c.rel} —— 唔見：[${c.present.filter((n) => !src.includes(n)).join(" | ")}]` +
    `　唔應該有但有：[${c.absent.filter((n) => src.includes(n)).join(" | ")}]`);

  /* N：逐個 needle 剝走／種返，條 predicate 一定要反口。 */
  for (const n of c.present) {
    check(`N(${c.name}) 剝走「${n}」之後應該紅`, !ok(strip(src, n)),
      "FAILED TO FIRE —— 剝走 needle 都仲係綠，即係呢條 check 從來冇睇緊嘢");
  }
  for (const n of c.absent) {
    check(`N(${c.name}) 種返「${n.slice(0, 40)}」之後應該紅`, !ok(`${src}\n${n}\n`), "FAILED TO FIRE");
  }
}

/* ── I：真係裝 ───────────────────────────────────────────────────────────
   呢個 call 就係 hook 嘅 call site。裝唔到唔紅（唔准為咗環境問題 abort 當日 release），
   但一定要印出嚟，而且 R1 仍然守住個結果。 */
let installedOk = false;
try {
  for (const r of installGitHooks()) {
    if (r.state === "foreign") {
      notes.push(`⚠️ I: ${r.name} 嗰個位已經有另一個 hook（唔係我哋嘅），冇覆蓋 → ${r.path}`);
      continue;
    }
    const dest = readFileSync(r.path, "utf8");
    check(`I: 裝落去嗰份 ${r.name} 要同源碼一樣`, lf(dest) === lf(readFileSync(join(SRC_DIR, r.name), "utf8")), r.path);
    check(`I: 裝落去嗰份 ${r.name} 唔准有 CR（CRLF → bad interpreter → 靜靜唔行）`, !dest.includes("\r"), r.path);
    installedOk = true;
    notes.push(`I: ${r.name} ${{ ok: "已經係最新", installed: "裝咗", updated: "更新咗" }[r.state]} → ${r.path}`);
  }
} catch (err) {
  notes.push(`⚠️ I: 裝唔到 hook（環境問題，唔當紅）—— ${err.message}`);
}

/* ── E：攞 sh 真跑一次 ──────────────────────────────────────────────────
   靜態掃字串證明唔到個 hook 行起上嚟啱。呢度真係餵五個 message 落去睇 exit code。 */
function findSh() {
  try { execFileSync("sh", ["-c", "exit 0"], { stdio: "ignore" }); return "sh"; } catch { /* 落 fallback */ }
  try {
    /* Git for Windows：exec-path 係 <root>/mingw64/libexec/git-core，sh 喺 <root>/usr/bin */
    const ep = execFileSync("git", ["--exec-path"], { encoding: "utf8" }).trim();
    const cand = join(ep.replace(/[/\\]mingw\d+[/\\]libexec[/\\]git-core$/i, ""), "usr", "bin", "sh.exe");
    if (existsSync(cand)) { execFileSync(cand, ["-c", "exit 0"], { stdio: "ignore" }); return cand; }
  } catch { /* 冇就冇 */ }
  return null;
}

const SCISSORS = "# ------------------------ >8 ------------------------";
const CASES = [
  { name: "body 有、subject 冇 → 一定要擋", msg: `docs: 講返件事\n\n所以呢粒唔帶 ${TAG}\n`, want: 1 },
  { name: "subject 有 → 放行", msg: `feat: 出街 ${TAG}\n\nbody 講嘢\n`, want: 0 },
  { name: "完全冇 literal → 放行", msg: "chore: 執嘢\n\n冇提個 tag\n", want: 0 },
  {
    name: "literal 淨係喺 --verbose 個 diff 入面 → 放行（唔剝就自己咬自己）",
    msg: `chore: 改 hook\n\n${SCISSORS}\ndiff --git a/x b/x\n+TAG='${TAG}'\n`,
    want: 0,
  },
  { name: "literal 淨係喺 # 注釋行 → 放行", msg: `chore: 執嘢\n\n# 提示：唔好打 ${TAG}\n`, want: 0 },
];

const sh = findSh();
if (!sh) {
  notes.push("⚠️ E: 搵唔到 `sh`，跳過 hook 執行證明（唔當紅）");
} else {
  const dir = mkdtempSync(join(tmpdir(), "cardz-hook-"));
  try {
    /* 跑源碼嗰份（LF 正規化過）：E 驗邏輯，行尾由上面 I 嗰條「唔准有 CR」驗。 */
    const hookPath = join(dir, "commit-msg");
    writeFileSync(hookPath, lf(read(HOOK_REL)), "utf8");
    for (const [i, c] of CASES.entries()) {
      const msgPath = join(dir, `msg${i}.txt`);
      writeFileSync(msgPath, c.msg, "utf8");
      let code = 0;
      try { execFileSync(sh, [hookPath, msgPath], { stdio: "pipe" }); } catch (e) { code = e.status ?? -1; }
      check(`E${i + 1}: ${c.name}`, code === c.want, `exit=${code}，預期 ${c.want}`);
    }
    notes.push(`E: 用 ${sh} 真跑咗 ${CASES.length} 個 case`);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}

/* ── R：history ratchet ─────────────────────────────────────────────────
   已經溜咗入 history 嘅「body 有、subject 冇」，凍結喺呢三粒。只准減唔准加。
   （唔要求佢哋一定搵得返 —— shallow clone 會少嘢，嗰個係環境唔係缺陷。） */
const FROZEN = new Set(["2851497c", "b6cb3ed7", "2bf34eba"]);

/** message → 係咪「body 有、subject 冇」 */
function bodyOnlyTag(msg) {
  if (!msg.includes(TAG)) return false;
  const subject = msg.split("\n").find((l) => l.trim() !== "") ?? "";
  return !subject.includes(TAG);
}

/* R 嘅負控制：用合成 message 證 classifier，唔好攞真 history 去自證。 */
check("N(R): classifier 要捉到「body 有、subject 冇」", bodyOnlyTag(`docs: 講嘢\n\n呢粒唔帶 ${TAG}\n`), "FAILED TO FIRE");
check("N(R): subject 有嗰啲唔准當違規", !bodyOnlyTag(`feat: 出街 ${TAG}\n\nbody\n`), "假陽性");
check("N(R): 完全冇 literal 唔准當違規", !bodyOnlyTag("chore: 執嘢\n\nbody\n"), "假陽性");

let scanned = 0;
try {
  const raw = execFileSync("git", ["log", "--format=%H%x00%B%x01", "HEAD"],
    { cwd: ROOT, encoding: "utf8", maxBuffer: 64 * 1024 * 1024 });
  const offenders = [];
  for (const rec of raw.split("\x01")) {
    if (!rec.trim()) continue;
    const idx = rec.indexOf("\x00");
    if (idx < 0) continue;
    const sha = rec.slice(0, idx).trim().slice(0, 8);
    const msg = rec.slice(idx + 1);
    scanned += 1;
    if (bodyOnlyTag(msg)) {
      offenders.push({ sha, subject: (msg.split("\n").find((l) => l.trim() !== "") ?? "").slice(0, 60) });
    }
  }
  const fresh = offenders.filter((o) => !FROZEN.has(o.sha));
  check("R1: 唔准再有新嘅「body 有 literal、subject 冇」commit", fresh.length === 0,
    `${fresh.map((o) => `${o.sha} ${o.subject}`).join(" ／ ")}　—— 呢啲 commit 兩種讀法答案唔同，` +
    "AWS 側可能已經當佢出街。修法：以後個 literal 一入 message 就擺埋上 subject（AGENTS.md 17）。");
  notes.push(`R: 掃咗 ${scanned} 粒 commit，違規 ${offenders.length}（凍結 ${FROZEN.size}、新增 ${fresh.length}）`);
} catch (err) {
  notes.push(`⚠️ R: 讀唔到 git history（環境問題，唔當紅）—— ${err.message.split("\n")[0]}`);
}

for (const n of notes) console.log(` · ${n}`);
if (failed.length) {
  console.error(`FAIL test-deploy-tag-contract (${failed.length})`);
  for (const f of failed) console.error(` - ${f}`);
  process.exit(1);
}
console.log(
  `PASS test-deploy-tag-contract（${CONTRACTS.length} 條契約 + 逐個 needle 負控制、` +
  `hook ${installedOk ? "已裝" : "未裝(note)"}、${sh ? `${CASES.length} 個執行 case` : "冇 sh 跳過"}、` +
  `history ${scanned} 粒、凍結 ${FROZEN.size} 粒）`,
);
