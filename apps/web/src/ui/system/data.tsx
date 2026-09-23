import type {ReactNode} from 'react'

export function FilterBar({children, onSubmit}: {children: ReactNode; onSubmit: () => void}) {
  return (
    <form data-ui="filter" onSubmit={event => { event.preventDefault(); onSubmit() }}>
      {children}
    </form>
  )
}

export function Unit({children}: {children: ReactNode}) {
  return <span data-ui="unit">{children}</span>
}

export type DataRole = 'title' | 'when' | 'result' | 'meta' | 'action' | 'field'

export type DataColumn<Row> = {
  key: string
  header: string
  role?: DataRole
  testId?: string
  cell: (row: Row) => ReactNode
}

export function DataTable<Row extends {id: string}>({
  rows,
  columns,
  layout = 'ledger',
  label,
  testId,
}: {
  rows: Row[]
  columns: Array<DataColumn<Row>>
  layout?: 'records' | 'ledger'
  label: string
  testId?: string
}) {
  return (
    <div data-ui="table-scroll" data-testid={testId}>
      <table data-ui="data-table" data-layout={layout}>
        <caption data-ui="sr">{label}</caption>
        <thead>
          <tr>
            {columns.map(column => <th key={column.key} scope="col">{column.header}</th>)}
          </tr>
        </thead>
        <tbody>
          {rows.map(row => (
            <tr key={row.id}>
              {columns.map(column => (
                <td
                  key={column.key}
                  data-role={column.role || 'field'}
                  data-label={column.role === 'title' || column.role === 'action' ? undefined : column.header}
                  data-testid={column.testId}
                >
                  {column.cell(row)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
