import type { IconDefinition } from "@fortawesome/free-solid-svg-icons"
import type { SVGProps } from "react"

import { cn } from "@/lib/utils"

export type { IconDefinition }

/**
 * Font Awesome Free icon (icons CC BY 4.0) as inline SVG. Size comes from CSS (1em unless a
 * parent or class sets it), colour from currentColor; decorative unless given an accessible name.
 */
function Icon({ icon, className, ...props }: { icon: IconDefinition } & Omit<SVGProps<SVGSVGElement>, "children">) {
  const [width, height, , , path] = icon.icon
  return (
    <svg
      data-slot="icon"
      data-icon={icon.iconName}
      viewBox={`0 0 ${width} ${height}`}
      width="1em"
      height="1em"
      fill="currentColor"
      focusable="false"
      aria-hidden={props["aria-label"] || props["aria-labelledby"] ? undefined : true}
      className={cn("shrink-0", className)}
      {...props}
    >
      {(Array.isArray(path) ? path : [path]).map((d) => <path key={d} d={d} />)}
    </svg>
  )
}

export { Icon }
