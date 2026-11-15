"use client"

import * as React from "react"
import { cn } from "@/lib/utils"

function Table({ className, containerClassName, ...props }: React.ComponentProps<"table"> & {containerClassName?: string}) {
  return (
    <div
      data-slot="table-container"
      className={cn("relative w-full overflow-x-auto", containerClassName)}
    >
      <table
        data-slot="table"
        className={cn("w-full caption-bottom text-sm", className)}
        {...props}
      />
    </div>
  )
}

/** `sticky` keeps the header row visible while the table container scrolls. */
function TableHeader({ className, variant = "default", sticky = false, ...props }: React.ComponentProps<"thead"> & {variant?: "default" | "muted"; sticky?: boolean}) {
  return (
    <thead
      data-slot="table-header"
      className={cn("[&_tr]:border-b", variant === "muted" && "bg-header [&_th]:font-semibold [&_th]:text-[13px]", sticky && "[&_th]:sticky [&_th]:top-0 [&_th]:z-10 [&_th]:bg-header", className)}
      {...props}
    />
  )
}

function TableBody({ className, ...props }: React.ComponentProps<"tbody">) {
  return (
    <tbody
      data-slot="table-body"
      className={cn("[&_tr:last-child]:border-0", className)}
      {...props}
    />
  )
}

function TableFooter({ className, ...props }: React.ComponentProps<"tfoot">) {
  return (
    <tfoot
      data-slot="table-footer"
      className={cn(
        "border-t bg-muted/50 font-medium [&>tr]:last:border-b-0",
        className
      )}
      {...props}
    />
  )
}

/** `interactive={false}` is for rows that are not records, such as an empty state. */
function TableRow({ className, interactive = true, ...props }: React.ComponentProps<"tr"> & {interactive?: boolean}) {
  return (
    <tr
      data-slot="table-row"
      className={cn(
        "border-b transition-colors", interactive && "hover:bg-muted/50 has-aria-expanded:bg-muted/50 data-[state=selected]:bg-muted",
        className
      )}
      {...props}
    />
  )
}

function TableHead({ className, ...props }: React.ComponentProps<"th">) {
  return (
    <th
      data-slot="table-head"
      className={cn(
        "h-10 px-2 text-left align-middle font-medium whitespace-nowrap text-foreground [&:has([role=checkbox])]:pr-0",
        className
      )}
      {...props}
    />
  )
}

function TableCell({ className, ...props }: React.ComponentProps<"td">) {
  return (
    <td
      data-slot="table-cell"
      className={cn(
        "p-2 align-middle whitespace-nowrap [&:has([role=checkbox])]:pr-0 data-[selected=true]:bg-muted data-[selected=true]:outline data-[selected=true]:outline-1 data-[selected=true]:outline-ring",
        className
      )}
      {...props}
    />
  )
}

function TableCaption({
  className,
  ...props
}: React.ComponentProps<"caption">) {
  return (
    <caption
      data-slot="table-caption"
      className={cn("mt-4 text-sm text-muted-foreground", className)}
      {...props}
    />
  )
}

export {
  Table,
  TableHeader,
  TableBody,
  TableFooter,
  TableHead,
  TableRow,
  TableCell,
  TableCaption,
}
