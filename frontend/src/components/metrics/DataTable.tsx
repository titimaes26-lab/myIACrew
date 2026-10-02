export interface TableData {
  columns: string[];
  rows: string[][];
}

export default function DataTable({ columns, rows, label }: TableData & { label: string }) {
  return (
    <div className="viz-table-wrap">
      <table className="viz-table" aria-label={label}>
        <thead>
          <tr>{columns.map((column) => <th key={column} scope="col">{column}</th>)}</tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row[0]}>
              {row.map((cell, index) => (index === 0 ? <th key={index} scope="row">{cell}</th> : <td key={index}>{cell}</td>))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
