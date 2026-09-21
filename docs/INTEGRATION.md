# CLI / FlowDesk 集成契约

CLI 的 `analyze` 单次接收一份媒体，宿主可自行安排批次与资源预算。0.2 起，APP 也提供显式文件夹导入和持久队列，宿主可通过 HTTP 使用这套队列。不会扫描未指定目录。默认串行使用视觉模型，避免多任务争用显存。

## 输入与输出

`framepulse analyze <absolute source path> --output <library root>`。使用子进程参数数组，禁止拼接 shell 字符串；中文、空格、引号都作为一个参数传入。

安装版/免安装版根目录提供 `framepulse.cmd` 供命令行使用。FlowDesk 等自动化宿主优先直接调用 `resources/backend/framepulse-backend.exe`，传参数数组并关闭 shell；该程序与 Python 安装后的 `framepulse` 入口遵循同一契约。开发源码可用 `python -m framepulse`。

- stdout：只包含最终 JSON 结果，字段有 `event=result`、`record`（绝对路径）、`asset_id`、`shot_count`、`failed_shots`、`vision`、`status`。
- stderr：NDJSON 进度/错误；进度含 `event=progress`、`phase`、`current`、`total`、`message`。不要依赖中文 message 的文字解析逻辑。
- 退出码：0 完成；2 输入、引擎或协议错误；3 部分 AI 镜头失败；4 取消（若取消发生在尚未创建首份记录前，不会返回 record）。
- 可以复用原命令续做。宿主只在看到退出码及记录完成后消费新结果，不凭文件存在推断全流程完成。记录本身的 runs、shot.status、warnings 给出更细的状态。
- `--no-vision` 只测量；`--vision` 显式启用指定 provider。默认只自动采用已发现本地 Ollama 视觉模型，不自动把素材发往云端。
- `--profile fast|balanced|detailed` 选择证据和视觉尺寸。单张图片可输入；视频超过 45 分钟会被拒绝，不会静默截断为前 45 分钟。打包 APP 的 `resources/backend/framepulse-backend.exe` 提供相同 CLI，不依赖系统 Python。
- `FRAMEPULSE_API_KEY` 仅传给子进程环境，不放到命令行、URL 或项目 JSON。

## 读取与跳转

先执行 `validate`，再按 `asset.id` + `shot.id` 稳定引用，不使用文件名或镜头序号作为关联键。视频定位秒数为 `shot.range.start_us / 1e6`；范围半开。播放器能否逐帧精确定位取决于其解码实现，不能只用平均 fps 推算 VFR 帧号。

工作台链接支持 `http://127.0.0.1:47840/?asset=<sha256>&shot=<shot-id>`（前端实现），由宿主打开对应素材并选择镜头。记录中的 `regions` 只绑定 `keyframe_id`，不是持续跟踪框；应在该证据图上按归一化坐标绘制。视频原文件由 `asset.source.uri` 定位，证据图路径相对于 `record.fmp.json` 所在目录。

## HTTP 审核工作台

只监听 loopback。`GET /api/config` 取得本会话 token，所有写请求带 `X-FramePulse-Token`；服务器拒绝跨 Origin 和非本机 Host。API 不适合作为公网部署入口。

`GET /api/assets` / `GET /api/assets/{sha256}` 读取；`POST /api/analyze` 提交任务；`GET /api/jobs` 查询；`POST /api/jobs/{id}/cancel` 取消。`PATCH /api/assets/{sha256}/shots/{shotId}` 使用当前 `revision` 提交字段值与审核状态；陈旧 revision 或资产正在分析时返回 409。人工写入原值进入 history。

所有基于同 SHA-256 的分析/修订共享操作系统文件锁，CLI 与 UI 不能交叉覆盖同一素材。读取已有 checkpoint 不受阻塞。不要从外部直接覆盖正在使用的记录文件。

### 0.2 批量 API

- `POST /api/queue/add`：`{paths:[absolutePath,...],recursive:true,vision:true,model:"...",provider_url:"...",profile:"fast",threshold:27,ocr:false}`。未提供的设置使用当前默认值。只入队，不启动；返回 `jobs` 和带原因的 `skipped`。
- `POST /api/queue/start`、`pause`、`resume`：控制派发。暂停允许当前素材完成；当前素材要立即停止需调用该任务 `cancel`。
- `GET /api/jobs`：`{jobs:[{id,path,name,state,progress,model,profile,vision,...}],queue:{state,active_job_id,pending_count,error}}`。任务状态为 `pending/running/completed/failed/cancelled`；队列为 `idle/running/paused`。`queue.error` 表示本地存储异常，不能当作仍在正常运行。
- `POST /api/jobs/{id}/retry`：把失败/取消任务重新设为待处理，之后开始或恢复队列。
- `GET /api/models?provider_url=...` 查询模型；`POST /api/config` 保存非密钥默认配置。`api_key` 仅进程内有效且绑定服务 URL；省略表示保留、空字符串表示清除。

任务持久化时保留入队的模型/模式快照；修改默认配置不追溯修改已有队列。APP 重启会恢复为待处理并暂停，远程密钥需重新输入。

`progress.phase=hash` 的 current/total 单位为字节；`detect` 为微秒；`keyframes` 为帧数；`vision/ocr` 为镜头数。进度条只表示当前阶段，不是全任务统一百分比。长片可能长时间停留在同一个 AI 镜头，使用 elapsed_seconds 判断耗时。

桌面 APP 使用自己申请的临时 loopback 端口，不应硬编码为 47840。需要固定服务端口的宿主应自行启动 `serve --port <port> --data <library>`；CLI 集成不需要桌面窗口或端口。

## 公开交换

`export --format web-annotation --source-uri https://host/media.mp4 --evidence-base-uri https://host/bundle/`。区域指向证据帧，时间段标记指向媒体。主媒体 URI 和证据基址都需要接收方可访问；只替换一项不代表所有本机定位信息均已移除。FMP 中保留原始摘要作为身份核验，公开共享副本需按自己的隐私政策处理路径与备注。
