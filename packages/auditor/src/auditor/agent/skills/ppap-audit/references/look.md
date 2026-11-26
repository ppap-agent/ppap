# look 命令

在工作目录里运行。文件用 `files.json` 里的 `name`。每个命令末尾会说明「未显示」的部分；没有看到「已全部显示」之前，不要当作看全了。

## 看一份文件有什么

```sh
look outline 文件
```

表格：每张工作表的行列数、合并区域数、没有缓存值的公式、内嵌图片和图形数量。PDF：页数、哪些页有文字层、哪些是扫描页。Word：段落和表。

## 读表格

```sh
look cells 文件 --sheet 工作表名                 # 从头读，一次最多 80 行
look cells 文件 --sheet 工作表名 --range A61:Q140 # 接着读，或只读某个区域
look cells 文件 --sheet 工作表名 --range F1:F300 --rows 300   # 只扫一列，可一次读更多行
```

- 每格写成 `B7=原值`，`⏎` 表示格内换行。空格不列出；整行为空会注明。
- 合并区域的值只写在左上角那一格，末尾列出合并区域。引用合并块里任何一格都读到左上角的值。
- `〔公式无缓存值〕`表示这格的值未知，不是空白。
- 要判断「每一行都…」时，先看表头确定列，再把那几列从正文第一行扫到最后一行。

## 查找

```sh
look find 文件 文字      # 一份文件
look find '*' 文字       # 本批全部文件
```

查单元格、文字层、段落、表和图形里的文字，忽略空白和大小写。图片、图章和扫描页里的字查不到，输出会列出没查到的范围。没有命中不等于原件里没有。

## PDF 和 Word

```sh
look page 文件 3            # 第 3 页的文字层
look paragraphs 文件        # Word 段落
look table 文件 1           # Word 第 1 张表
```

文字层的顺序不等于表格的行序。行列关系要对照页面图。

## 看图

```sh
look pictures 文件                      # 列出内嵌图片（含所在工作表和单元格）和绘制的图形
look picture 文件 xl/media/image2.emf   # 取出一张图片，再用读取工具查看
look render 文件 --page 1               # 把一页渲染成图片；表格和 Word 会先排版成页
look render 文件 --page 1 --grid        # 画上 A–J × 1–10 的格子
look render 文件 --page 1 --clip C3:E5 --dpi 220   # 放大那几格
```

命令会给出图片路径，用读取工具打开它。看不清就先 `--grid` 找到位置，再 `--clip` 放大。表格渲染的页是排版后的样子，用来看图形、图章和版面；单元格内容仍以 `look cells` 的原值为准。

## 转写

```sh
look ocr 文件 --page 1                 # 整页转写
look ocr 文件 --page 1 --clip C3:E5    # 只转写局部，更准
look ocr 文件 --picture xl/media/image1.png
```

这是视觉模型的转写，可能有错字，编号里的字母和数字尤其容易错。它帮你快速知道一页写了什么、关键内容在哪；关键内容要放大后自己看图确认。

## 记录

```sh
look submit 结论.json     # 提交一条或一组结论；被退回会说明原因
look status               # 哪些条款还没有结论
look note --topic 客户零件号 --fact "F01R10DP4Z，字段名 零件号Part No." --file 文件 --where 'P0!H4' --quote F01R10DP4Z
look remember "这类流程图的图例在表尾备注行，不在表头"
look uphold PFD:1 "复看了 P0!A6:L25 和全部图片，结论成立"
```

`searched` 里声明的范围以这些命令看过的为准：`cells` 读过的区域、`page` 读完的页、不带 `--clip` 的 `render` 和 `ocr`、`paragraphs` 和 `table` 读到的行、`pictures`、`picture`、`find`。
