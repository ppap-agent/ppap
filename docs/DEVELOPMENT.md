# 开发

普通工作只需要当前 Issue、相关源码和最近测试。目标是尽快完成迁移和开发，让客户尽早使用。

## 可直接使用的资产

`ppap-agent` GitHub 组织中的仓库内容、配置、文档、测试数据，以及我们可以访问的远程环境，默认已经授权用于本项目开发、迁移、测试和交付，除非产品负责人以后明确说某项不能用。

开发时可以直接：
- 读取和迁移其它组织仓库中的现有实现；
- 复用已有配置、脚本、fixture、oracle 和测试；
- 使用我们可访问的远程测试/运行环境验证真实路径；
- 优先复制/迁入已经工作的内聚模块，而不是重新实现。

不要为“这些资产能不能用”增加默认审批或证据流程。

## 配置

应用配置的 executable authority 是 typed Settings 和 [`.env.example`](../.env.example)。

- Secret 只通过本地 gitignored `.env`、GitHub Secrets 或部署 Secret 注入。
- 可选 capability 缺配置只影响该 capability，不阻止 Core 启动和无关测试。
- 已有组织内配置可以直接用于迁移/开发；迁入终态配置后由本仓 Settings 管理。
- 新增配置项前先确认它代表独立运行决策，而不是可推导的内部细节。

## isolated-dev

```sh
python tools/stack.py install
python tools/stack.py up
python tools/stack.py verify
python tools/stack.py seed-auth
python tools/stack.py verify-auth
python tools/stack.py verify-uploads
python tools/stack.py down
```

## 测试

```sh
python -m pip install -r requirements.txt
PYTHONPATH=packages:services/api:services/worker python -m unittest discover -s tests/unit -v
```

#3 完成后，corpus pytest 直接从本仓运行。

## 迁移工作

来源仓和远程环境是可直接利用的迁移资产。不要只因为“来源是旧系统”就重新实现。

选择迁移粒度时以**最快形成稳定客户路径且降低终态上下文**为准：
- 小修改能完成就小修改；
- 一个 package/能力域整体迁入更快时就整体迁入；
- 迁入后立即把 tests/config 归本仓，删除跨仓长期依赖和无消费者代码。

## 最短交付路径

当前 Issue → 复用/迁入现成资产 → 跑本仓测试 → 修真实失败 → 必要的真实环境验证 → 当前 PR → 尽快打通客户路径。

实施状态只更新 Issue/PR，不另建运行台账或 proof registry。
