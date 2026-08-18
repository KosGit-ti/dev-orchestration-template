import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

/** Tailwind のクラス名を条件付きで組み立て、衝突を後勝ちで解決する。 */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}
