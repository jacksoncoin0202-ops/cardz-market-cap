-- CARDZ 041: 公開 card id 同內部 opaque_id 分家。
--
-- 2026-08-11 查 DB 實測：catalog_variant 有 4 行 opaque_id 帶住供應商前綴
-- （id 1813/1814/1865/1866），其中 3 行上到榜，已經出咗街 —— 生產
-- /sitemap.xml、首頁同 /api/v1/market 都出得到。opaque_id 就係公開 URL
-- （/card/<opaque_id>），所以呢 4 個 id 直接將一個供應商代號寫喺 URL 上面。
--
-- 點解唔直接 rename：opaque_id 由 PLAN_036_FE02 D7 凍結，而且佢已經被兩個地方
-- 當成前像 hash 落咗鎖 —— catalog_source_identity.bind_evidence_json 同佢個
-- evidence_sha256、catalog_printing_identity.provenance_json.sourceRowSha256。
-- 改個名等於要重算兩個 stored hash、撞 D7、仲會令 converge_printing_identity 個
-- fingerprint fire。全部都可以避開。
--
-- 所以呢度分家：opaque_id 一個 byte 都唔郁，繼續做內部 identity；公開投影層
-- （apps/web/src/lib/live-db-snapshot.ts）出 COALESCE(alias.public_id,
-- variant.opaque_id）。冇 alias 嘅卡行為完全不變。
--
-- 個 CHECK 唔係裝飾：呢張表唯一嘅意義就係「公開 id 一定喺 cmc_ 命名空間」，
-- 一個放行嘅 alias 等於將問題由 catalog_variant 搬過嚟。

CREATE TABLE IF NOT EXISTS public_card_alias (
    variant_id BIGINT UNSIGNED NOT NULL,
    public_id VARCHAR(96) COLLATE utf8mb4_unicode_ci NOT NULL,
    reason VARCHAR(255) COLLATE utf8mb4_unicode_ci NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (variant_id),
    UNIQUE KEY uq_public_card_alias_public_id (public_id),
    CONSTRAINT fk_public_card_alias_variant
        FOREIGN KEY (variant_id) REFERENCES catalog_variant (id),
    CONSTRAINT ck_public_card_alias_namespace
        CHECK (public_id REGEXP '^cmc_[0-9a-f]{20,24}$')
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT IGNORE INTO cardz_schema_version (version_code) VALUES ('041');
