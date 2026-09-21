# FramePulse 0.1 实现与接口契约

本文面向开源贡献者和 FlowDesk 等集成方，描述当前模块、文件和本地 API。FMP 0.1.0 是实验草案；字段语义见 [PROTOCOL.md](PROTOCOL.md)，结构定义见 [JSON Schema](../src/framepulse/schemas/fmp-0.1.schema.json)。字段可以表达，不代表模型输出已获准确性认证。当前没有 OTIO/NLE 工程导出、XMP 写回、音频转写或多用户远程服务。

Python 3.10+，使用 `src` 布局。引擎和 CLI 可独立于界面运行；本地工作台由 FastAPI 与 `src/framepulse/web` 中的静态 HTML/CSS/JavaScript 组成。JSON 是权威记录，当前不依赖数据库。默认数据目录 `runtime/library` 不纳入 Git；资产目录包含 `<data>/<sha256>/record.fmp.json`、`frames/` 和需要时生成的 `preview.mp4`。源文件不被改写。`.write.lock` 用于同一资产的跨进程写锁；记录通过同卷临时文件原子替换。

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
     subjects:Claim,actions:Claim,setting:Claim,lighting:Claim,color:Claim,narrative_function:Claim,tags:Claim,...},
   emotion:{valence:Claim,arousal:Claim,tension:Claim},
   regions:[{id:string,keyframe_id:string,xywh:[x,y,w,h],label:string,origin:"human"|"inferred",confidence:number|null}],
   review:{state:"unreviewed"|"accepted"|"edited"|"rejected",note:string},
   status:"measured"|"analyzed"|"failed", warnings:[]}],
 runs:[{id:string,kind:"measurement"|"vision"|"ocr",provider:string,model:string|null,
   started_at:ISO8601,completed_at:ISO8601|null,status:string,config:{},...}],
 history:[], warnings:[], extensions:{} }
Claim = {value: any JSON or null, origin:"measured"|"inferred"|"human", confidence:number|null,
 evidence_ids:[keyframe IDs],run_id:string|null,review_status:"unreviewed"|"accepted"|"edited"|"rejected"}.
