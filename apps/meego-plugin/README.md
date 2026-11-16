# PPAP 飞书项目插件

飞书项目（Meego）工作项详情里的“PPAP 审核”标签页。在工作项里选择附件，PPAP 识别后由用户确认，再开始审核；审核完成后，可把结论写入工作项评论区。需求与验收见 #324。

## 工作方式

- 插件页面由飞书项目托管，运行在 `https://projectplg.feishupkg.com` 的 iframe 中，只调用 PPAP 后端，不复用 PPAP 网页前端。
- 首次使用时，插件用 `JSSDK.utils.getAuthCode()` 获取授权码，交给 `POST /integrations/meego/session`；PPAP 返回一个连接链接。用户在浏览器里用飞书账号登录 PPAP，并在确认页确认后，飞书项目身份才会绑定到这份飞书授权上。
- 之后每次打开标签页，插件都换取一个 2 小时有效的宿主会话令牌，并通过 `Authorization: Bearer` 调用 PPAP 接口。令牌只保存在内存里，网页的 Cookie 会话不能当作令牌使用。
- 附件由 PPAP 后端直接从飞书项目下载，不经过插件页面上传。写回时先查评论里是否已有本轮的标记；结果不确定时只读回，不会重复写入。

## 后端配置

API 进程需要以下配置（见 `.env.example`）：

- `PPAP_MEEGO_PLUGIN_ID`、`PPAP_MEEGO_PLUGIN_SECRET`：插件凭证。Secret 只注入 API。
- `PPAP_MEEGO_TENANT_MAP`：飞书项目 `saas_tenant_key` 到同一企业飞书 `tenant_key` 的显式映射。没有映射的空间一律拒绝。
- 该飞书租户还必须在 `PPAP_CURRENT_TENANT_ALLOWLIST` 中。

插件需要在开放平台“权限管理”中申请读取工作项、读取附件、读写评论和读取用户的权限，并在空间中安装。

## 开发与发布

```sh
cp plugin.config.example.json plugin.config.json   # 填入插件 ID、Secret 和详情页 resourceId；该文件不入库
npx -p @lark-project/cli@2.8.6 lpm start          # 本地调试，需要信任 CLI 生成的本地根证书，并允许浏览器访问本地网络
npx -p @lark-project/cli@2.8.6 lpm release --source-type remote   # 构建并上传产物，输出产物版本
```

产物上传后，在开放平台“插件发布”中创建版本：使用范围选“指定空间”，不发布到插件商店。PPAP 后端地址在 `src/features/ppap_tab/config.ts`。
