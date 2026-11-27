# look 命令

在工作目录里运行。文件用 `files.json` 里的 `name`。每个命令末尾会说明「未显示」的部分；没有看到「已全部显示」之前，不要当作看全了。末尾的〔剩余 N 秒〕是你的时限。

## 看一份文件有什么

```sh
look outline 文件
```

表格：每张工作表的行列数、合并区域数、没有缓存值的公式、内嵌图片和图形数量。PDF：页数、每页的表格大小和图片数、哪些是扫描页。Word：段落和表。

## 读表格文件

```sh
look cells 文件 --sheet 工作表名                 # 从头读，一次最多 80 行
look cells 文件 --sheet 工作表名 --range A61:Q140 # 接着读，或只读某个区域
look cells 文件 --sheet 工作表名 --range F1:F300 --rows 300   # 只扫一列，可一次读更多行
```

- 每格写成 `B7=原值`，`⏎` 表示格内换行。空格不列出；整行为空会注明。
- 合并区域的值只写在左上角那一格，末尾列出合并区域。引用合并块里任何一格都读到左上角的值。
- `〔公式无缓存值〕`表示这格的值未知，不是空白。
- 区域里有图片时，末尾列出每张图左上角所在的格。
- 要判断「每一行都…」时，先看表头确定列，再把那几列从正文第一行扫到最后一行。

## 读 PDF

```sh
look page 文件 3                       # 第 3 页：有表格按行列读，没有表格读文字层
look page 文件 3 --table 1 --start 40  # 一页太长时接着读
look page 文件 3 --raw                 # 文字层的原顺序
```

- 有表格的页按 `行号 | 列号=内容` 给出，空格不列出，所以哪一列空着一目了然。表头常占几行，先看表头确定每个列号是什么。
- `〔图 p3-2〕`是放在那一格里的图片（贴成图片的尺寸、符号、签名）；后面带「转写」的是视觉模型读的，关键内容要 `look picture` 取出来自己看。
- 表格线画得不规整时行列可能错位：内容对不上表头时，`look render` 看页面图。
- 扫描页没有文字层：`look page` 会给出已有的整页转写，内容要看图确认。

## 读 Word

```sh
look paragraphs 文件        # 段落
look table 文件 1           # 第 1 张表
```

## 查找

```sh
look find 文件 词 [词 ...]     # 一份文件，一次可查多个词
look find '*' 受控 PGQP 量产   # 本批全部文件
```

查单元格、文字层（PDF 给出表、行、列）、段落、表和图形里的文字，忽略空白和大小写。图片、图章和扫描页里的字查不到，输出会列出查不到的范围。没有命中不等于原件里没有。

## 看图

```sh
look pictures 文件                      # 列出图片：在哪张表哪一格、PDF 在哪页哪格，以及绘制的图形
look picture 文件 p1-3                  # 取出一张图片，再用读取工具打开看
look render 文件 --page 1               # 把一页渲染成图片；表格和 Word 会先排版成页
look render 文件 --page 1 --grid        # 画上 A–J × 1–10 的格子
look render 文件 --page 1 --clip C3:E5 --dpi 220   # 放大那几格
```

命令给出图片路径，用读取工具打开它，你就看到了。看不清先 `--grid` 找到位置，再 `--clip` 放大。表格文件渲染的页是排版后的样子，用来看图形、图章和版面；单元格内容仍以 `look cells` 的原值为准。

## 转写

```sh
look ocr 文件 --page 1                 # 整页转写
look ocr 文件 --page 1 --clip C3:E5    # 只转写局部，更准
look ocr 文件 --picture p1-3
```

视觉模型的转写，可能有错字，编号里的字母和数字尤其容易错。它帮你快速知道一页写了什么、关键内容在哪；同一处转写过一次就会留着，再问不花时间。

## 案卷和记录

```sh
look notes                # 读案卷；look notes 文件、look notes --topic 零件号 可以筛选
look note --topic 客户零件号 --fact "F01R10DP4Z，字段名 零件号Part No." --file 文件 --where 'P0!H4' --quote F01R10DP4Z
look note --topic 受控章 --fact "第 1 页右上有红色受控章" --file 文件 --where page:1 --quote "受控文件" --seen
look submit -             # 从标准输入提交结论；也可以给 JSON 文件的路径
look status               # 条款有没有结论
look uphold PFD:1 "复看了 P0!A6:L25 和全部图片，结论成立"
look remember "这类流程图的图例在表尾备注行，不在表头"
```

`searched` 里声明的范围以这些命令看过的为准：`cells` 读过的区域、`page` 读完的页、不带 `--clip` 的 `render` 和 `ocr`、`paragraphs` 和 `table` 读到的行、`pictures`、`picture`、`find` 查过的每个词。
