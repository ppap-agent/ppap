import { createElement, type SVGProps } from "react"

import { cn } from "@/lib/utils"

/** A line icon: its element list on a 24 × 24 grid (see src/components/icons.ts). */
export type IconDefinition = { name: string; node: readonly (readonly [string, Readonly<Record<string, string | number>>])[] }

/**
 * Thin outlined icon as inline SVG. Size comes from CSS (1em unless a parent or class sets it),
 * colour from currentColor; decorative unless given an accessible name.
 */
function Icon({ icon, className, ...props }: { icon: IconDefinition } & Omit<SVGProps<SVGSVGElement>, "children">) {
  return (
    <svg
      data-slot="icon"
      data-icon={icon.name}
      viewBox="0 0 24 24"
      width="1em"
      height="1em"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.75}
      strokeLinecap="round"
      strokeLinejoin="round"
      focusable="false"
      aria-hidden={props["aria-label"] || props["aria-labelledby"] ? undefined : true}
      className={cn("shrink-0", className)}
      {...props}
    >
      {icon.node.map(([tag, attributes], index) => createElement(tag, { key: index, ...attributes }))}
    </svg>
  )
}

export { Icon }
