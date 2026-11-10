# LessonTrace 课迹

面向教师的课堂证据复盘原型：已有课堂录像、人物动作观察、教学资料、连续问答及带引用报告。

## 评委轻量部署

GitHub Actions 构建 `ghcr.io/cubevlmu/lessontrace:latest`（linux/amd64）。每次构建先启动2GiB内存限制的容器验证登录、权限、前端、OpenCV、PyAV与FFmpeg，再推送镜像。Actions产物提供离线镜像tar.gz及SHA256；这不等于目标服务器已部署。

后端使用Python 3.11 slim；不包含Torch、Ollama、模型权重、课堂媒体、数据库或API密钥。视频分析结果由独立私有部署包导入，在线文字生成调用DeepSeek。服务器建议4核4GB，实际容量与并发须目标实机验证。

```sh
docker build --target api -f deploy/Dockerfile -t lessontrace:local .
```

空镜像不会自动产生演示课堂。独立部署包中的操作说明负责数据导入、域名/TLS、评委账号、初始化及验收。请勿向本仓库上传该私有数据包或账号文件。

## 证据边界

视频动作不代表心理专注或学习效果。化名与样例作业是演示材料，不能冒充真实学生成绩或独立身份确认。保留模型错误、不确定性和被引用校验过滤的段落。当前不报告未经独立标注验证的识别准确率。

本仓库为部署源码快照，未包含本机开发历史、研究数据、历史聊天和测试素材；第三方依赖使用各自许可证。
