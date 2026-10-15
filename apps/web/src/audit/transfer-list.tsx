// Stitch: projects/13261964020862199279/screens/6f00d96e12bc4089adcb51e77855af0d
import {Badge} from '../components/ui/badge'
import {Button} from '../components/ui/button'
import {Table, TableBody, TableCell, TableHead, TableHeader, TableRow} from '../components/ui/table'
import {FileName} from '../journey/file-name'
import {canRetryUpload, uploadName, type UploadRow} from './uploads'

export function TransferList({rows, onRead, onRetry, onRemove}: {rows: UploadRow[]; onRead: (id: string) => void; onRetry: (id: string) => void; onRemove: (id: string) => void}) {
  const unresolved = rows.filter(row => !row.removed && row.receipt.state !== 'ready')
  const removed = rows.filter(row => row.removed && row.receipt.state !== 'ready')
  const table = (items: UploadRow[]) => <Table className="table-fixed"><TableHeader><TableRow><TableHead>文件</TableHead><TableHead>传输情况</TableHead><TableHead className="w-36">操作</TableHead></TableRow></TableHeader><TableBody>{items.map(row => <TableRow key={row.id}><TableCell className="whitespace-normal"><FileName name={uploadName(row.input)}/></TableCell><TableCell className="whitespace-normal"><Badge variant="outline">{row.removed ? '已排除' : {uploading: '传输中', ready: '已接收', failed: '明确失败', unknown: '结果未知'}[row.receipt.state]}</Badge><p className="mt-2 text-xs text-muted-foreground">{row.receipt.reason || '等待接收回执，尚未进行格式预检查。'}</p></TableCell><TableCell><div className="flex flex-wrap gap-2">{['unknown', 'uploading'].includes(row.receipt.state) && <Button size="sm" variant="outline" disabled={row.busy} aria-label={`读取传输 ${uploadName(row.input)}`} onClick={() => onRead(row.id)}>{row.busy ? '传输处理中…' : '读取结果'}</Button>}{!row.removed && <><Button size="sm" variant="outline" disabled={!canRetryUpload(row)} aria-label={`重试传输 ${uploadName(row.input)}`} onClick={() => onRetry(row.id)}>重试此文件</Button><Button size="sm" variant="ghost" aria-label={`排除传输 ${uploadName(row.input)}`} onClick={() => onRemove(row.id)}>排除本次</Button></>}</div></TableCell></TableRow>)}</TableBody></Table>
  return <>{unresolved.length > 0 && <section className="space-y-3"><h2 className="font-semibold">待处理传输 · {unresolved.length} 份</h2><p className="text-sm text-muted-foreground">已接收文件在下方预检查。单份失败不影响其他文件；结果未知时只读取原请求。</p>{table(unresolved)}</section>}{removed.length > 0 && <section className="space-y-3"><h2 className="font-semibold">已排除传输 · {removed.length} 份</h2><p className="text-xs text-muted-foreground">排除只是不纳入本次识别，不代表远端文件已删除或请求已取消。未知请求仍可读取。</p>{table(removed)}</section>}</>
}
