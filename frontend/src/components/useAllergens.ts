import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { Allergen } from "../api/types";

let cache: Promise<Allergen[]> | null = null;

/** The 14 standard allergens (cached for the session). */
export function useAllergens(): { allergens: Allergen[]; byCode: Record<string, Allergen> } {
  const [allergens, setAllergens] = useState<Allergen[]>([]);
  useEffect(() => {
    cache ??= api<Allergen[]>("/allergens").catch((e) => {
      cache = null;
      throw e;
    });
    let alive = true;
    cache.then((a) => alive && setAllergens(a)).catch(() => {});
    return () => {
      alive = false;
    };
  }, []);
  const byCode = Object.fromEntries(allergens.map((a) => [a.code, a]));
  return { allergens, byCode };
}
