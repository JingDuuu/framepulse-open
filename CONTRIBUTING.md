# Contributing

Python 3.10+。安装 `pip install -e '.[dev]'`，执行 `pytest -q`。UI 不依赖外部 CDN 或打包工具。

协议修改必须同步 schema、文档和可运行测试。不要悄悄改变时间零点、坐标系、未知值或字段来源的含义。不要用 demo 结果替代实际 AI 输出；测试 fake provider 应明确只验证接口。

PR 中请说明用户行为变化、验证素材类别和已知限制。不要提交私人素材、API key、记录库、模型权重、`.reference`、日志或构建临时文件。失败恢复、人工内容保护和实际定位是核心验收条件。
