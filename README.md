# FramePulse Open · 帧脉

**把视频和图片变成可以定位、核查、修订、交换的媒体记录。**

FramePulse 是本地优先的分析引擎、CLI 和导演审核工作台。FlowDesk 或其他资产管理程序负责选文件和安排批次，FramePulse 接收一份素材，输出一份带证据的 `record.fmp.json`。原素材不会被改写。

本项目参考用户提供的 FramePulse 0.7.1 核心拉片流程重新实现，产品重心从非线性剪辑台转为开放标记中间体。当前版本 `0.1.0`，协议 FMP 0.1.0 为实验草案，尚不是行业标准。

## 本地启动

Windows，Python 3.10+：首次双击 `Setup-FramePulse.cmd`；以后双击 **`Start-FramePulse.cmd`**，工作台在 `http://127.0.0.1:47840` 打开。命令行安装与启动：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e '.[dev]'
.\.venv\Scripts\framepulse.exe serve --open
```

Linux/macOS 可使用 `.venv/bin/framepulse serve --open`；Windows 是本次验收平台。默认库在 `runtime/library`，可通过 `serve --data` 更改。

## 一次导入会完成什么

- 原文件 SHA-256、技术信息、真实解码时间戳、自动切镜、每镜多张证据帧。
- 可用的本地视觉模型逐镜生成内容、主体、动作、景别、机位、构图、运镜、场景、灯光、色彩、叙事作用和标签。
- 每镜情绪维度：愉悦度（valence）、激活度（arousal）、叙事张力（tension）。这些是主观模型解释，缺失值保持空缺。
- 图片使用同一协议，包含单个分析单元和区域标注。
- 界面播放定位、证据帧浏览、字段修订、确认/退回、人工画框、CSV/JSON/W3C 导出。
- 每镜保存、失败后继续、跨进程同素材写锁、人工内容和确认状态保护。

每个分析字段包含 `origin`、`confidence`、`evidence_ids`、`run_id`、`review_status`。原始模型信心不是经过校准的准确率。记录测量事实和模型判断时使用不同来源标记。

## AI 配置

没有视觉模型也可完成测量、切镜和关键帧提取。程序只自动发现已经运行的本地 Ollama 中明确声明支持 `vision` 的模型；不会下载模型或启动额外模型服务。界面会显示实际是否启用 AI。

可以连接 OpenAI-compatible 视觉接口或本地 llama.cpp：

```powershell
$env:FRAMEPULSE_PROVIDER_URL = 'http://127.0.0.1:11434/v1'
$env:FRAMEPULSE_MODEL = 'your-installed-vision-model'
# 云端如需密钥，只通过环境传入，不写进记录、仓库或界面：
$env:FRAMEPULSE_API_KEY = 'your-key'
.\framepulse.cmd analyze 'D:\素材\参考视频.mp4' --vision --output 'D:\媒体标记库'
```

勾选 AI 并配置远程地址时，抽取的证据图片将发送到该地址。默认本地配置在本机处理。字幕 OCR 为可选扩展：`pip install -e '.[ocr]'` 后使用 `--ocr`；当前适配中文/英文关键帧文字，不能替代完整字幕转写。日语 OCR、ASR、真正的时序视频/声音理解需要后续 provider。

## CLI 与 FlowDesk 接入

```powershell
.\framepulse.cmd capabilities
.\framepulse.cmd analyze 'D:\素材\参考视频.mp4' --output 'D:\媒体标记库'
.\framepulse.cmd analyze 'D:\素材\海报.png' --no-vision --output 'D:\媒体标记库'
.\framepulse.cmd validate 'D:\媒体标记库\<sha256>\record.fmp.json'
.\framepulse.cmd search 'D:\媒体标记库\<sha256>\record.fmp.json' '逆光'
.\framepulse.cmd export 'D:\媒体标记库\<sha256>\record.fmp.json' --format csv --output shots.csv
```

默认 `analyze` 只会自动使用发现的本地模型；远程 provider 需要 `--vision`。`--no-vision` 保证只运行测量。stdout 输出结果 JSON，stderr 输出逐行 JSON 进度，便于宿主应用调度；一份素材失败不会要求宿主丢弃其他素材结果。详见 [集成契约](docs/INTEGRATION.md) 和 [Node 示例](examples/flowdesk-integration.mjs)。

```text
<output>/<完整 SHA-256>/
  record.fmp.json      权威记录
  frames/             可回看的证据帧
  preview.mp4         必要时生成的浏览代理
```

源文件移动后，对新路径再次调用分析可按哈希复用已有数据。跨机器分享必须带上 `frames` 并提供可访问的源文件。JSON 含本机路径，发布前需替换定位信息。W3C 导出可同时传 `--source-uri` 和 `--evidence-base-uri`。

## 专业与技术边界

自动检测不保证复杂闪白、叠化、遮挡转场或极短镜头全部正确。多张证据帧比单帧覆盖更多内容，但不能等同完整时序理解；不据此声称知道不可见动作、精确焦距、拍摄设备、真实人物心理、对白或音乐。片内连续的动作/情绪变化仍需要更密集分析或人工判断。

当前情绪点以镜头为单位，不是连续心理测量。视频时间采用 `[start_us,end_us)` 整数微秒并保留解码依据；浏览器播放不是专业 NLE 的帧精确监视器。源素材和证据图的映射以记录中的实际 PTS 为准。

本版没有复刻旧 NLE 的剪辑、多轨、配音、ProRes/FCPXML 导出，也未修改现有 FlowDesk。OTIO、IPTC/XMP、音频识别、对象跟踪、向量索引和通用批处理编排在路线图内。

## 开放规范与开发

- [FMP 协议](docs/PROTOCOL.md) / [JSON Schema](src/framepulse/schemas/fmp-0.1.schema.json)
- [标准依据与适配边界](docs/STANDARDS.md)
- [导演字段词表](docs/DIRECTOR-TAXONOMY.md)
- [架构与路线图](docs/ARCHITECTURE.md)
- [验收记录](docs/QA.md)
- [贡献指南](CONTRIBUTING.md) / [发布说明](docs/RELEASING.md)

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m build
```

MIT 许可。源代码和规范可自由复用；素材、模型权重与各自许可证由使用方管理。原交接包和私人分析库不会进入仓库。
