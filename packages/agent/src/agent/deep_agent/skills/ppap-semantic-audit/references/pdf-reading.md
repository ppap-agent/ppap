# PDF 资源适配

沿用 `documents.pdf.PdfSpec`、现有预设和提取实现。文件类型、文件名及相似标题只帮助找候选，不能证明版式相同。

## 开发

- 先核对原页的表头、区域、合并关系和跨页续表。PDF 文字顺序不等于表格行序；一行文字也不一定是一个业务对象。
- `template-pdf-spec.py` 提供当前文件的初始配置。把本次使用的配置放入 `resources.py`，再由任务类的 `PDF_SPEC` 引用。关联 PDF 使用 `PDF_SPECS[file_version_id]`。
- 需要调整时，直接修改现有 `GroupingSpec`、`RegionSpec`、`ExtractionSpec`、`StitchSpec` 等字段。接口定义见 `documents.pdf.spec`，具体文件通过 Python 的 `inspect.getfile` 定位。
- 配置修改后重新转换，查看表格和原件。缺列、续页丢失、错误合并或无法读取时继续修订；转换有值不证明结构正确。
- 转换后的工作簿通过原 Fluent API 扫描和断言。关联文件通过 `self.load_related_pdf(file_version_id)` 打开，再用 `ctx.bind_workbook` 绑定该文件身份。

## 来源与能力范围

原件页码、区域、转换诊断由现有文档能力保留；派生工作簿坐标不能冒充 PDF 原件坐标。引用无法确定时保留缺口。页外坐标、重复页、空网格和未解释的图像不能作为已验证事实。

当前自动版式适配针对有文字层的 PDF。没有可用文字网格时，保留 UNABLE。签章、图例及扫描内容需要实际视觉观察；无法获得该观察时，检查应明确报告未解决，不能从邻近文字推定。

正式执行使用最终固定的配置和代码。资源与检查逻辑在开发期间可以反复修订，固定后不得修改同一产物。
