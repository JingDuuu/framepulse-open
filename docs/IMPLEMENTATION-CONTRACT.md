# FramePulse 0.2 实现与接口契约

本文面向开源贡献者和 FlowDesk 等集成方，描述应用 0.2.0 的模块、文件和本地 API。交换协议仍为 **FMP 0.1.0** 实验草案；字段语义见 [PROTOCOL.md](PROTOCOL.md)，结构定义见 [JSON Schema](../src/framepulse/schemas/fmp-0.1.schema.json)。新增 `overview` 和媒介/主题字段为可选扩展，旧记录无需补齐。字段可以表达，不代表模型输出已获准确性认证。当前没有 OTIO/NLE 工程导出、XMP 写回、音频转写或多用户远程服务。

Python 3.10+，使用 `src` 布局。引擎和 CLI 可独立于界面运行；工作台由 FastAPI 与 `src/framepulse/web` 中的本地静态资源组成，独立桌面窗口由 Electron 承载。打包桌面版内含冻结的 Python 后台，使用者无需另装 Python 或 Node.js；源码桌面开发需要 Node.js 22.12+。

JSON 是权威记录，当前不依赖数据库。CLI 默认数据目录为相对当前工作目录的 `runtime/library`；项目 `Start-FramePulse.cmd` 明确指定项目下的该目录。直接启动打包 APP 默认使用 Windows `%APPDATA%/FramePulse/library`，可用 `--data <绝对路径>` 覆盖。资产目录包含 `<data>/<sha256>/record.fmp.json`、`frames/` 和需要时生成的 `preview.mp4`。源文件不被改写。`.write.lock` 用于同一资产的跨进程写锁；分析期间允许读，拒绝并发分析和人工写入；完整记录通过同卷临时文件原子替换。

库根目录还包含非交换文件 `settings.local.json`（非敏感首选项）和 `queue.local.json`（持久任务队列）。它们与资产 sidecar 分开，均不纳入 Git。

文档类型示意（不是可直接解析的 JSON，可选实现字段未全部展开）：

```text
{format:"framepulse.media", spec_version:"0.1.0", id:"urn:uuid:...", revision:1,
 created_at: ISO8601, updated_at: ISO8601,
 asset:{id:"urn:sha256:<full hash>", kind:"video"|"image", name:string,
   source:{uri:"file:///...",sha256:string,size_bytes:int},
   technical:{width:int,height:int,duration_us:int|null,frame_rate:{numerator:int,denominator:int}|null,
     codec:string|null,has_audio:bool,time_base:string|null,...}},
 time:{unit:"microseconds",origin:"first_presented_frame",interval:"half-open"},
 shots:[{id:string,index:1,range:{start_us:int,end_us:int}|null,
   boundary:{method:string,confidence:number|null},
   keyframes:[{id:string,time_us:int|null,uri:"frames/xxx.jpg",width:int,height:int}],
   analysis:{summary:Claim,shot_size:Claim,camera_angle:Claim,composition:Claim,camera_movement:Claim,
     subjects:Claim,actions:Claim,setting:Claim,lighting:Claim,color:Claim,narrative_function:Claim,tags:Claim,
     visual_medium:Claim,themes:Claim,...},
   emotion:{valence:Claim,arousal:Claim,tension:Claim},
   regions:[{id:string,keyframe_id:string,xywh:[x,y,w,h],label:string,origin:"human"|"inferred",confidence:number|null}],
   review:{state:"unreviewed"|"accepted"|"edited"|"rejected",note:string},
   status:"measured"|"analyzed"|"failed", warnings:[]}],
 runs:[{id:string,kind:"measurement"|"vision"|"ocr",provider:string,model:string|null,
   started_at:ISO8601,completed_at:ISO8601|null,status:string,config:{},...}],
 overview?:{status:"measured"|"partial"|"completed",
   coverage:{total_shots:int,analyzed_shots:int,semantic_shots:int,abstained_shots:int,failed_shots:int},
   analysis:{summary:Claim,visual_medium:Claim,themes:Claim,narrative:Claim,tags:Claim,subjects:Claim},
   source_shot_ids:[shot IDs],warnings:[]},
 history:[], warnings:[], extensions:{} }
Claim = {value: any JSON or null, origin:"measured"|"inferred"|"human", confidence:number|null,
 evidence_ids:[keyframe IDs],run_id:string|null,review_status:"unreviewed"|"accepted"|"edited"|"rejected"}.
```

未知信息使用 null/缺失，不补零。VLM 提供的 confidence 未经校准；当前紧凑提示默认不索取字段级分数，未返回时保留 null。情绪三轴为 0–1，valence 的中性值为 0.5。

