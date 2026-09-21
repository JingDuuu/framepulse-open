# 发布与恢复

当前为本地 0.1.0 候选版本，GitHub 远程仓库由项目所有者指定。源码包不等于已公开发布，也不等于独立 Windows EXE。

## 发布检查

1. `python -m pytest -q`；实际图片和至少两种视频编码、VFR 与非零起始 PTS、断点恢复、人工保护。
2. 实际本地模型完整分析一次，并保留失败镜头重试记录；不能把 fake provider 测试当成模型质量测试。
3. 工作台导入、播放、跨镜跳转、字段/区域修改、确认、重开、导出；检查浏览器错误。
4. `python -m build`；在隔离 Python 环境安装 wheel，执行 `framepulse capabilities` 和真实图片分析。
5. 源码发布仅包含 allowlist 文件。排除 `runtime`、`qa-output`、`.reference`、模型、密钥、私人素材、个人路径和私有环境。
6. 发布 GitHub 仓库后再核对公开文件和可下载包的 SHA-256。未执行时不得称为已上线。

## 本地数据与回滚

分析库独立于程序，默认 `runtime/library`。升级前可复制该目录；原媒体无需迁移。删除或替换程序源码不代表删除分析库。协议版本迁移必须保留旧记录备份，不能直接用新语义解释旧版本。

手动修订在 `history` 保存旧镜头；重测改变边界时保留原记录且不按镜号盲重映射。恢复某次修改时，应读取历史在当前revision基础上重新提交，而不是绕过运行中锁覆盖文件。

桌面入口 `Start-FramePulse.cmd` 使用同目录 `.venv` 内的明确 Python 路径。便携源码包首次要运行 Setup；当前没有打包模型，也没有独立 Electron/PyInstaller EXE。
