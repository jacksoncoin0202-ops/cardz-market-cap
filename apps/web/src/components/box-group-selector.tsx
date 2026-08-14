"use client";

import { motion } from "framer-motion";
import { useId } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { copy } from "@/lib/i18n";
import { sealedGroups, type Locale, type SealedGroup } from "@/lib/types";

export type BoxScope = "all" | SealedGroup;

export function normaliseBoxScope(value: string | null | undefined): BoxScope {
  return sealedGroups.includes(value as SealedGroup) ? (value as SealedGroup) : "all";
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
    ...sealedGroups.map((group) => ({ scope: group as BoxScope, label: t.box.groups[group] })),
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