镜头 `visual_medium.value` 为 `2d_animation`、`3d_animation`、`live_action`、`mixed`、`screen_capture`、`graphic`、`unknown` 或 null；`themes.value` 为标签数组或 null。整片 `overview.analysis.visual_medium.value` 是去重排序后的数组或 null，可以同时包含多种媒介。`summary`、`narrative` 为字符串或 null；其余概括字段为数组或 null。

概括由已有镜头 Claim 摘录汇总，不额外请求模型，也不推断未见内容。汇总 Claim 保留 `evidence_ids`、`source_shot_ids` 和 `aggregation:"extractive-source-claims"`；默认 `origin:"inferred"`、`confidence:null`、`run_id:null`。已有人工或 accepted/edited 概括保留。覆盖计数不是互斥分类：`analyzed_shots` 包含明确弃答，`semantic_shots` 只统计存在可用语义且未拒绝的镜头；`completed` 表示逐镜处理完成，不表示全片内容都已获准确标注。拒绝或未知内容不会被补成剧情。

## 模块接口

- `protocol.py`：`now_iso()`、`stable_id(*parts)`、`claim(value, origin='measured', confidence=None, evidence_ids=None, run_id=None)`；`validate_document(doc) -> list[str]`；`read_document(path)` 回放私有检查点；`write_document(path, doc)` 原子写入；`build_overview(doc) -> dict`；`export_web_annotations(doc, source_uri=None, evidence_base_uri=None) -> dict`；`export_shots_csv(doc) -> str`。内部写入接口 `append_shot_checkpoint()`、`compact_document()` 由持有资产锁的引擎调用。
- `engine.py`：`analyze_asset(source: Path, output_root: Path, *, vision=False, provider_url=None, model=None, api_key=None, threshold=27.0, resume=True, progress=None, cancel=None, ocr=False, profile='balanced') -> Path`，返回记录路径。`progress(dict)` 接收 phase/current/total/message；`cancel()` 返回布尔值。`capabilities() -> dict` 报告执行程序、可选依赖、完整 profiles 参数和 `max_video_duration_us`，适配器存在不代表模型服务可达。
- `providers.py`：Ollama 原生及 OpenAI-compatible 视觉请求、模型结果归一化、短帧 ID 到稳定证据 ID 的转换。
- `config.py`：模型发现、非敏感首选项落盘和仅进程内的凭证管理。
- `jobs.py`：`JobQueue` 管理本地持久队列，串行调用单资产引擎；支持文件/目录导入、暂停、取消、重试和重启恢复。
- `server.py`：`create_app(data_dir, *, token=None) -> FastAPI`。
- `cli.py`：`main(argv=None) -> int`，提供 capabilities/analyze/validate/inspect/search/export/serve。

引擎 threshold 范围为 `(0,255]`，当前 CLI/API 对外限制为 `[1,100]`。测量不需要模型，也不上传媒体；OCR 需要可选 RapidOCR 依赖，默认冻结桌面构建不包含它。引擎默认 `vision=False`，程序不自动下载或启动模型。CLI 的 `--vision` 显式启用视觉，`--no-vision` 强制仅测量；当本地发现结果明确返回视觉能力时，CLI 可以自动启用该本地服务。配置远程端点不会触发这一自动规则。

支持图片和最长 45 分钟的视频。视频先 probe 再计算文件摘要，解码阶段再次校验实际时间；45 分钟是当前引擎输入上限，不是 FMP 格式限制或处理时限承诺。三档均逐帧解码检测，关键帧在时间分段内按质量选择，再以一次顺序解码提取；不会为每个镜头反复从头解码。

| profile | 检测最长边 | 每镜证据/提交帧上限 | 保存证据最长边 | 视觉请求最长边 | Ollama context |
| --- | ---: | ---: | ---: | ---: | ---: |
| `fast` | 256 | 3 | 960 | 768 | 8192 |
| `balanced`（默认） | 320 | 5 | 1280 | 960 | 8192 |
| `detailed` | 480 | 7 | 1600 | 1280 | 16384 |

图片只生成一张显示方向校正后的证据；极短镜头可能不足表中帧数。三档当前请求同一套导演字段，仅改变测量尺寸和证据覆盖；快速档不承诺与细读档同等语义准确率。

取消在阶段及模型等待中检查，已完成检查点保留；未完成测量不保证已有完整记录。扫描完成后的私有 `.measurement-cache.json` 可复用选帧计划，扫描中途取消仍需重新扫描。断开模型请求不保证远端计算已经终止。

## 本地 HTTP

CLI 的 serve 仅绑定 `127.0.0.1`。同源 API，不开放 CORS 通配；写请求需会话 token。以下 `{hash}` 为完整 SHA-256，响应可包含未列出的附加信息。

