# Web Client

浏览器/PWA 与飞书 H5 使用同一个 PPAP 产品客户端。isolated-dev 通过 `python tools/stack.py up` 启动本地 Web。

## Owns

- 产品导航和呈现
- 文件/资料集准备
- 审核提交和结果回访
- 原文定位与下一步
- 人工复核
- 用量/历史视图
- 浏览器与飞书宿主的小型平台适配

## Boundary

Web 只调用 Product API。它不能：

- 调 Worker/旧 engine tasks API
- 直接访问 PostgreSQL/PostgREST
- 持有对象存储管理凭据
- 建立飞书专属业务台账
- 嵌入服务端 Feishu/Partner AI Secret

优先复用同一产品 route/state；只有真实宿主差异才增加平台适配。
