# BOX sidecar — 037 / FE04

037 只加 BOX。036 PSA10 seed 唔准污染。公開路徑 /box，唔係 /sealed。

契約：HANDOFF_037_FE04.md。

## 已落地（2026-08-14，live 已確認）

- Sidecar：data/public/box-subset.json（307／275／307）
- Loader：讀完 PSA10 seed 再掛 box block
- FE：/box、nav BOX、METHOD & DATA
- Docker：image COPY box-subset.json，唔寫入 seed
- Fallback：拎走 overlay 即返 036 / FE03
