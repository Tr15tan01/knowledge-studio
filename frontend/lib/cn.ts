import { clsx, type ClassValue } from "clsx";
import { extendTailwindMerge } from "tailwind-merge";

// Teach tailwind-merge about the project's custom type scale so `text-display` isn't treated as a colour.
const twMerge = extendTailwindMerge({ extend: { classGroups: { "font-size": [{ text: ["display", "headline"] }] } } });

export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}
