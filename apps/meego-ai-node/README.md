# PPAP 飞书项目 AI 节点

飞书项目流程里的“PPAP 审核”AI 节点。它是 [PPAP 飞书项目插件](../meego-plugin/README.md) 的另一个入口，不是第二套审核。需求与验收见 #327。

- 流程到达节点时，飞书项目把节点状态事件推给 `POST /integrations/meego/ai-node/events`。PPAP 验签后只记录节点状态，不导入、不受理、不扣点。
- 有人打开工作项的“PPAP 审核”页签时，插件看到节点在等待，就自动准备第一次审核（读取整包资料、识别类型），停在确认这一步。确认受理时才扣点，口径与网页端、插件一致。
- 最近一轮的结论为“符合”并写回工作项后，PPAP 用这个应用自己的凭证把节点的 AI 状态置为完成，再完成节点，流程进入下一步。其他结论下节点继续等待，整改后重新审核整包。每一步先读节点状态，结果不确定时只读回，不重复提交。
- 这里的节点卡片只说明流程走到哪、下一步做什么；结论、问题和确认都在“PPAP 审核”页签。

## 配置

这是飞书项目里单独的 AI 应用，有自己的 Plugin ID 和 Secret。API 进程需要（见 `.env.example`）：

- `PPAP_MEEGO_AI_NODE_PLUGIN_ID`：AI 应用的 Plugin ID。
- `PPAP_MEEGO_AI_NODE_TOKEN`：AI 应用“功能配置”里的 Webhook Token，用于验签。
- `PPAP_MEEGO_AI_NODE_PLUGIN_SECRET`：AI 应用的 Plugin Secret（开发者后台“基本信息”里的明文），用于完成节点。

用户的 PPAP 连接、空间映射和租户放行沿用插件的配置，不需要再连接一次。

## 开发与发布

```sh
cp plugin.config.example.json plugin.config.json   # 填入 AI 应用的 pluginId、pluginSecret、卡片 resourceId
npx -p @lark-project/cli@2.8.6 lpm start                          # 本地调试
npx -p @lark-project/cli@2.8.6 lpm release --source-type remote    # 构建并上传产物，再在开发者后台“功能配置 → 上架”选择该产物
```

`plugin.config.json` 含 Secret，已被忽略，不进仓库。
