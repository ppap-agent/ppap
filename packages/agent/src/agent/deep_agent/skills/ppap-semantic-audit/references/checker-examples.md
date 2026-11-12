# 教学反例

这些片段只说明写法。它们不是验收样本，也不是参考答案。不要把其中的工作表名或句子抄成对本次文件的判断。

## 可用写法

空范围返回 `UNABLE`，并保留依据。已执行断言且没有 NG 时才返回 `PASS`。

```python
checked = 0
for row in section.rows_from(columns):
    if row.is_present("工序名称"):
        row.expect("操作描述", context="工序缺少操作描述")
        checked += 1
if checked == 0:
    return "UNABLE", "未确定本次规则要求的工序范围"
return "PASS", "已检查本次要求的工序"
```

## 不能交付的写法

- 没有断言却返回 `PASS`，包括只写一句建议。
- 已经收集到 NG，方法末尾仍返回 `PASS`。
- `check()` 返回 `None`。条款身份会丢失。
- 多个条款共用一个没有 `clause_id` 的入口。
- 按规则 ID 保存程序，再直接用于另一个文件。
- 把宿主临时路径写入程序。
- 范围不完整或仍有未解问题时，对“全部”要求返回 `PASS`。
- 没有见证却对“存在一个”返回 `PASS`，或把一个见证写成全量扫描。
- 搜索未完成却写成“确认不存在”。