```

未知信息使用 null/缺失，不补零。VLM 提供的 confidence 未经校准；当前紧凑提示默认不索取字段级分数，未返回时保留 null。情绪三轴为 0–1，valence 的中性值为 0.5。

## 模块接口

- `protocol.py`：`now_iso()`、`stable_id(*parts)`、`claim(value, origin='measured', confidence=None, evidence_ids=None, run_id=None)`；`validate_document(doc) -> list[str]`；`read_document(path)`；`write_document(path, doc)` 原子写入；`export_web_annotations(doc, source_uri=None, evidence_base_uri=None) -> dict`；`export_shots_csv(doc) -> str`。
- `engine.py`：`analyze_asset(source: Path, output_root: Path, *, vision=False, provider_url=None, model=None, api_key=None, threshold=27.0, resume=True, progress=None, cancel=None, ocr=False) -> Path`，返回记录路径。`progress(dict)` 接收 phase/current/total/message；`cancel()` 返回布尔值。`capabilities() -> dict` 报告执行程序、可选依赖和适配器能力，适配器存在不代表模型服务可达。
- `providers.py`：Ollama 原生及 OpenAI-compatible 视觉请求、模型结果归一化、短帧 ID 到稳定证据 ID 的转换。
- `server.py`：`create_app(data_dir, *, token=None) -> FastAPI`。
- `cli.py`：`main(argv=None) -> int`，提供 capabilities/analyze/validate/inspect/search/export/serve。

引擎 threshold 范围为 `(0,255]`，当前 CLI/API 对外限制为 `[1,100]`。测量不需要模型，也不上传媒体；OCR 需要可选 RapidOCR 依赖。引擎默认 `vision=False`，程序不自动下载或启动模型。CLI 的 `--vision` 显式启用视觉，`--no-vision` 强制仅测量；当本地发现结果明确返回视觉能力时，CLI 可以自动启用该本地服务。配置远程端点不会触发这一自动规则。网页提交的 `vision` 决定该次请求是否调用模型。

取消在阶段及模型等待中检查，已完成检查点保留；未完成测量不保证已有完整记录。断开模型请求不保证远端计算已经终止。

## 本地 HTTP

CLI 的 serve 仅绑定 `127.0.0.1`。同源 API，不开放 CORS 通配；写请求需会话 token。以下 `{hash}` 为完整 SHA-256，响应可包含未列出的附加信息。

```text
GET /api/health -> {version,capabilities}; GET /api/assets -> {assets:[{id:<hash>,name,kind,shot_count,duration_us,analyzed_count,reviewed_count,thumbnail:<URL|null>,updated_at}]}
GET /api/assets/{hash} -> document; GET /api/assets/{hash}/media -> actual source/preview with Range; GET /api/assets/{hash}/files/{relative} -> frames only inside bundle.
POST /api/analyze {path,vision:false,provider_url?,model?,threshold?:27,ocr?:false} -> {id:job ID}. Browser can pass a local absolute path. File dialog endpoint POST /api/pick -> {path|null} optional Windows tkinter.
GET /api/jobs -> {jobs:[{id,state:"queued"|"running"|"completed"|"failed"|"cancelled",progress:{phase,current,total,message},asset_id?,error?}]}; POST /api/jobs/{id}/cancel.
PATCH /api/assets/{hash}/shots/{shotId} {revision:int,review?:{state,note},analysis?:{field:new value},emotion?:{field:number|null},regions?:array} -> updated document. 409 stale revisions. Server adds history, human provenance, preserves prior values.
GET /api/assets/{hash}/export?format=json|csv|web-annotation returns download.
GET /api/config -> {token,provider_url,model,vision_available,provider_source} for UI session; X-FramePulse-Token required for writes; Origin and Host checked. API keys via server env only, never returned.
GET /api/protocol -> {name,version,status:"experimental",schema:JSON_Schema}
```

PATCH 使用资产锁与 revision，繁忙或过期返回 409。编辑值写入人工来源并保留原字段的证据集合；新增字段没有原证据时为空。认可操作保留原来源，修改前的完整镜头存入 history。记录校验失败返回 422。媒体接口无代理而读取原视频时，首次读取或文件状态改变后核对 SHA-256；媒体不匹配时拒绝套用旧记录。

## 证据、时间与导出

`keyframes[].uri` 使用资产目录相对路径，例如 `frames/f-000000120.jpg`，以记录所在目录解析，不以当前工作目录解析。关键帧保存实际显示方向；图片先应用 EXIF 方向。区域坐标属于对应关键帧，不能解释为连续对象轨迹。

视频证据保存 `source_pts`、`decode_index`；技术信息保存 `source_first_pts`、`source_first_pts_us`、`time_base` 和精度说明。时间来自实际解码 PTS、首帧归零并取整为微秒；完整逐帧 PTS 表未写入 sidecar。当前明确 `timing_frame_exact=false`，末帧时长可能估算并作标记。

W3C 导出以原媒体为镜头目标，以**证据关键帧本身**为区域目标；矩形像素按该关键帧尺寸转换，不使用从单个时刻持续到片尾的选择器。CLI `--source-uri` 只改主媒体 URI，`--evidence-base-uri` 独立指定证据目录基 URI，并应以 `/` 结尾。默认使用本机 file URI；导出不上传素材，也不是自动匿名化。完整 JSON 为无损保存入口，CSV/W3C 不能替代全部来源与历史。

## 模型接口与续跑

模型请求使用紧凑 JSON 值和请求内短帧 ID（K1 等），落盘时转为 Claim 和稳定 UUID。这是内部传输格式，不是第二种 FMP。每镜头最多提交五张时间抽样帧；没有特定字段引用时，Claim 关联全部输入帧，运行配置明确 evidence_association，因此关联帧不等于逐项证明。

Ollama 原生请求使用 `think=false`、8192 context、最多 3000 生成 token；OpenAI-compatible 使用最多 3000 输出 token，不统一设置其服务端 context。截断或非法结果最多重试一次，不补猜残缺 JSON；失败镜头保留并标记失败。密钥通过独立 api_key 或 CLI/服务器的 `FRAMEPULSE_API_KEY` 传入，不允许在模型地址中嵌入账号密码、查询参数或 fragment。

续跑区分文档级 `measurement_fingerprint`（阈值、检测器、选帧策略）和镜头级 `vision_fingerprint`（端点、模型、提示、响应结构、提交证据、适配器版本）。配置变化会重新执行对应阶段；人工保护独立于缓存，`--no-resume` 也不解除保护。accepted/edited/rejected 整镜头跳过自动视觉与 OCR。重新测量保存原 shots；边界变化而无法对应的人工记录进入 `extensions.archived_reviewed_shots`；视觉重分析保存替换前的非空 AI 字段。

## CLI 输出与界面约束

analyze 进度以 JSON Lines 写入 stderr，最终结果 JSON 写入 stdout，含记录路径、资产 ID、镜头数、失败数和状态。退出码：0 成功，2 参数/处理/校验错误，3 部分视觉或 OCR 失败，4 取消。export 不指定输出路径时直接在 stdout 输出所选格式；帮助和 argparse 参数错误沿用常规命令行输出。批次调度由调用方负责。

读写拒绝 NaN/Infinity。JSON Schema 和语义校验检查时间分区、图片空时间、摘要身份对应、唯一 ID、证据/运行引用和矩形范围；这些检查不能证明语义判断正确或远程 URI 可访问。

界面使用 `web/` 的本地静态资源、中文文案和真实空态，不依赖外部字体/CDN。默认变量为 bg `#0d131b`、panel `#121a24`、line `#273443`、ink `#f4f7fb`、muted `#8f9baa`、saffron `#f3bd54`。播放器、镜头列表、测量节奏/情绪展示、区域叠加、修订与导出使用同一份后端记录。空格播放/暂停、左右切镜不抢占输入控件。已确认和已编辑分别呈现；未知情绪不补零，静态区域不伪装为跟踪轨迹。
