# 从接管到完整产品

本页是入口和依赖导航，实施状态只在 [GitHub Issues](https://github.com/ppap-agent/ppap/issues)。2026-09-18 已读取 #2–#16 的正文并核对无同结果的 open PR；这些是真实 Issues，不是待发布草稿。修改依赖时同步拥有该结果的 Issue，不复制运行日志。

## 开始工作

先完成 [#2](https://github.com/ppap-agent/ppap/issues/2) 的真实隔离运行栈；[#3](https://github.com/ppap-agent/ppap/issues/3) 的样本和 oracle 恢复可并行。不要先扩大 ppap-host，也不要等所有历史系统都研究完才建立第一个可验证结果。

| Issue | 独立可验收结果 | 必要前置 | 主要证明 |
|---|---|---|---|
| [#2](https://github.com/ppap-agent/ppap/issues/2) | 干净环境启动并识别真实 API/Worker/PG/对象存储 | 无 | 安装、停止、重启及版本读回 |
| [#3](https://github.com/ppap-agent/ppap/issues/3) | 样本、人工 oracle、许可和支持范围可重放 | 无 | 同一 corpus 的逐规则/定位比较 |
| [#4](https://github.com/ppap-agent/ppap/issues/4) | 真实登录、租户协作和授权回访 | #2 | S02 |
| [#5](https://github.com/ppap-agent/ppap/issues/5) | 上传、版本固化与历史下载一致 | #2、#4 | S03 |
| [#6](https://github.com/ppap-agent/ppap/issues/6) | 本仓检查器在真实 corpus 上可接受 | #2、#3 | 引擎回归，为 S01 提供执行证明 |
| [#7](https://github.com/ppap-agent/ppap/issues/7) | 草稿明确资料角色、识别与规则版本 | #4、#5、#6 | S01、S03 |
| [#8](https://github.com/ppap-agent/ppap/issues/8) | 一次受理在断线与 Worker 故障后保持身份 | #6、#7 | S04 |
| [#9](https://github.com/ppap-agent/ppap/issues/9) | 统一 Web/PWA 完成审核、回访及证据审阅 | #4、#5、#7、#8 | S01 |
| [#10](https://github.com/ppap-agent/ppap/issues/10) | 复核不改机审，重审保留旧轮次 | #9 | S05 |
| [#11](https://github.com/ppap-agent/ppap/issues/11) | 飞书真实来源和现有入口进入同一产品 | #4、#5、#9 | S06 |
| [#12](https://github.com/ppap-agent/ppap/issues/12) | 使用事件与外部权益可对账、不重复扣量 | #4、#8、#9 | S07 |
| [#13](https://github.com/ppap-agent/ppap/issues/13) | 历史归属、文件、用量和旧链接可迁移 | #4、#5、#8、#12 | S08 |
| [#14](https://github.com/ppap-agent/ppap/issues/14) | 新实例恢复原件、记录与未完成工作 | #8、#12 | S09 |
| [#15](https://github.com/ppap-agent/ppap/issues/15) | 可重建、可升级、可独立贡献的发布物 | #3、#9、#11、#12、#14 | S10 |
| [#16](https://github.com/ppap-agent/ppap/issues/16) | 可回退切换，退出无消费者的 PPAP 遗留路径 | #10、#11、#12、#13、#14、#15 | 全部 Stories + 消费者退出证据 |

这些是验收依赖，不是禁止前端设计、契约讨论或测试先行的排期锁。可以并行制作候选实现；不能在前置结果未成立时宣布集成验收。

## Northstar 覆盖

[INVENTORY](INVENTORY.md) 的 C01→#4；C02→#5；C03/C04→#7；C05→#8；C06→#3/#6；C07/C09→#9；C08→#10；C10→#11；C11→#12；C12→#13；C13→#2/#14；C14/C15→#16；C16→#3/#15。每项证明的用户边界见 [Stories](STORIES.md)。

公开分发权、支持的格式/规则集合、企业成员权限细则、计费配置、保留期和运维目标必须由相应事实或负责人确定。未知项是依赖和交付限制，不是编码者自由补造的政策。它们分别由 #3/#4/#12/#14/#15 收集，不能以“全部票关闭”取代决定。

## 关闭一项工作的条件

提交实现和最近验证；注明源码/环境/输入身份；区分本地测试、真实依赖和用户终态证据。失败或缺凭据不算通过。迁移类 Issue 还需说明旧消费者与数据写入的退出情况。没有完成的子结果保留在原 Issue，不通过增加一层总览来隐藏。

终态验收不是承诺今天即可全自动交付。目标代码、授权样本、隔离部署和真实外部配置仍须实际实现/取得；路线图负责让每个剩余缺口都有归属。
