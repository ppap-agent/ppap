// What an accepted audit stands on, at the top of its result (#361): the customer whose rules were chosen, standard or deep
// mode, the rules frozen at acceptance, and what the round charged. All of it is fixed when the task is accepted; the
// points are read from the usage ledger and said as they stand, never guessed. Implementation details stay out.
import {useEffect, useState} from 'react'
import {request} from '../api'
import type {components} from '../generated/openapi'
import {customerName} from './customer-register'
import {deepModeTitle} from './deep-mode'
import {chargeText, type Charge} from './receipts-contract'
import {useCustomerCatalog} from './review-content'
import {openInPlace, surfaceAddress, taskUsageAddress} from './routes'

type Task = components['schemas']['AuditTask']

const link = 'text-primary underline-offset-4 hover:underline'

export function AuditBasis({task}: {task: Task}) {
  const catalog = useCustomerCatalog()
  const [charge, setCharge] = useState<{task: string; value: Charge | null}>()
  useEffect(() => {
    let current = true
    void request<Charge>(`/audit-tasks/${encodeURIComponent(task.id)}/charge`).then(value => value, () => null).then(value => {if (current) setCharge({task: task.id, value})})
    return () => {current = false}
  }, [task.id, task.round_id, task.state])
  const read = charge?.task === task.id ? charge : undefined
  const customer = task.customer_preference
  return <dl aria-label="审核依据" className="flex min-w-0 flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
    {customer && <div className="flex gap-1"><dt>客户</dt><dd className="text-foreground">{customerName(catalog?.customers, customer)}</dd></div>}
    <div className="flex gap-1"><dt className="sr-only">审核模式</dt><dd className="text-foreground">{task.deep_mode ? deepModeTitle : '标准模式'}</dd></div>
    <div className="flex gap-1"><dt>规则</dt><dd>受理时冻结的版本，每份文件的规则见文件详情 · <a href={surfaceAddress('requirements')} onClick={openInPlace} className={link}>审核规则库</a></dd></div>
    <div className="flex gap-1"><dt className="sr-only">扣点</dt><dd>{!read ? '正在读取本轮扣点…' : read.value ? <span className="text-foreground">{chargeText(read.value)}</span> : '本轮扣点暂时无法读取'} · <a href={taskUsageAddress(task.number)} onClick={openInPlace} className={link}>查看用量记录</a></dd></div>
  </dl>
}
