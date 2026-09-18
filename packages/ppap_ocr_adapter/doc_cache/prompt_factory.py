# src/prompt_factory.py

from collections.abc import Mapping


class PromptFactory:
    """
    负责根据 Tags 和 Context 组装 Gemma 3 的 Prompt
    """

    @staticmethod
    def get_tagging_prompt() -> str:
        """
        生成用于 Tagging (打标) 的 Prompt
        """
        return """
你是一个严格的 JSON 数据提取器。
请分析图片并根据 OCR 线索返回 JSON。

**【严格约束】**
1. **必须** 输出纯 JSON 文本，**严禁** 使用 markdown 代码块（不要写 ```json）。
2. **严禁** 添加 Schema 定义之外的任何字段。
3. 只能返回以下 4 个字段：
   - "has_checkboxes"
   - "two_column_split"
   - "has_handwriting"
   - "has_merged_row_number"

**【字段逻辑定义】**
- **has_checkboxes**: (Boolean) 图片中是否出现方框(□)、打钩框(☑)？或者 OCR 文本中包含 "Yes/No" 选项？。
- **two_column_split**: (Boolean) 页面布局是否明显分为左右两栏（如：左边是供应商信息，右边是客户信息）？如果是普通的通栏表格，请填 false。
- **has_handwriting**: (Boolean) 是否有手写签名或手写日期？
- **has_merged_row_number**: (Boolean) **仅当**你看到一个表格，且该表格的第一列是序号（如 1,2,3），
  **并且**出现了“左侧序号单元格合并，右侧对应多行内容”的情况时，才设为 true。如果只是普通表格，必须设为 false。


请严格按照 JSON Schema 返回结果。
"""

    @staticmethod
    def get_extraction_prompt(
        tags: set[str],
        tag_hints: Mapping[str, str] | None,
        tagging_desc: Mapping[str, str | Mapping[str, str]],
        merged_ocr_context: str,
        tag: Mapping[str, str] | None = None,
    ) -> str:
        tag_hints = tag_hints or tag or {}

        # 1. 动态生成当前 Tags 的语义说明列表
        active_tag_descriptions: list[str] = []
        for active_tag in tags:
            raw_desc = tagging_desc.get(active_tag, "未定义特征")
            desc = raw_desc.get("description", "未定义特征") if isinstance(raw_desc, Mapping) else raw_desc
            active_tag_descriptions.append(f"- [{active_tag}]: {desc}")
        tags_context = "\n".join(active_tag_descriptions)

        """
        生成用于最终内容提取的 Prompt
        """
        # 1. 基础指令
        base_prompt = f"""
你是一个通用的【视觉文档转录专家】。
你的核心任务是：**直接解码【原始图片】中的视觉信息，将其翻译为语义化的 HTML 代码**。
请忽略参考文本中的排版错误，严谨地以图片为准。

【🚫 负向约束 (Negative Constraints) - 最高优先级】
1. **忽略所有图片/图表**：如果是 `IMAGE`、`FIGURE` 类型区域，**直接跳过，输出空字符串**。
2. **禁止文本流输出**：在处理表格区域时，**严禁**输出纯文本。所有内容必须被包裹在 `<td>` 中。
3. **禁止 `rowspan`**：为了数据处理方便，在序号列和关键信息列，**禁止使用 `rowspan` 跨行**。必须将合并的单元格拆解，并将内容复制到每一行 (Fill-Down)。

【输入信号】
- 包含的元素特征 (Tags)：{tags_context}

【思维链路与执行步骤 (Chain of Thought)】
请严格按照以下步骤进行视觉扫描和代码生成：

1. **👀 第一步：整体布局切分 (Global Layout Segmentation)**
   - **观察**：首先鸟瞰整张图片，识别出页面中独立的“逻辑块”。
   - **判断逻辑**：
     - **横向分割**：表单通常是自上而下排列的。
     - **边界识别**：有边框包围的区域是一个独立块；没有边框但有明显“小标题”的，小标题及其下方内容为一个新块。
     - **Paddle参考**：利用 layout_hints 中的坐标信息，确认物理区块的边界。

2. **✂️ 第二步：块间隔离 (Block Isolation)**
   - **强指令**：在处理完一个布局块（例如：表头区）并准备处理下一个布局块（例如：主体表格区）之前，**必须**插入一个显式的分割线。
   - **代码格式**：使用 `<hr class="block-separator" data-block-type="next-section">` 来物理隔开不同区域。这对于后续解析至关重要。

3. **📝 第三步：细节解码 (Detail Decoding)**
   - 进入每个布局块内部，根据 `{"HAS_HANDWRITING"}` 激活对应的专项处理模块（如复选框、分栏处理）。
   - 严格还原文字、数字和表格结构。

"""

        # 2. 动态模块：复选框纠错
        merged_row_number_module = ""
        if "HAS_MERGED_ROW_NUMBER" in tags:
            merged_row_number_module = """
🚀 **【专项模块激活】：Tag [HAS_MERGED_ROW_NUMBER] - 强制拆解**
【🛠️ 表格结构修复专项指令 (Table Repair Protocol)】
**你必须启动“网格对齐模式”，严格执行以下修复逻辑：**

**1. 针对“序号合并导致列错位”的修复 (Fix Merged Index)**
> **痛点**：OCR 常把左侧跨行的序号（如 "11"）识别为合并单元格，导致第二行缺失序号，整行数据向左偏移（原本的第二列变成了第一列）。
> **视觉铁律**：**表格的第一列永远是序号（数字）**。
> **执行动作**：
  - 检查每一行的第一个单元格。
  - **如果**它是内容描述（如“靠背通风”）而不是数字：
    - **判定**：这是列错位！说明上一行的序号（如 "11"）跨行了。
    - **修复**：**强制在行首插入一个新的 `<td>`**，填入上一行的序号（"11"）。
  - **禁止**：**绝对禁止使用 `rowspan`**！必须把跨行的序号拆解，在每一行都显式地写出来（Fill-Down）。

**2. 针对“行/列遗漏”的修复 (Fix Missing Data)**
> **痛点**：OCR 可能会漏掉某些看起来像直线的行，或者漏掉空的单元格。
> **执行动作**：
  - **列数锁定**：先数清楚表头有几列（假设是 N 列）。接下来的**每一行必须且只能有 N 个 `<td>`**。
  - 如果某一行只有 N-1 个格子，请立即看图，找出漏了哪一列（通常是空的判定列或序号列），并补上 `<td></td>` 或对应的文字。

**3. 针对“结构窜行”的修复 (Fix Row Shifting)**
> **痛点**：右侧的数据（如“尺寸”）可能被 OCR 错误地识别到了上一行或下一行。
> **执行动作**：
  - **水平对齐检查**：依靠图片中的**横向分割线**。
  - 凡是在同一水平高度的文字，必须放在同一个 `<tr>` 里。
  - 如果 OCR 草稿把不同高度的文字放在了一起，请**强制拆分**为两个 `<tr>`。

---
**正确修复示例 (Correct Example)**：
*OCR 草稿 (错误)*：
```html
<tr><td rowspan="2">11</td><td>座椅重量</td>...</tr>
<tr><td>靠背通风</td><td>OK</td>...</tr> <!-- 错误：这里少了一列，"靠背通风"变成了第1列 -->
```

*你的输出 (修复后)*：
```html
<tr>
  <td>11</td> <!-- 序号 -->
  <td>座椅重量</td>
  ...
</tr>
<tr>
  <td>11</td> <!-- 修复动作：主动补全序号，纠正列错位 -->
  <td>靠背通风</td> <!-- 现在它是第2列了，位置正确 -->
  ...
</tr>
```
"""

        checkbox_module = ""
        if "HAS_CHECKBOX" in tags:
            checkbox_module = """
🚀 **【专项模块激活】：由于检测到 Tag [HAS_CHECKBOX]**
**目标对象**：全页面的交互式符号。
> **核心指令**：本页面包含关键的交互式表单。你必须启动 **[像素级视觉检测]** 模式。
> **铁律**：HTML 输出中**绝不允许**出现独立的汉字 “回”、“口” 或字符 “0”/“O” 作为选项前缀！必须转换为 `<input>` 标签。

**1. 🕵️‍♂️ 视觉状态判定字典 (Visual State Dictionary)**
请盯着图片中的方框区域，匹配以下视觉特征：
| 视觉特征 (Image) | 含义 | HTML 代码 (必须精确) |
| :--- | :--- | :--- |
| **空方框** (白色内部) | 未选中 | `<input type="checkbox">` |
| **打钩 (√)** | **已选中** | `<input type="checkbox" checked>` |
| **打叉 (×)** | **已选中** | `<input type="checkbox" checked>` |
| **实心黑块 (■)** | **已选中** | `<input type="checkbox" checked>` |
| **框内有圆点/阴影** | **已选中** | `<input type="checkbox" checked>` |

**2. 📝 OCR 幻觉强制映射 (OCR Artifact Mapping)**
OCR 辅助文本极易出错，若在选项前看到以下“怪字符”，**无视其字面义，强制按以下规则转译**：
- 看到 **“回”** 字 $\rightarrow$ 判定为 **[已选中]** $\rightarrow$ 输出 `<input type="checkbox" checked>`
- 看到 **“口” / “0” / “O”** $\rightarrow$ 判定为 **[未选中]** $\rightarrow$ 输出 `<input type="checkbox">`
- 看到 **“☑”** (Unicode符) $\rightarrow$ 判定为 **[已选中]** $\rightarrow$ 输出 `<input type="checkbox" checked>`

**3. 🧠 逻辑补全法则 (Logical Completion)**
> **场景**：图片模糊导致框线看不清，但语义上必须有框。
- **Yes/No 必带框**：只要看到 text 包含 “是/Yes”、“否/No”、“满足/不满足”，**必须**无中生有在文字前补全 `<input>`。
- **排版对齐**：如果上一行有框，这一行位置对应的地方模糊不清，**必须**根据对齐原则补一个框。

**4. 🚫 负向约束 (Negative Constraints)**
- **严禁遗漏**：宁可错杀（多补框），不可放过（漏框）。
- **严禁幻觉**：不要自己发明 Checked 属性，只有**视觉上确实有墨迹**或**OCR出现“回”字**时才加 `checked`。

**示例输入(OCR)**: "回 尺寸测量  口 外观标准"
**正确输出(HTML)**: `<label><input type="checkbox" checked> 尺寸测量</label> <label><input type="checkbox"> 外观标准</label>`

请务必仔细观察图片中的每一个方框!!!! 这非常关键!!!! 错漏一个都不行!!!!
"""

        # 3. 动态模块：跨页表格
        table_module = ""
        if "CROSS_PAGE_TABLE" in tags:
            table_module = """
【专项：CROSS_PAGE_TABLE - 跨页表格 (Cross-page Table)】
**注意**：此页面是表格的续页或起始页。
1. 请保持表格结构的连贯性。
2. **重点检查序号列**：如果图片左侧序号（如 11, 12）是连贯的，请确保 HTML 中体现。
3. 不要随意闭合 `</table>`，除非页面底部明确结束。
"""

        # 4. 动态模块：左右分栏/复杂布局
        layout_module = ""
        #         if "TWO_COLUMN" in tags or "TWO_COLUMN" in tags:
        #             layout_module = """
        # 🔥 **模式：复杂分栏与视觉解耦 (Visual Decoupling)**
        # > **场景诊断**：图片中存在明显的竖向分割线（Visual Separator），将页面分为左右两部分。
        # > **OCR 陷阱**：辅助 OCR 文本极有可能无视了这条线，错误地将左边的文字（如 "供方制造..."）和右边的文字（如 "提交顾客..."）拼接在了一起。

        # **核心指令：执行“视觉解耦”重组**

        # 1.  **🔍 视觉定位 (Visual Anchor)**：
        #     - 找到那条垂直的分割线（或空白带）。
        #     - 以此线为界，建立两个独立的逻辑容器：`[Left_Container]` 和 `[Right_Container]`。

        # 2.  **🧠 文本清洗与归位 (Text Untangling)**：
        #     - 当你看到一行 OCR 文本时，请看图片判断：
        #       - 这段文字在分割线左边吗？ -> 放入 `[Left_Container]`。
        #       - 这段文字在分割线右边吗？ -> 放入 `[Right_Container]`。
        #     - **特例处理**：如果 OCR 给了一行长文本 "A B"（A在左，B在右），你必须将其**打断**，A 归左，B 归右。

        # 3.  **📝 纵向序列化输出 (Linearization Output)**：
        #     - 按照以下顺序生成 HTML，严禁左右混排：

        #     ```html
        #     <!-- ==================== -->
        #     <!-- 🟢 第一步：输出左栏 -->
        #     <!-- ==================== -->
        #     <div class="section-left">
        #        <h3>(左栏标题，如：供方信息)</h3>
        #        <!-- 在这里把左栏所有内容全部写完，一行都不要剩 -->
        #        ...
        #     </div>

        #     <!-- ==================== -->
        #     <!-- 🔵 第二步：输出右栏 -->
        #     <!-- ==================== -->
        #     <div class="section-right">
        #        <h3>(右栏标题，如：顾客信息)</h3>
        #        <!-- 在这里开始写右栏内容 -->
        #        ...
        #     </div>
        #     ```

        # **再次警告**：
        # - 不要因为 OCR 给了你一行连着的字，你就照抄。**看图！看分割线！**
        # - 左右两栏在语义上通常是独立的实体（例如：一方是供应商，一方是客户），不要让它们的信息混杂。
        # """

        # 5. 动态模块：印章
        seal_module = ""
        #         if "HAS_SEAL" in tags:
        #             seal_text = tag_hints.get("seal_text", "未知印章")
        #             seal_module = f"""
        # 【专项：印章 (Seal)】
        # 图片中包含印章。OCR 识别结果为："{seal_text}"。
        # 请将此信息包含在 HTML 的合适位置（如 `<div class="seal">...</div>`）。
        # """

        ocr_context = f"""
【Paddle OCR 辅助输入 (Flawed Context)】
**注意**：以下是 PaddleOCR 识别的**草稿内容**。它包含正确的文字信息，但**结构可能会存在严重缺陷**（如漏行、错列、错误的合并单元格）。
**请务必以原始图片为准，对以下内容进行【结构重组与修复】：**
{merged_ocr_context}
"""

        # 组装
        final_prompt = f"{base_prompt}\n{layout_module}\n{checkbox_module}\n{merged_row_number_module}\n{table_module}\n{seal_module}\n{ocr_context}"

        print("=== Generated Prompt ===\n\n", final_prompt)
        return final_prompt
