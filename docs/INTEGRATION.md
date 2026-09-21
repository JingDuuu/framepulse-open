# CLI / FlowDesk 集成契约

宿主程序拥有素材发现、批量队列、资源预算和重试策略。FramePulse 单次接收一份媒体，不扫描整盘或自动递归资产目录。可以由宿主限制并发为 1（本地大模型通常更合适），逐次收集结果。

## 输入与输出

`framepulse analyze <absolute source path> --output <library root>`。使用子进程参数数组，禁止拼接 shell 字符串；中文、空格、引号都作为一个参数传入。

- stdout：只包含最终 JSON 结果，字段有 `event=result`、`record`（绝对路径）、`asset_id`、`shot_count`、`failed_shots`、`vision`、`status`。
- stderr：NDJSON 进度/错误；进度含 `event=progress`、`phase`、`current`、`total`、`message`。不要依赖中文 message 的文字解析逻辑。
- 退出码：0 完成；2 输入、引擎或协议错误；3 部分 AI 镜头失败；4 取消（若取消发生在尚未创建首份记录前，不会返回 record）。
- 可以复用原命令续做。宿主只在看到退出码及记录完成后消费新结果，不凭文件存在推断全流程完成。记录本身的 runs、shot.status、warnings 给出更细的状态。
- `--no-vision` 只测量；`--vision` 显式启用指定 provider。默认只自动采用已发现本地 Ollama 视觉模型，不自动把素材发往云端。
- `FRAMEPULSE_API_KEY` 仅传给子进程环境，不放到命令行、URL 或项目 JSON。

## 读取与跳转

先执行 `validate`，再按 `asset.id` + `shot.id` 稳定引用，不使用文件名或镜头序号作为关联键。视频定位秒数为 `shot.range.start_us / 1e6`；范围半开。播放器能否逐帧精确定位取决于其解码实现，不能只用平均 fps 推算 VFR 帧号。

工作台链接支持 `http://127.0.0.1:47840/?asset=<sha256>&shot=<shot-id>`（前端实现），由宿主打开对应素材并选择镜头。记录中的 `regions` 只绑定 `keyframe_id`，不是持续跟踪框；应在该证据图上按归一化坐标绘制。视频原文件由 `asset.source.uri` 定位，证据图路径相对于 `record.fmp.json` 所在目录。

## HTTP 审核工作台

只监听 loopback。`GET /api/config` 取得本会话 token，所有写请求带 `X-FramePulse-Token`；服务器拒绝跨 Origin 和非本机 Host。API 不适合作为公网部署入口。

`GET /api/assets` / `GET /api/assets/{sha256}` 读取；`POST /api/analyze` 提交任务；`GET /api/jobs` 查询；`POST /api/jobs/{id}/cancel` 取消。`PATCH /api/assets/{sha256}/shots/{shotId}` 使用当前 `revision` 提交字段值与审核状态；陈旧 revision 或资产正在分析时返回 409。人工写入原值进入 history。

所有基于同 SHA-256 的分析/修订共享操作系统文件锁，CLI 与 UI 不能交叉覆盖同一素材。读取已有 checkpoint 不受阻塞。不要从外部直接覆盖正在使用的记录文件。

## 公开交换

`export --format web-annotation --source-uri https://host/media.mp4 --evidence-base-uri https://host/bundle/`。区域指向证据帧，时间段标记指向媒体。主媒体 URI 和证据基址都需要接收方可访问；只替换一项不代表所有本机定位信息均已移除。FMP 中保留原始摘要作为身份核验，公开共享副本需按自己的隐私政策处理路径与备注。
