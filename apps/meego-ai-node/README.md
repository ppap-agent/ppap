# PPAP 飞书项目 AI 节点

飞书项目流程里的“PPAP 审核”AI 节点。它是 [PPAP 飞书项目插件](../meego-plugin/README.md) 的另一个入口，不是第二套审核。

- 流程到达节点时，飞书项目把节点状态事件推给 `POST /integrations/meego/ai-node/events`。PPAP 验签后只记录节点状态，不导入、不受理、不扣点。
- 有人打开工作项的“PPAP 审核”页签时，插件看到节点在等待、且这个工作项上还没有任何人的审核，就自动准备第一次审核（读取整包资料、识别类型），停在确认这一步；这次准备归打开页签的人。确认受理时才扣点，口径与网页端、插件一致。
- 最近一轮的结论为“符合”并写回工作项后，PPAP 用这个应用自己的凭证把节点的 AI 状态置为完成，再完成节点，流程进入下一步。其他结论下节点继续等待：整改后重新审核整包，或由这一轮的发起人在页签里人工放行（放行人、理由另起评论，结论不改）。每一步先读节点状态，结果不确定时只读回，不重复提交。
- 节点卡片和插件的节点表单卡片是同一份代码（插件工程的 `src/features/ai_node`，复用 `src/features/ppap_node/Card.tsx`）：上方是流程节点的状态，下方是结论、待处理数和是否已写回。卡片用 AI 应用的授权码换取会话，PPAP 按同一个飞书项目用户找到插件里已建立的连接，不需要再连接一次。

## 配置

这是飞书项目里单独的 AI 应用，有自己的 Plugin ID 和 Secret。API 进程需要（见 `.env.example`，值记在仓库根目录 `.env`，按 `deploy/tke/README.md` 同步到 Secret）：

- `PPAP_MEEGO_AI_NODE_PLUGIN_ID`：AI 应用的 Plugin ID。
- `PPAP_MEEGO_AI_NODE_TOKEN`：AI 应用“功能配置”里的 Webhook Token，用于验签。
- `PPAP_MEEGO_AI_NODE_PLUGIN_SECRET`：AI 应用的 Plugin Secret（开发者后台“基本信息”里的明文），用于完成节点和换取卡片会话。

用户的 PPAP 连接、空间映射和租户放行沿用插件的配置。

## 发布

卡片的代码在插件工程里，这里只放 AI 应用自己的配置。`release.sh` 临时换上 AI 应用的配置，在插件工程里构建上传，结束后放回插件自己的配置：

```sh
cp plugin.config.example.json plugin.config.json   # 填入 AI 应用的 pluginId、pluginSecret、卡片 resourceId
./release.sh                                       # 输出产物版本，再在开发者后台“功能配置 → 发布最新配置”选择该产物
```

`plugin.config.json` 含 Secret，已被忽略，不进仓库。