```text
GET /api/health -> {version,app:"framepulse",capabilities}; GET /api/assets -> {assets:[{id:<hash>,name,kind,shot_count,duration_us,analyzed_count,reviewed_count,thumbnail:<URL|null>,updated_at,overview?,warnings,status}]}
GET /api/assets/{hash} -> document; GET /api/assets/{hash}/media -> actual source/preview with Range; GET /api/assets/{hash}/files/{relative} -> frames only inside bundle.
POST /api/analyze {path,vision?:false,provider_url?,model?,threshold?:27,ocr?:false,profile?:"balanced"} -> {id:job ID}. Enqueues one file and starts the queue.
POST /api/queue/add {paths:[local file or directory],recursive?:true,vision?,provider_url?,model?,threshold?:27,ocr?:false,profile?} -> {jobs:[job],skipped:[{path,reason}]}. Adds pending jobs without starting the queue.
POST /api/queue/start | /api/queue/resume | /api/queue/pause -> queue snapshot.
GET /api/jobs -> {jobs:[{id,path,name,state:"pending"|"running"|"completed"|"failed"|"cancelled",profile,model,vision,progress:{phase,current,total,message,...},asset_id?,error?,...}],queue:{state:"idle"|"running"|"paused",active_job_id,pending_count,error}}.
POST /api/jobs/{id}/cancel -> {ok:true}; POST /api/jobs/{id}/retry -> {ok:true}.
POST /api/pick {mode?:"files"|"folder"} -> {paths:[string],path:string|null}. Optional browser-mode tkinter picker; Electron uses authorized native IPC.
PATCH /api/assets/{hash}/shots/{shotId} {revision:int,review?:{state,note},analysis?:{field:new value},emotion?:{field:number|null},regions?:array} -> updated document. 409 stale revisions. Server adds history, human provenance, preserves prior values.
GET /api/assets/{hash}/export?format=json|csv|web-annotation returns download.
GET /api/config -> {token,provider_url,model,vision,profile,vision_available,provider_source,has_api_key,max_video_minutes:45,profiles:{fast:{label,description},balanced:{label,description},detailed:{label,description}}}.
POST /api/config {provider_url?,model?,vision?,profile?,api_key?} -> updated config including token, excluding the credential.
GET /api/models?provider_url=<optional URL> -> {models:[{name,size,capabilities,vision,details?}],provider_url,errors:[]}.
GET /api/protocol -> {name,version,status:"experimental",schema:JSON_Schema}
```

写请求需要 `X-FramePulse-Token`，且检查 Origin/Host。API key 可通过配置 API 或 `FRAMEPULSE_API_KEY` 提供，只在当前进程内按端点保存，不写配置、队列或返回值；空值可清除此端点的进程内凭证。重启后需重新输入，或由环境重新提供。`vision_available` 只表示已配置模型名，不是服务健康或视觉能力认证；模型列表中的 `vision:null` 表示兼容端点未报告能力。

`/api/queue/add` 未填 profile/vision 时使用当前首选项；替换端点且未明确 vision 时不自动继承上传授权。`/api/analyze` 为兼容单文件调用保留 `vision:false`、`profile:"balanced"` 默认值。入队时固定该任务的模型、模式等选项，后续修改首选项不改写已入队任务。单文件 analyze 的 start 会启动已有待处理队列，不是独立并发作业。

当前每次最多接收 10000 个路径、扫描加入 10000 份受支持媒体；队列历史总计上限 20000。按规范化路径跳过当前 pending/running 重复任务，不按文件名推断重复；同内容不同路径最终仍受 SHA-256 资产锁保护。队列一次处理一份素材。pause 让当前素材完成而暂停后续派发；cancel 请求停止对应任务；retry 只允许 failed/cancelled，重新置为 pending，复用已完成的引擎结果。重启把未完成任务恢复成 pending 并暂停，不自动派发。队列写入失败会暂停后续任务并返回错误，不声称大批量压力测试已通过。

PATCH 使用资产锁与 revision，繁忙或过期返回 409；同源文件排队时也禁止编辑。编辑值写入人工来源并保留原字段的证据集合；新增字段没有原证据时为空。认可操作保留原来源，修改前的完整镜头存入 history，并重新生成概括。记录校验失败返回 422。媒体接口无代理而读取原视频时，首次读取或文件状态改变后核对 SHA-256；媒体不匹配时拒绝套用旧记录。

## 证据、时间与导出

`keyframes[].uri` 使用资产目录相对路径，例如 `frames/f-000000120.jpg`，以记录所在目录解析，不以当前工作目录解析。关键帧保存实际显示方向；图片先应用 EXIF 方向，透明像素合成到明确标记的中性灰预览底。区域是校正后图像的 normalized xywh，属于对应关键帧；视觉区域的 `extensions` 记录 `source:"vision"`、`tracking:false`，不能解释为连续对象轨迹，也不能把透明预览底当成原图内容。

