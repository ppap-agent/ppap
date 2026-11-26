# auditor 镜像

[packages/auditor](../../packages/auditor/README.md) 的运行环境，只装它需要的东西：

- Python 读取库（openpyxl、xlrd、PyMuPDF、python-docx、Pillow、httpx），版本取自 `uv.lock`。
- LibreOffice：转换 `.doc`、把表格和 Word 排版成页、把 EMF/WMF 图转成 PNG。基础镜像和系统包与应用镜像相同。
- Pi：与 Deep guest 同一份锁定版本（[deploy/deep/package-lock.json](../deep/package-lock.json)）。

镜像里没有检查程序、产品代码和 API。以 uid 10001 运行。

```bash
docker build -f deploy/auditor/Dockerfile -t ppap-auditor:dev .
```

## 运行时要给它的

| 项 | 给法 |
|---|---|
| Pi 的模型配置 | 把 Pi 的 agent 目录只读挂到容器里，并设 `AUDITOR_PI_SOURCE` 指向它。启动时复制到容器内的临时目录再用（Pi 要在配置旁写锁文件）。 |
| 转写用的视觉模型 | `PPAP_MODEL_VISION_BASE_URL`、`PPAP_MODEL_VISION_API_KEY`、`PPAP_MODEL_VISION_MODEL`、`PPAP_MODEL_VISION_REASONING_EFFORT`，与产品同名同义。不给则 `look ocr` 不可用，审核员直接看图。 |
| 批次目录 | 挂到容器里，作为 `python -m auditor.run` 的参数。 |

这是实验环境：容器直接持有模型凭据并能联网，审核员用 Pi 的 `bash` 运行 `look`。它没有 Deep 的网关、短期凭证和沙箱，不能直接用于客户数据的线上审核。
