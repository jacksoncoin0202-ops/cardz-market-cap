/**
 * 038 / FE05 = 037/FE04 + visual upgrade; 037/FE04 remains the fallback (tag fe04-live).
 * backend generation 冇郁（照樣 037 / 037db）——FE05 純粹係 presentation 層升級。
 *
 * fallback 兩個欄要**成組**：FE04 係配 037 出街嘅，所以 FALLBACK_GENERATION 同步由
 * "036" 改做 "037"。呢一組就係 docs/FE05_ROLLBACK.md 個錨點 tag `fe04-live`
 * （commit 4bed89a7）—— rollback runbook 靠 /api/health 呢兩個字判斷退去邊，
 * 出一組「036/FE04」呢種從未存在過嘅配搭會直接誤導。
 * （AGENTS.md rule 15 講嘅 036/FE03 係再上一手 fallback，已經被 037/FE04 取代。）
 */
export const PRODUCT_GENERATION = "037";
export const PRODUCT_GENERATION_ALIAS = "037db";
export const PRESENTATION = "FE05";
export const FALLBACK_GENERATION = "037";
export const FALLBACK_PRESENTATION = "FE04";
