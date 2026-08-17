"use client";

import { motion } from "framer-motion";
import { useId } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { copy } from "@/lib/i18n";
import { sealedTcgs, type Locale, type SealedGroup, type SealedTcg } from "@/lib/types";

export type BoxScope = "all" | SealedTcg;

/* `product.group` 仍然係 "optcg-en" 呢類四值，掣只認前半段（TCG）。 */
export function tcgOfGroup(group: SealedGroup): SealedTcg {
  return group.split("-")[0] as SealedTcg;
}

export function normaliseBoxScope(value: string | null | undefined): BoxScope {
  if (sealedTcgs.includes(value as SealedTcg)) return value as SealedTcg;
  /* 向後相容：舊連結 / 舊書籤仲係 ?group=ptcg-jp，當佢係 "ptcg"（唔理語言）。 */
  const head = (value ?? "").split("-")[0];
  return sealedTcgs.includes(head as SealedTcg) ? (head as SealedTcg) : "all";
}

export function BoxGroupSelector({ locale }: { locale: Locale }) {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const t = copy[locale];
  const active = normaliseBoxScope(params.get("group"));
  const pillId = `box-group-pill-${useId()}`;

  const select = (scope: BoxScope) => {
    const query = new URLSearchParams(params.toString());
    if (scope === "all") query.delete("group");
    else query.set("group", scope);
    const suffix = query.toString();
    router.replace(suffix ? `${pathname}?${suffix}` : pathname, { scroll: false });
  };

  const options: Array<{ scope: BoxScope; label: string }> = [
    { scope: "all", label: t.box.groupAll },
    ...sealedTcgs.map((tcg) => ({ scope: tcg as BoxScope, label: t.box.groups[tcg] })),
  ];

  return (
    <div className="period-selector box-group-selector" role="group" aria-label={t.nav.box}>
      {options.map((option) => (
        <button
          key={option.scope}
          type="button"
          aria-pressed={active === option.scope}
          onClick={() => select(option.scope)}
        >
          {active === option.scope && (
            <motion.span
              layoutId={pillId}
              className="period-pill"
              transition={{ type: "spring", bounce: 0.18, duration: 0.35 }}
            />
          )}
          <span className="period-label">{option.label}</span>
        </button>
      ))}
    </div>
  );
}
