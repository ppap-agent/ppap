/**
 * Type-level negative cases. tsc must reject each next line.
 * This file is excluded from design lint because the examples are intentionally illegal.
 */
import type {ReactNode} from 'react'
import {Search} from 'lucide-react'
import {Button, Checkbox, FileInput, IconButton, Input, Link} from './controls'
import {Icon} from './icons'

const styled = {title: '说明', style: {opacity: 0.2}}
const named = {title: '说明', className: 'x'}
const colored = {title: '说明', color: 'red'}

export function directStyleRejected() {
  // @ts-expect-error direct style is not a primitive prop
  return <Button style={{opacity: 0.2}}>提交</Button>
}

export function spreadStyleRejected() {
  // @ts-expect-error an existing object must not carry style into Button
  return <Button {...styled}>提交</Button>
}

export function spreadClassRejected() {
  // @ts-expect-error an existing object must not carry className into Link
  return <Link {...named}>说明</Link>
}

export function spreadColorRejected() {
  // @ts-expect-error an existing object must not carry color into Input
  return <Input {...colored} />
}

export function wrapperForwardRejected(props: {children?: ReactNode; title?: string; style?: {opacity: number}}) {
  // @ts-expect-error a wrapper must not forward style onto Button
  return <Button {...props}>{props.children}</Button>
}

export function systemMarkerRejected() {
  // @ts-expect-error callers cannot replace the system marker
  return <Button data-ui="hack">提交</Button>
}

export function fileTypeRejected() {
  // @ts-expect-error file input type is fixed
  return <FileInput label="文件" type="text" />
}

export function checkboxTypeRejected() {
  // @ts-expect-error checkbox type is fixed
  return <Checkbox label="同意" type="text" />
}

export function iconButtonTypeRejected() {
  // @ts-expect-error icon button type is fixed
  return <IconButton label="关闭" type="submit" />
}

export function iconPixelRejected() {
  // @ts-expect-error icon size is sm, md, or lg
  return <Icon icon={Search} size={37} />
}