视频证据保存 `source_pts`、`decode_index`；技术信息保存 `source_first_pts`、`source_first_pts_us`、`time_base` 和精度说明。时间来自实际解码 PTS、首帧归零并取整为微秒；完整逐帧 PTS 表未写入 sidecar。当前明确 `timing_frame_exact=false`，末帧时长可能估算并作标记。

W3C 导出以原媒体为镜头目标，以**证据关键帧本身**为区域目标；矩形像素按该关键帧尺寸转换，不使用从单个时刻持续到片尾的选择器。CLI `--source-uri` 只改主媒体 URI，`--evidence-base-uri` 独立指定证据目录基 URI，并应以 `/` 结尾。默认使用本机 file URI；导出不上传素材，也不是自动匿名化。完整 JSON 为无损保存入口，CSV/W3C 不能替代全部来源与历史。

## 模型接口与续跑

模型请求使用紧凑 JSON 值和请求内短帧 ID（K1 等），落盘时转为 Claim 和稳定 UUID。这是内部传输格式，不是第二种 FMP。每镜头按 profile 最多提交 3/5/7 张时间抽样帧；没有特定字段引用时，Claim 关联全部输入帧，运行配置明确 evidence_association，因此关联帧不等于逐项证明。

Ollama 原生请求使用 `think=false`、表中 8192/16384 context、最多 3000 生成 token；OpenAI-compatible 使用最多 3000 输出 token，不统一设置其服务端 context。截断或非法结果最多重试一次，不补猜残缺 JSON；失败镜头保留并标记失败。结构有效的全部 null 输出是明确弃答，不伪造内容。运行 usage 可记录输入/输出 token、重试、请求耗时，以及 Ollama 返回的加载、预填充、生成耗时与生成 tokens/s；上限不等于实际生成量。密钥通过独立 api_key、配置 API 或 `FRAMEPULSE_API_KEY` 传入，不允许在模型地址中嵌入账号密码、查询参数或 fragment。

续跑区分文档级 `measurement_fingerprint`（阈值、检测器、选帧策略、profile/尺寸）和镜头级 `vision_fingerprint`（端点、模型、提示、响应结构、提交证据、profile/输入尺寸/context、适配器版本）。配置变化会重新执行对应阶段；人工保护独立于缓存，`--no-resume` 也不解除保护。accepted/edited/rejected 整镜头跳过自动视觉与 OCR。重新测量保存原 shots；边界变化而无法对应的人工记录进入 `extensions.archived_reviewed_shots`；视觉重分析保存替换前的非空 AI 字段。

逐镜结果先 fsync 追加到私有 `record.fmp.journal.jsonl`，每 25 个镜头检查点或约 60 秒压实一次；已完成测量的任务在正常结束、受控取消和异常路径通过 `finally` 再压实。**最终 `record.fmp.json` 独立包含全部记录，日志不是交换格式。** 运行中直接读取裸 JSON 可能看到较早快照，应通过 `read_document()`、CLI inspect/validate/export 或 API 获得已回放的最新视图。崩溃后回放完整日志行，忽略未完成末行；不匹配文档和不连续 revision 拒绝回放，已被较新 JSON 覆盖的事件不再生效。先原子写全量记录，再清理日志，避免旧检查点覆盖人工修改。

## CLI 输出与界面约束

analyze 进度以 JSON Lines 写入 stderr，最终结果 JSON 写入 stdout，含记录路径、资产 ID、镜头数、失败数和状态。退出码：0 成功，2 参数/处理/校验错误，3 部分视觉或 OCR 失败，4 取消。export 不指定输出路径时直接在 stdout 输出所选格式；帮助和 argparse 参数错误沿用常规命令行输出。CLI analyze 保持单资产入口；FlowDesk 等调用方可以自行调度，也可以调用工作台的持久队列 API。

读写拒绝 NaN/Infinity。JSON Schema 和语义校验检查时间分区、图片空时间、摘要身份对应、唯一 ID、证据/运行引用和矩形范围；这些检查不能证明语义判断正确或远程 URI 可访问。

界面使用 `web/` 的本地静态资源、中文文案和真实空态，不依赖外部字体/CDN。默认变量为 bg `#0d131b`、panel `#121a24`、line `#273443`、ink `#f4f7fb`、muted `#8f9baa`、saffron `#f3bd54`。播放器、镜头列表、测量节奏/情绪展示、区域叠加、修订与导出使用同一份后端记录。空格播放/暂停、左右切镜不抢占输入控件。已确认和已编辑分别呈现；未知情绪不补零，静态区域不伪装为跟踪轨迹。
