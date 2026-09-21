# FramePulse Media Protocol · FMP 0.1.0

FMP 是为视频、图片资产的自动分析、人工复核和跨产品读取设计的**实验性开放交换草案**。它是 FramePulse 自有的 JSON 格式，不是 W3C、IPTC 或其他组织认可的行业标准，也不表示其他软件已经支持它。0.1.0 仍可能因真实素材验证而调整；使用方应固定协议版本。

目标是让 FlowDesk 或其他调用方拿到一份文件，就能知道：它描述哪个源文件、镜头在哪里、看到了什么、结论来自哪些画面、哪些内容只是模型解释，以及人后来修正了什么。素材采集、批次调度和资产库搜索可以由调用方负责。

本文规定交换语义；具体入口和当前能力以程序的 CLI 帮助、`capabilities()` 和 [实现契约](IMPLEMENTATION-CONTRACT.md) 为准。协议允许表达某个字段，不等于当前模型一定能可靠生成该字段。

FramePulse Open **0.2.0 应用仍使用 `spec_version: "0.1.0"`**。本次增加可选的整片 `overview`、镜头媒介/主题字段和实现扩展；没有要求旧 0.1 记录补齐这些字段。旧记录仍可读取，消费者应保留自己不认识的可选字段。应用版本、处理档位和协议版本是三个独立概念。

## 1. 文件与身份

一个资产对应一个目录：

```text
<data-root>/<source-sha256>/
  record.fmp.json
  frames/
    <keyframe>.jpg
  preview.mp4              # 需要浏览器代理时才生成
```

处理完成后的 `record.fmp.json` 是完整、自包含的权威结构化记录。关键帧和代理是衍生文件，不能替代原素材身份。源文件保持不变；记录原子写入。搬运分析成果时应同时搬运 `frames/`，并在新环境重新关联源文件。

应用 0.2 在完成技术测量、进入逐镜标注后可暂存私有 `record.fmp.journal.jsonl`，避免长片每处理一个镜头就重写整份 JSON。程序的 `read_document()`、CLI 和本地 API 会回放已完整落盘的事件，得到最新视图；直接读取裸 JSON 的第三方在此期间可能看到较早的完整快照。日志每 25 个镜头检查点或约 60 秒压实一次；已有日志的任务正常结束、受控取消或异常退出时通过 `finally` 再压实。最终 JSON 不需要日志就能解释全部镜头、来源、概要和历史。

进程崩溃/断电时日志可能保留。下一次程序读取或续跑会回放完整行，忽略未写完的末行；不连续修订或资产不匹配会报错。已经被较新 JSON 修订覆盖的旧事件不再生效，不能覆盖后来的人改。日志是实现内部恢复文件，**不是第二种 FMP 交换格式**，第三方无需复制其事件结构。交换运行中的成果应通过程序读取/导出；脱离程序直接搬运时，应先等待任务完成或受控取消。

`.measurement-cache.json` 是另一个私有暂存文件，只保存已完成逐帧测量后的选帧计划，用于提帧阶段失败/取消后的继续执行。它不代表已有完整分析记录；逐帧扫描本身中断后仍需重新扫描。

| 字段 | 0.1 语义 |
| --- | --- |
| `format` | 固定 `framepulse.media`。 |
| `spec_version` | 固定 `0.1.0`；与应用版本无关。 |
| `id` | 记录的 UUID URI，与文件在磁盘上的位置无关。 |
| `revision` | 正整数；每次持久化编辑递增，用于检测同时编辑。 |
| `created_at` / `updated_at` | 带时区的 ISO 8601 时间。 |
| `asset.id` | 0.1 使用 `urn:sha256:<完整摘要>` 作为内部资产键；这是本草案约定，不宣称该 URN 命名空间已注册。 |
| `asset.source.sha256` | 原始文件全部字节的 SHA-256。重编码、重新封装或修改嵌入元数据都会改变它。 |
| `asset.source.uri` | 本机源文件 URI；它是定位线索，不是跨电脑可用性的保证。 |
| `asset.source.size_bytes` | 原文件字节数。 |
| `asset.kind` | `video` 或 `image`。 |
| `asset.name` | 展示名称，不能作为身份依据。 |

