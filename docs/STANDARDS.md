# FMP 的标准依据与映射边界

核对日期：2026-09-22。以下来源均为标准组织或项目官方文档。FMP 0.1.0 是 FramePulse 的实验草案；本文件解释设计参考和适配方向，不宣称认证、无损兼容或行业采用。某标准能表达一个概念，不意味着当前程序已经实现该标准的完整读取、写入或一致性测试。

## 为什么保留一个小型核心

项目需要同时表达资产身份、镜头定位、关键帧证据、AI 的不确定判断和导演修订。既有标准在不同层面解决这些问题。我们采用简单 JSON 核心并提供映射，将初版接入成本控制在一个 CLI 调用和一个可检查的文件；不要求所有调用方部署 RDF 数据库或完整广播元数据系统。这是项目的工程选择。

## 采用关系

| 官方方案 | 已核对的职责 | FMP 的取舍 | 0.1 边界 |
| --- | --- | --- | --- |
| [W3C Web Annotation Data Model](https://www.w3.org/TR/annotation-model/) §3、§4.2.1 | 用 Annotation、body、target 和 selector 表达标注及其指向。 | 将字段和镜头标记导出到可交换的 Annotation JSON-LD。 | 自有 JSON 不是 Web Annotation；只有导出文件使用该模型，不包含其完整服务协议。 |
| [W3C Media Fragments URI 1.0](https://www.w3.org/TR/media-frags/) §4.2.1、§4.2.2 | 规定 `t` 时间区间和 `xywh` 矩形；时间采用半开区间。 | 镜头范围与时空链接复用其语义。 | 它不定义切镜算法、AI 字段或跨帧对象跟踪；浏览器/下游软件是否实际响应还要验证。 |
| [IPTC Photo Metadata 2025.1](https://iptc.org/std/photometadata/specification/IPTC-PhotoMetadata-2025.1.html) §11.20、§12.6–12.8 | 图片描述、人物地点、权利，以及区域和边界。 | 后续资产级元数据与图片区域的映射依据。 | 当前 FMP 区域不是 IPTC ImageRegion 原生结构，尚无完整读写适配。 |
| [IPTC Video Metadata Hub 1.7 Properties](https://iptc.org/std/videometadatahub/recommendation/IPTC-VideoMetadataHub-props-Rec_1.7.html) | 可见/可听内容、行政、权利与技术信息；包含 Shot Type、Location Shot/Shown、Person Heard/Shown。 | 资产通用字段优先对齐已有含义，避免把“拍摄地点”和“画面地点”混为一谈。 | 导演分析不硬塞入单个关键词字段；完整 VMH JSON/XMP 适配列入后续。 |
| [Adobe XMP Specifications](https://developer.adobe.com/xmp/docs/xmp-specifications/) Part 1–3 | 元数据模型、XML/RDF 序列化及文件内嵌规则。 | 后续与图片/视频资产管理软件交换基础元数据的载体。 | FMP sidecar 不是 `.xmp`；当前不改写源文件。 |
| [OpenTimelineIO File Format Specification](https://opentimelineio.readthedocs.io/en/latest/tutorials/otio-file-format-specification.html) 与 [Serialized Data Documentation](https://opentimelineio.readthedocs.io/en/latest/tutorials/otio-serialized-schema.html) | 编辑时间线、Clip、Marker、TimeRange、RationalTime 和命名空间 metadata。 | 后续将镜头和标记送入剪辑工作流，而把自有分析放入 namespaced metadata。 | 0.1 不声称已有 OTIO/NLE 工程导出；下游适配器也可能丢失自定义 metadata。 |
| [EBU Tech 3293 / EBUCore](https://tech.ebu.ch/publications/tech3293) 与 [EBUCore schema](https://www.ebu.ch/metadata/schemas/EBUCore/documentation/urn_ebu_metadata-schema_ebucore.html) | 广播媒体描述、技术信息、部件及关系。 | 作为术语与媒体结构对照，保持后续广播系统接入空间。 | 初版不复制全套 XML/本体，也不声称 EBUCore 合规。 |
| [W3C PROV-O](https://www.w3.org/TR/prov-o/) §3.1 | Entity、Activity、Agent，以及生成、使用、派生关系。 | 用素材/证据、runs 和模型/人工来源解释“谁基于什么产生了它”。 | 当前为轻量 JSON 来源记录，不是 PROV-O RDF，也没有签名证明。 |
| [W3C EmotionML 1.0](https://www.w3.org/TR/emotionml/) §2.2–2.4 | 情绪维度、类别、置信度、表达模态、关联对象与时间轨迹。 | 区分维度数值、信心和证据，并明确主观含义。 | 当前不导入导出 EmotionML；叙事 tension 是 FMP 自定义维度。 |

IPTC VMH 本身已经有多种技术格式的映射，因此没有必要重新发明标题、权利、人物和地点的定义。[IPTC VMH 官方说明](https://iptc.org/standards/video-metadata-hub/)

## Web Annotation 导出的具体约束

每个导出的 Annotation 使用自己的 IRI、`type=Annotation`、`body`、`target` 和标准上下文 `http://www.w3.org/ns/anno.jsonld`。`target` 可以是指向媒体的 SpecificResource；通过 `FragmentSelector` 表达范围，并用 `conformsTo=http://www.w3.org/TR/media-frags/` 说明选择器语法。W3C 推荐这种显式 selector 表达，而不是只能拼接一个带 fragment 的字符串。[Web Annotation §3.1、§4.2.1](https://www.w3.org/TR/annotation-model/)

概念映射如下；这不是完整导出文件示例：

```text
FMP shot.range {start_us:1250000,end_us:4000000}
→ FragmentSelector.value "t=1.25,4"

FMP region.xywh [0.1,0.2,0.3,0.4] on a 1920×1080 keyframe
→ FragmentSelector.value "xywh=pixel:192,216,576,432"
```

区域选择器应指向区域真正依据的图片，或指向具有明确坐标变换的视频帧；不能不说明旋转/裁剪就把缩略图区域套在原媒体上。矩形像素化会发生取整；FMP 仍保留原始归一化坐标。Media Fragments 的 percent 语法是整数百分比，不接收任意精度归一化小数。[Media Fragments §4.2.2](https://www.w3.org/TR/media-frags/)

`asset.id` 用于本应用匹配，`asset.source.uri` 用于实际定位。把 `file:///...` 导出不等于远程接收者可以读取这台电脑。交换双方需要媒体 URI 重定位、媒体授权和摘要匹配机制；0.1 不建立公共文件分发服务。

来源、confidence、review、复杂导演字段不应未经命名空间定义就伪装成 W3C 标准词汇。映射可把可阅读结论放入 TextualBody；若需要完整信息，伴随原始 FMP 文件传输。只成功解析 JSON-LD，不足以证明所有 FMP 内容可往返保留。

当前适配器将每个非空 Claim 的字段名、值、来源、信心、证据 ID、run ID 和复核状态序列化为 `TextualBody.value` 中的 JSON 文本，并标明 `format=application/json`。第三方通用标注工具能定位其目标，但不一定理解文本中的 FMP 字段；需读取 FMP 的消费者额外解析该 JSON。导出文件没有内嵌完整 `runs` 或 `history`，因此 run ID 的进一步来源信息仍需随附原始记录。

当前区域导出直接指向证据关键帧，以该帧的尺寸转换像素坐标；body 另附 `source_media` 和 `source_time_us` 供 FMP 消费者回到源视频。它不再用没有终点的 `t=<单个时间>` 来代表静态框。CLI 提供 `--source-uri` 与 `--evidence-base-uri` 两个独立参数；默认仍是本机 URI，具体用法和公开交换边界见 [协议第 8 节](PROTOCOL.md#8-交换读取与版本演进)。

## IPTC / XMP 的后续字段映射提案

以下映射未作为 0.1 完整读写接口交付：

| FMP 内容 | 建议映射 | 必须保留的区别 |
| --- | --- | --- |
| `asset.name` | 不自动等同 IPTC Title；只有明确作品标题才映射。 | 文件名和作品标题不同。 |
| 人工确认的 `analysis.tags` | IPTC Keywords / XMP `dc:subject` | 技术标签、故事解释和权利状态不可混为同一种关键词。 |
| `analysis.subjects` | 在结构化且确认后映射 Person Shown / Object Shown | 角色描述不等于演员身份；模型不可补写人物真实姓名。 |
| `analysis.setting` | 条件满足时映射 Location Shown | 画面虚构地点不等于摄影机实际位置 Location Shot。 |
| `analysis.shot_size` | 使用明确词表后映射 Video Shot Type | 不同词表的粗细不同；映射时保留原词。 |
| `regions` | IPTC ImageRegion / RegionBoundary | 坐标单位、形状及参考图片必须转换。 |
| 明确来自来源的作者/版权数据 | 对应 Creator / Copyright / Rights 字段 | 分析程序与分析操作者不是原作品创作者；无法从画面推断许可。 |

VMH 的可见/可听内容字段及映射见 [IPTC VMH Properties](https://iptc.org/std/videometadatahub/recommendation/IPTC-VideoMetadataHub-props-Rec_1.7.html)；XMP 的常用命名空间见 [Adobe XMP namespace definitions](https://developer.adobe.com/xmp/docs/xmp-namespaces/)。

## 为什么情绪、节奏和可信度不能合并

EmotionML 将情绪强度、维度位置和 confidence 分开，并允许时间轨迹。FMP 借此将“情绪偏负”“很激烈”“判断没把握”保存为不同含义。0.1 中的 valence 和 arousal 使用 0–1 量表，valence 中性为 0.5；tension 则是项目自己的叙事读法。[EmotionML §2.2.2、§2.3.1](https://www.w3.org/TR/emotionml/)

镜头时长是时间测量，感受到的节奏还与动作密度、声画同步、构图重复和上下文有关；作品情绪更不能由镜头时长单独推出。这是 FMP 的分析原则。对 VLM 自报 confidence 不作校准概率承诺；将来若有人工标注基准集，应另行公布样本范围、测试条件和校准方法。

## 后续适配的验收条件

发布正式适配器前，应使用可公开的对照素材验证：时间区间、图片旋转、区域位置、未知值、来源和人工修改是否在交换后仍成立。OTIO 尤其需要验证媒体引用与原时间基准；IPTC/XMP 需要验证原元数据不会被误清空；W3C 导出需要验证实际消费者的时空跳转，而不只检查字段存在。

新适配器应有独立能力声明及已知损失说明。只有完成对应的实际互操作验证，才将“规划映射”改写为“支持”。
