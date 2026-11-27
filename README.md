# 课迹 LessonTrace

课迹是供教师和教学督导使用的课堂录像复盘工具。选择一段录像、一个时间范围或一条人物轨迹后，可以结合教案和作业提问，从回答里的引用回看画面、查阅原文，再把核实过的内容写入报告。

例如，教师选中某位学生在录像第 8 至 14 秒的片段，问“能说明他没听懂吗？”课堂助手会说明仅凭这段画面无法判断理解程度，并建议请学生用自己的话解释刚才的一步，再对照实际作答。教师可以在回答旁打开原片，继续核查。

## 在本机启动

在 Linux 上准备 Bash、Python 3.12 或 uv，以及 Node.js 和 pnpm。首次安装依赖和构建前端需要联网。在仓库根目录运行

```bash
bash start_product.sh
```

启动器会初始化本地数据目录，构建缺失的前端，并启动 API 和分析队列。首次启动时按终端提示设置管理员密码，随后打开 [本机课堂工作台](http://127.0.0.1:8787/app)。已有数据目录使用原账户。按 Ctrl+C 停止服务。

| 用途 | 命令或配置 |
| --- | --- |
| 在无桌面环境运行或更换端口 | `bash start_product.sh --no-browser --port 8788` |
| 修改前端后重新构建 | `bash start_product.sh --build` |
| 使用已有 Python 环境 | 安装 `requirements/api.txt`，将 `INTICLASS_APP_PYTHON` 指向该环境的 Python |
| 指定持久化目录 | 设置 `INTICLASS_DATA_DIR`，默认使用仓库根目录下的 `lesson_trace_data/` |

回看已保存的录像和阅读现有报告可以离线进行。分析新录像需要 FFmpeg、人物检测权重、跟踪环境和已配置的视觉模型服务。跟踪环境可使用 `requirements/cpu.txt` 安装，并通过 `INTICLASS_TRACKING_PYTHON` 指定解释器。需要生成模型分析和建议时，可在产品的模型设置页配置文字模型。

## 完成一次复盘

1. **选择课次和资料。** 在课堂中导入录像，关联教案、作业或教师记录，选择要分析的人物与时间范围。
2. **核查回答。** 课堂助手显示已完成观察和失败的分析窗口数。点击视频引用可在当前对话打开回放，点击资料引用可查看原文。人物框跟随实际检测记录，轨迹中断处留空。
3. **整理报告。** 将需要的回答加入报告草稿，编辑并保存版本。打印和 Markdown 导出会列出录像文件及文件内时间，或资料标题及页码，方便另一位教师复核。

## 部署与源码

后端使用 FastAPI 和 SQLite，前端使用 React、TypeScript 和 Vite。课堂录像、账户、证据帧和模型存放在部署实例的数据目录中。

| 位置 | 内容 |
| --- | --- |
| `src/agent/fusion/` | 问题范围解析、证据检索、回答、覆盖统计与回放解析 |
| `src/context/` | 课次和资料管理、权限检查、报告版本与导出 |
| `src/tracking/`、`src/behavior_discovery/` | 人物轨迹、采样窗口和行为观察 |
| `frontend/src/` | 课堂工作台、助手对话、证据预览和报告编辑 |
| `deploy/`、`.github/workflows/` | 容器、网关和镜像构建检查 |

从仓库根目录可构建 Linux API 镜像

```bash
docker build --target api -f deploy/Dockerfile -t lessontrace:local .
```

GitHub Actions 在 Linux AMD64 容器中检查登录、前端、媒体解码和网关，再发布镜像。构建步骤和运行配置见 [镜像工作流](.github/workflows/container.yml) 与 [部署模板](deploy/compose.release.yaml)。