摘要相同可以复用该文件的分析；相似画面、不同码率文件或裁剪版不可因此共享相同坐标。跨编码资产家族、代理到原片的显式变换属于后续扩展。公开交换时，调用方应将本机 URI 替换为接收方能定位的媒体 URI；不应只改 URI 而忘记校验摘要。

## 2. 技术事实与时间

`asset.technical` 包括 `width`、`height`、`duration_us`、`frame_rate`、`codec`、`has_audio`、`time_base`。未知值为 `null`；静态图片没有视频时长，`duration_us`、`frame_rate` 和 `time_base` 可为 `null`。

`frame_rate` 使用 `{ "numerator": 30000, "denominator": 1001 }` 这样的有理数表示。帧率是技术描述，不能在可变帧率视频上代替逐帧时间戳。

0.1 的公共时间坐标为：

```json
{
  "time": {
    "unit": "microseconds",
    "origin": "first_presented_frame",
    "interval": "half-open"
  }
}
```

视频中首个实际显示帧为零点，时间以整数微秒表达；秒数等于微秒数除以 1,000,000。所有镜头范围都是 `[start_us, end_us)`：起点属于本镜头，终点属于下一个区间。这样相邻镜头不会共享同一终止帧。[W3C Media Fragments 1.0 §4.2.1](https://www.w3.org/TR/media-frags/) 采用相同的半开区间语义。

`shots[].range` 在视频中为 `{start_us, end_us}`；图片中为 `null`。视频应满足 `0 <= start_us < end_us <= duration_us`。镜头按起点排序；镜头序号是展示顺序，`id` 才是引用键。镜头重新切分后不得只凭 `index` 将旧人工标注贴到新镜头。

当前视频引擎按 FFmpeg 实际解码帧读取 PTS，以首帧为零点，并按解码序号抽取证据。证据帧保存 `source_pts` 和 `decode_index`；`asset.technical` 保存 `time_base`（如 `1/90000`）、`source_first_pts`、`source_first_pts_us`、`decoded_frames`、`frame_rate_mode` 及精度说明。当 `time_base` 可用时，当前微秒坐标的计算为 `round(source_pts * time_base * 1,000,000) - source_first_pts_us`；读取不到时间基准时使用解码器的秒时间。`round(frame_index / average_fps)` 不作为 VFR 的真实时间。

**微秒时间不等于帧精确承诺。** 上述源时间字段是当前实现附加信息，尚不是 0.1 JSON Schema 的必填项；完整逐帧 PTS 表仅在测量时使用，没有全部写入 sidecar。重复或倒退的显示时间戳会使分析停止；末帧持续时间无法读取时会估算，并设置 `last_frame_duration_estimated`。当前明确记录 `timing_frame_exact=false`，不能据此宣称所有 VFR、非零起始时间、编辑列表或 B 帧素材都通过帧精确回定位认证。

后续完整时间索引和剪辑适配仍应保留实际时间基准。导出 OTIO 时转换到 `RationalTime`，不能为了填满导出格式而虚构固定帧率。[OTIO 时间类型定义](https://opentimelineio.readthedocs.io/en/latest/tutorials/otio-serialized-schema.html)

## 3. 镜头、证据与处理覆盖

`shots[]` 同时承载视频镜头和图片的唯一分析单元。图片使用一个单元，不人为创造时长。

| 字段 | 用途 |
| --- | --- |
| `id` / `index` | 稳定引用键 / 从 1 开始的展示序号。 |
| `boundary.method` | 实际切镜算法或人工方式，不能只写“AI”。 |
| `boundary.confidence` | 边界可信度；算法未提供可信量化时为 `null`。 |
| `keyframes[]` | 证据帧，含 `id`、`time_us`、相对 `uri`、`width`、`height`。图片的 `time_us` 为 `null`。 |
| `analysis` | 导演分析字段，每个字段是独立 `Claim`。 |
| `emotion` | `valence`、`arousal`、`tension` 三个独立 `Claim`。 |
| `regions[]` | 指向具体证据帧的区域标记。 |
| `review` | 当前镜头的复核状态和备注。 |
| `status` | `measured`、`analyzed` 或 `failed`。 |
| `warnings[]` | 该镜头的覆盖不足、抽帧失败等实际问题。 |

`measured` 只表示已有技术测量/切镜等结果，不能作为内容理解完成；`analyzed` 表示执行过语义分析，也不表示所有字段正确；`failed` 不能用空描述伪装成功。接收方应检查每个字段与证据，而不是只看整个任务完成。

证据帧必须来自对应资产，`evidence_ids` 必须能在对应镜头中解析。当前视频引擎按档位将镜头分为 3/5/7 个时间区间，各选一张清晰度/曝光质量较好的帧；短镜头保留可用帧，VFR 的空区间由其他实际观察帧补足。图片始终只有一个实际图像单元，不复制成多个假证据。抽样帧只支持它实际展示的内容；这些选择不保证覆盖所有显著动作。单帧证据不能证明镜头全程的移动方向、动作过程、对白内容或转场形态。记录没有音频证据时，不应填充声画关系为已测事实。

### 3.1 三个处理档位与时长边界

以下是应用 0.2 的实现配置，不是 FMP 文件的普适限制。三个档位都测量全部解码帧并读取真实 PTS；它们不通过降帧来伪造时间精度，也不改变未知值规则。

| `profile` | 测量图像最长边 | 每镜自动证据 / 最多提交视觉模型 | 模型输入图像最长边 | 本地 Ollama 上下文 |
| --- | --- | --- | --- | --- |
| `fast` 快速 | 256 px | 3 / 3 | 768 px | 8192 |
| `balanced` 均衡，默认 | 320 px | 5 / 5 | 960 px | 8192 |
| `detailed` 细读 | 480 px | 7 / 7 | 1280 px | 16384 |

图像不会放大到上述尺寸；人工复核需要保留的旧证据可能使记录中的帧数超过自动选帧数量。详细档提供更多观察点和图像细节，但也增加时间、显存/内存需求，不能承诺比例对应的准确率提高。目前三档使用相同的导演字段集合；快速档不是隐藏删除所有专业字段的另一种协议。

应用当前支持图片和**单个不超过 45 分钟的视频**。已知超时长视频在计算完整 SHA-256 前拒绝；元数据缺失或不准确时，真实解码时长仍受同一上限约束。源文件的非零 PTS 偏移不当作内容时长。45 分钟是此版引擎边界，协议本身没有规定只能描述 45 分钟。文件较多时由队列/外部调度分配任务；一个完成标记不等于每个字段都有可靠结论。

### 3.2 可选的整片 `overview`

0.2 可在顶层增加以下对象，旧记录没有它仍然有效：

```text
overview: {
  status: "measured" | "partial" | "completed",
  coverage: {total_shots, analyzed_shots, semantic_shots, abstained_shots, failed_shots},
  analysis: {summary: Claim, visual_medium: Claim, themes: Claim,
             narrative: Claim, tags: Claim, subjects: Claim},
  source_shot_ids: [实际镜头 ID],
  warnings: [覆盖与解释限制]
}
```

| 字段 | 解释 |
| --- | --- |
| `summary.value` | 有覆盖比例的已标注镜头摘要摘录，保留镜头序号；长片最多选取 8 条分布在已有摘要序列中的不同摘录，不冒充逐段完整剧情。 |
| `visual_medium.value` | 已有镜头媒介代码的去重数组，或 `null`；例如同时包含 `2d_animation` 与 `live_action`。不是靠文件扩展名猜制作方式。 |
| `themes` / `tags` / `subjects` | 从已有镜头 Claim 汇聚的词语数组，或 `null`；不补充未观察的主题、物体或身份。 |
| `narrative.value` | 有镜头编号的原 `narrative_function` 用途推断摘录，或 `null`；不是新编的全片因果故事。 |
| `coverage` | 总镜头、已执行语义分析镜头、具有可用视觉语义镜头、明确弃答镜头与失败镜头分别计数；这些计数存在交集，不能直接相加。 |
| `source_shot_ids` | 本次概要所覆盖的有语义镜头 ID；每个概要 Claim 另有自己的 `source_shot_ids` 和证据集合。 |

当前概要是**基于既有标注的确定性摘录与汇聚**，没有额外把整部片子发送给模型做无依据剧情推演。概要 Claim 使用 `aggregation: "extractive-source-claims"`，`origin=inferred`、`run_id=null`，通过镜头和证据回到原始 Claim 及其运行来源。明确人工写入/确认的概要 Claim 会保留；重新切镜导致旧引用无法对应时归档，不强行重新关联。

`completed` 表示全体镜头完成过分析尝试，不表示语义全部已知：例如全黑过渡帧可以合法弃答。消费者必须同时显示 `semantic_shots`、`abstained_shots` 和空值。弃答保留 `shots[].extensions.vision_status="abstained"`；没有有效视觉判断时情绪也保持未知，不能为使曲线完整而填入中性值。原镜头/字段被拒绝后，不再作为自动概要的内容来源。

## 4. Claim：结论与依据分开保存

所有 `analysis` 和 `emotion` 字段采用同一个结构：

```json
{
  "value": "中近景",
  "origin": "inferred",
  "confidence": 0.72,
  "evidence_ids": ["frame-0002-a"],
  "run_id": "vision-run-001",
  "review_status": "unreviewed"
}
```

这只是结构示例，数值不是经过验证的准确率。字段的 `value` 可为文本、数组、对象、数字或 `null`；词汇建议见 [导演字段词表](DIRECTOR-TAXONOMY.md)。

| 字段 | 规则 |
| --- | --- |
| `origin` | `measured` 为实际探测/计算输出，`inferred` 为内容或审美推断，`human` 为人工写入。人工结论也可能有主观性。 |
| `confidence` | `null` 或 `[0,1]`。VLM 自报值是未经校准的信心，不能读作“正确概率 72%”；不同模型的分数不可直接排名。 |
| `evidence_ids` | 当前结论引用或分析输入的关键帧 ID。没有证据使用空数组，不能借用无关帧凑齐；关联帧本身不证明结论正确。 |
| `run_id` | 关联产生该值的 `runs[].id`；无运行关联时为 `null`。 |
| `review_status` | `unreviewed` / `accepted` / `edited` / `rejected`。人工认可不应把原 AI 来源改写为人工。 |

“未识别”与“识别为空”不同：`null` 表示未知/未分析；空数组表示该次分析明确返回了空结果，仍受其覆盖范围限制；零是实际数值。缺失情绪不填 0，未听取音频不填写“没有对白”，未读取 EXIF 不填写“未知镜头为 50mm”。

当前视觉适配器对模型的空语义列表保守写为 `null`；全体受支持字段都明确为空时另记弃答。因此界面中的空列表不能被读作已经证明不存在主体。

当前紧凑模型接口可以返回字段相关的帧引用；没有返回时，适配器将本次提交的全部帧记为输入关联，并在视觉运行的 `config.evidence_association` 中声明该策略。因此接收方不能把每个 ID 都当成模型逐项确认的支持证据。当前请求默认不要求模型编造字段级 confidence，通常保留 `null`；其他兼容响应提供的分数仍未经校准。

当前 Claim 的证据类型限于关键帧 ID。可选 RapidOCR 已将采样帧中的文字保存为 `analysis.ocr_text`，字框转换为 `regions` 中 `source=ocr` 的静态矩形；识别文本仍标为 `inferred`。音频片段、逐词转写、源元数据摘录以及校准后的置信度类型需要后续扩展。原素材文字和模型返回的描述都属于待分析数据，读取方不应把其中的命令当成应用指令。

## 5. 情绪曲线

0.1 的 `emotion` 表示**对作品呈现氛围的解释性标注**，不是对真实人物心理的测量，也不代表所有观众的反应。每轴范围为 `[0,1]`，未知为 `null`：

| 轴 | 0 | 0.5 | 1 |
| --- | --- | --- | --- |
| `valence` 情感倾向 | 强烈负向 | 中性或正负混合 | 强烈正向 |
| `arousal` 激活程度 | 平静、低激活 | 中等激活 | 强烈、躁动、高激活 |
| `tension` 叙事紧张度 | 几乎无悬念压力 | 中等悬念压力 | 强烈悬念/冲突压力 |

`tension` 是 FMP 的导演分析维度，不能直接冒充 EmotionML 的某个标准维度。EmotionML 对维度、置信度和时间轨迹作了区分；FMP 借鉴其思想，并未实现 EmotionML XML 导入导出。[W3C EmotionML 1.0 §2.2–2.4](https://www.w3.org/TR/emotionml/)

曲线节点必须关联对应镜头及其证据。镜头级平均判断只能画成镜头级节点/区间；不能将它平滑成每帧精确情绪。空值处保留断点；显示平滑时标明只是显示插值。快切、亮度、运动强度和音量可以是解释线索，不能直接换名为情绪。未来应拆分作品氛围、角色情绪、创作者意图和观众实测反应，并提供不同主体/上下文的轨迹。

## 6. 空间区域

```json
{
  "id": "region-001",
  "keyframe_id": "frame-0002-a",
  "xywh": [0.12, 0.18, 0.28, 0.56],
  "label": "画面左侧人物",
  "origin": "human",
  "confidence": null
}
```

`xywh` 依次是左上角 x、y、宽、高，使用 `[0,1]` 的归一化比例。坐标原点为左上角，x 向右、y 向下；`w > 0`、`h > 0`、`x+w <= 1`、`y+h <= 1`。

坐标属于 `keyframe_id` 指向的证据图像，按照该图像实际显示方向解释；抽取时应先应用视频旋转信息或图片 EXIF 方向。消费端以该关键帧的 `width`、`height` 还原像素；不应将带 EXIF 旋转的原始编码尺寸直接套用。播放器叠加区域时需映射到实际图像内容框，排除 `object-fit` 留下的黑边。

0.1 区域是**静态帧标记**，不代表目标在整个镜头内一直占据该区域，也不隐含跨镜头身份识别。跨时间跟踪、多边形、遮挡和区域级独立 Claim 属于后续扩展。人工改区域时应保留旧区域的历史记录。

应用 0.2 会要求视觉模型在指定锚点关键帧上尽可能定位 1–4 个主要可见人物/物体，无法确定时允许没有框。自动语义框可附 `extensions: {"source":"vision","tracking":false}`；OCR 字框与语义实体框仍应区分。预览可以突出该证据帧的区域，并跳转至相应时刻，不能在视频继续播放后把同一个框固定在屏幕上冒充跟踪结果。区域为空并不等于已经证明画面没有主体。

W3C 的 `xywh=pixel:x,y,w,h` 使用整数像素，`xywh=percent:...` 使用整数百分比，不能直接把 FMP 的 0–1 小数填入后者。小区域导出时优先使用关键帧像素矩形，并明确像素取整。IPTC 也已有图片区域与边界模型，但 FMP 的简化数组不等于 IPTC 原生结构。[W3C Media Fragments §4.2.2](https://www.w3.org/TR/media-frags/)、[IPTC Photo Metadata 2025.1 §11.20、§12.6–12.8](https://iptc.org/std/photometadata/specification/IPTC-PhotoMetadata-2025.1.html)

## 7. 运行来源、人工优先与历史

`runs[]` 保存 `id`、`kind`、`provider`、`model`、开始/完成时间、状态和 `config`。`kind` 为 `measurement`、`vision` 或 `ocr`。它解释本次结论如何产生；不保存 API 密钥。来源关系借鉴 PROV-O 中“实体由活动生成、活动使用其他实体、活动关联代理”的分离方式；0.1 尚不是 PROV-O RDF 序列化。[W3C PROV-O §3.1](https://www.w3.org/TR/prov-o/)

编辑请求携带当前 `revision`。服务器发现版本落后应拒绝并要求重新读取，不能静默覆盖。人工改值后，该字段使用 `origin=human`、`review_status=edited`；单纯点击认可保留原来源，仅更新复核状态。

**重跑必须保留人工结论和复核记录。** 对已认可、已编辑的字段，不应自动用新 AI 值替换；新结果可以作为后续候选。`history[]` 保存编辑前的值、证据引用和相关上下文，不能只记一句“已编辑”。删除或更换证据时，旧历史仍应可解释。当前历史不是数字签名、防篡改日志或法律证明。

当前实现已记录人工修订、重新测量和视觉重新分析的历史。每次重新测量保存完整的原 `shots`；镜头边界变化而无法按稳定 ID 对应的人工/已复核镜头另存于 `extensions.archived_reviewed_shots`，不会按序号硬贴到新镜头。视觉重分析保存替换前的非空 AI 字段。外部应用应保留这些历史，但避免绑定尚未稳定的事件内部结构；事件 ID、完整前后修订关系和多候选选择机制仍待后续规范。

续跑现在区分两类配置指纹：文档 `extensions.measurement_fingerprint` 包含检测阈值、检测器版本、档位及选帧/测量尺寸；镜头 `extensions.vision_fingerprint` 包含模型端点、模型名、提示、响应结构、提交的关键帧、图像尺寸、上下文配置和适配器版本。匹配的已完成工作可以跳过；改变测量配置会重新测量，改变视觉配置会重新分析未受人工保护的镜头。已认可、已编辑或已拒绝的整镜头不会被视觉/OCR 自动重跑。`--no-resume` 也不取消人工保护。

`vision_usage` 可记录实际输入/输出 token、提交帧数、格式重试次数，以及原生 Ollama 返回的加载、提示处理、生成和总耗时。生成 token 上限不是实际生成量，模型权重在 GPU 内也不等于整个流程始终满负载；比较速度应同时记录模型、档位、冷/热启动与这些实际数值，不只比较一个总秒数。

## 8. 交换、读取与版本演进

0.1 的交换出口是完整 FMP JSON、便于表格查看的镜头 CSV，以及 W3C Web Annotation JSON-LD 形式的标记导出。CSV 不能保留全部嵌套字段、来源和历史，不能充当无损回写格式。W3C 导出也是映射结果；完整保存仍以 FMP JSON 为准。

当前 CLI 的 W3C 导出支持分别替换主媒体和证据目录：

```text
framepulse export record.fmp.json --format web-annotation --source-uri https://media.example.org/video.mp4 --evidence-base-uri https://media.example.org/asset-bundle/ --output annotations.jsonld
```

示例域名仅说明参数。`--source-uri` 只替换主媒体定位；区域指向的关键帧需要 `--evidence-base-uri`。证据目录 URI 应以 `/` 结尾，并包含 `frames/` 的上级目录，这样 `frames/xxx.jpg` 才能正确解析。默认导出使用本机源媒体和证据帧的 `file:///` URI，网页端下载也遵循这一默认值；默认文件包含本机路径，不能直接当作已完成公开发布的文件。URI 替换只作用于 W3C 导出，不会匿名化原始 FMP JSON，也不会上传素材、赋予访问权限或检验远程文件是否存在。

外部工具读取时依次校验 `format`、`spec_version`、资产摘要和必需字段，再按 `shots[].id`、时间范围、关键帧和区域定位。未知字段应保留；无法理解的扩展可以忽略，不能把未知值当成默认否定。对尚未支持的版本应明确报错。尤其在 0.x 阶段，不能假设所有 minor 版本自动兼容。

`extensions` 是扩展容器；当前实现使用 `generator`、`profile`、`measurement_fingerprint`、`archived_reviewed_shots`、`archived_overview_claims`，以及镜头级的 `vision_fingerprint`、`vision_status`、`vision_usage`、`ocr_completed` 等项目键。这些键尚不是第三方扩展注册表。第三方扩展应使用发布者控制的 URI 或明确的组织命名空间，避免冲突；稳定后再考虑进入核心。版本变化应提供变更日志、升级器和升级前备份；不要原地重写旧证据。

| 0.1 范围 | 后续提案，尚不代表已实现 |
| --- | --- |
| 文件摘要、图片/视频记录、微秒镜头范围、证据帧源 PTS、首帧偏移 | 完整持久化 VFR 逐帧索引、素材家族与代理映射 |
| 字段级 Claim、运行来源、人工修改、镜头复核 | 多模型冲突候选、置信度校准、签名与长期来源链 |
| 静态区域、采样帧 OCR 字框与镜头级情绪字段 | 对象跟踪、多边形、声画联合事件、明确主体的情绪轨迹 |
| JSON、CSV、Web Annotation 导出 | IPTC/XMP 写入与回读、OTIO 时间线、EBUCore/EmotionML 适配 |

FlowDesk 可以负责发现文件、安排并发、重试和缓存，FramePulse 负责单资产分析及复核。调用方不必自动化点击界面；它应使用 CLI/本地 API，把状态和记录路径作为机器结果处理，保留失败原因，并以 `measured`、`analyzed` 和逐字段空值区分完成程度。标准映射依据和取舍见 [STANDARDS.md](STANDARDS.md)。
