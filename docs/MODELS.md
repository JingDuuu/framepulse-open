# 本地模型选择与本机配置

核对日期：2026-09-22。实查硬件为 Ryzen 9 5950X（16 核 / 32 线程）、约 128 GB 内存、RTX 3080 10 GB；Ollama 0.32.14。

## 本机推荐

| 用途 | 模型 | 官方下载体积 | 本次动作 |
| --- | --- | --- | --- |
| 质量优先 | `qwen3.5:9b-q4_K_M` | 6.6 GB | 原已安装；摘要 `6488c96fa5fa` 与官方当前版本一致，保留 |
| 批量初筛 | `qwen3.5:4b-q4_K_M` | 3.4 GB | 新增安装；摘要 `2a654d98e6fb`，实际图片/镜头分析验证 |

两者均为原生视觉模型、Q4_K_M、Apache 2.0。最低 Ollama 要求为 0.17.1，本机已满足。下载体积不等于完整运行显存；画面数量、分辨率、上下文和其他桌面程序都会影响占用。

官方来源：[9B 模型条目](https://ollama.com/library/qwen3.5:9b-q4_K_M)、[4B 模型条目](https://ollama.com/library/qwen3.5:4b-q4_K_M)、[Qwen 模型卡与小型号视觉评测](https://huggingface.co/Qwen/Qwen3.5-9B#vision-language)。最低版本核对自官方 registry 配置：[9B](https://registry.ollama.ai/v2/library/qwen3.5/blobs/sha256:be595b49fe22012bd1f5605ec14c7ffa58331783a88a4fd8c22e5fc8ec42cf9f)、[4B](https://registry.ollama.ai/v2/library/qwen3.5/blobs/sha256:de9fed2251b37295b763727a59ca35cf5cfe5c7379bc3e2104b2ce3c145aa887)。

## 为什么没有替换成 Qwen3.8

Qwen3.8 是更新的家族，但截至核对时，官方公开的最小通用本地型号是 27B，Ollama 的 `qwen3.8:27b-q4_K_M` 下载体积约 18 GB，尚需运行时空间；未找到官方 4B/9B 的 Qwen3.8。其权重本体已超出 10 GB 显存。CPU 分担虽可能借助大内存运行，但不适合作为每天大量素材处理的默认方案。本次没有下载或替换成 27B。

来源：[Qwen3.8 官方集合](https://huggingface.co/collections/Qwen/qwen38)、[27B 官方模型卡](https://huggingface.co/Qwen/Qwen3.8-27B)、[Ollama 型号列表](https://ollama.com/library/qwen3.8/tags)。这是对本机适配的工程判断，不是声称 9B 比 27B 更先进。

## 在 APP 中配置

打开「模型设置」，选择本地 Ollama 后刷新列表，挑选标有视觉能力的模型。默认只列实际已经安装的模型；软件不会自动下载权重。可切换「快速 / 均衡 / 细读」：分别每镜 3 / 5 / 7 张证据，使用不同视觉尺寸。细读占用更多上下文与显存，10 GB 显卡优先使用快速或均衡。

其他用户可配置自己的本地或 OpenAI-compatible 服务，填写地址和模型名。兼容服务未提供能力元数据时，模型列表会标记为能力未核实；服务可用性与视觉支持需要实际调用确认。更换远程地址默认关闭 AI，明确启用后才把证据图片发送到该服务。

API key 仅保留在当前后台进程内，并绑定到填写的服务地址；不会写入素材记录、设置文件或队列。重新启动后需重新输入远程密钥。非密钥的模型与模式设置会保存。已经加入队列的任务使用当时的模型/模式快照，修改默认设置只影响后续加入的任务。

本项目当前采用多张关键帧理解，不直接把整段视频或声音送入模型。模型具备视频能力不代表当前适配器已启用完整视频时序输入；音频语义仍不在本版能力内。模型响应中的计时与 token 数保存在每镜 `extensions.vision_usage`，用于诊断吞吐，不代表准确率。
