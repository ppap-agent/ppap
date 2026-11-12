# 复核时读取原件

用宿主核对证据时相同的读取方式，摘录才能核对通过。命令中的 `python` 已包含审核所用的库。

## Excel（.xlsx / .xlsm / .xls）

```sh
python - <<'PY'
from documents.excel import load_excel_workbook
wb = load_excel_workbook("files/00-示例.xls")   # .xls 也用它读取；得到的是缓存值
for ws in wb.worksheets:
    print(ws.title, ws.max_row, ws.max_column, [str(r) for r in ws.merged_cells.ranges][:20])
ws = wb["工作表名"]
for row in ws.iter_rows(min_row=1, max_row=15, max_col=12):
    print([f"{c.coordinate}={c.value!r}" for c in row if c.value is not None])
PY
```

- 合并单元格只在左上角有值；引用合并区域内的格子时，宿主按左上角取值。
- 公式按缓存值比对；没有缓存值就是未知，不能当成空白或已知值。
- 大表先看表头和行数，再按需要的列扫描全部正文行，不要只看前几行就下结论。

也可以用 `references/commands.md` 中的 `audit.document_tools view` 分页查看。

## PDF

```sh
python - <<'PY'
import fitz
doc = fitz.open("files/01-示例.pdf")
for page in doc:
    text = page.get_text()
    print(page.number + 1, len(text.strip()))      # 0 表示没有文字层（扫描页）
print(doc[2].get_text())                            # 第 3 页的文字层，摘录从这里逐字截取
doc[2].get_pixmap(dpi=110).save("page-3.png")       # 需要看签章、图样时渲染后再查看图片
PY
```

- 文字顺序不等于表格行序；跨行内容要结合页面图像确认对应关系。
- 没有文字层的页是扫描页：相关条款保持 UNABLE，不要靠看图订正。

## Word（.docx）

```sh
python - <<'PY'
import docx
d = docx.Document("files/02-示例.docx")
for i, p in enumerate(d.paragraphs, 1):
    if p.text.strip():
        print(i, p.text)
for t, table in enumerate(d.tables, 1):
    for r, row in enumerate(table.rows, 1):
        print(t, r, [cell.text for cell in row.cells])
PY
```

## 图片（.png / .jpg / .jpeg / .webp）

直接用读取工具查看图片文件。证据 `kind` 为 `visual`，摘录写你看到的内容（例如签章文字、签名位置）。看不清的内容保持未知。
