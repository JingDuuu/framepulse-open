# 发布与恢复

当前版本 **0.2.1**，FMP 交换协议保持 0.1.0。公开项目为 [JingDuuu/framepulse-open](https://github.com/JingDuuu/framepulse-open)，本次仅发布自有源码 ZIP，不分发模型或含 FFmpeg 的桌面二进制。源码使用者运行 Setup 后可打开独立 APP。将来发布独立安装包前，完成下述对应源代码与安装验收。

源码公开发布、本地构建、桌面启动和干净机器安装属于不同验收项；源码发布不表示独立安装包已公开或通过干净机器安装验证。

## 构建入口与产物

FramePulse 自有源码使用 MIT 许可证。桌面版由 Electron 窗口和 PyInstaller 冻结的 Python 后台组成，后台携带本地网页、FMP Schema、Python 运行时、分析依赖及 imageio-ffmpeg 的 FFmpeg 可执行程序。正常使用打包版不需要另装 Python、Node.js 或打开浏览器；模型服务与模型权重仍须另行配置。默认冻结流程安装基础包，不安装可选 `.[ocr]`，因此不承诺安装版具备 RapidOCR。

源码开发与桌面构建需要 Windows x64、Python 3.10+、Node.js 22.12+ 和 npm。`desktop/package.json` 固定 Electron/electron-builder 版本，`package-lock.json` 固定 npm 依赖；源码 Setup 还会检查 Node 最低版本。Python CLI 使用不需要 Node.js。

在项目根目录执行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\build-desktop.ps1
```

脚本使用隔离的 `.qa-venv`，拒绝 `include-system-site-packages=true`；默认安装当前基础项目和 `pyinstaller==6.22.3`，生成图标与第三方声明，再按 [desktop/backend.spec](../desktop/backend.spec) 从当前 `src` 冻结后台。它随后验证后台 `--version` / `capabilities`，在 `desktop` 执行 `npm ci` 和 `npm test`，最后运行 electron-builder。构建需能下载对应依赖，不包含下载模型这一步。

| 参数 | 实际作用 |
| --- | --- |
| `-BackendOnly` | 只构建/检查冻结后台，然后退出。 |
| `-SkipBackend` | 复用已有冻结后台；必须确认它对应当前待发布源码。 |
| `-Unpacked` | 运行 `npm run pack`，即 `electron-builder --win --dir --x64`。 |
| `-SkipInstall` | 跳过 Python/npm 依赖安装；只适用于依赖已准备好的构建环境。 |

默认 `npm run dist` 使用 **`electron-builder --win nsis zip --x64`**，配置产物如下；文件是否已成功生成及其摘要必须逐次核验，不能仅凭配置宣称构建成功。

| 产物 | 路径/文件名 |
| --- | --- |
| 冻结后台及依赖目录 | `build/backend/framepulse-backend/`，入口 `framepulse-backend.exe` |
| 可直接启动的完整桌面目录 | `dist/win-unpacked/`，入口 `FramePulse.exe` |
| 引导式安装包 | `dist/FramePulse-Setup-0.2.1-x64.exe` |
| 可解压桌面 ZIP | `dist/FramePulse-0.2.1-win-x64.zip` |
| Python wheel / sdist | `python -m build` 生成到 `dist/` |
| allowlist 源码 ZIP | `python scripts/package-source.py` 生成 `dist/FramePulse-Open-0.2.1-source.zip` 及 `.manifest.json` |

必须分发完整安装包、ZIP 或 `win-unpacked` 目录；单独复制 `FramePulse.exe` 或冻结后台 EXE 不够。NSIS 当前 `oneClick:false`、`perMachine:false`，允许选择安装目录，创建桌面/开始菜单快捷方式；`deleteAppDataOnUninstall:false` 表示配置上保留应用数据，仍需实际验证卸载行为。构建脚本关闭 `CSC_IDENTITY_AUTO_DISCOVERY`，当前候选不配置签名证书；公开发布时应单独记录签名状态及 Windows 提示。

## 发布检查

1. 在项目 Python 环境执行 `python -m pytest -q`，并执行根目录 `node --test tests/test_web.cjs`。在 `desktop/` 执行 `npm test`，实际测试文件为 `desktop/tests/lifecycle.test.cjs`，覆盖入口、原生 IPC 来源和退出检查点协调；这些测试不替代 Electron 窗口验收。
2. 测量验收使用实际图片、不同视频编码、VFR、非零起始 PTS、透明图片及 EXIF 旋转素材；检查三档、长片上限、取消恢复、journal 回放、人工保护和 JSON 校验。保留素材参数及耗时，不把支持 45 分钟输入表述为任意 45 分钟素材都有固定完成时间。
3. 实际本地视觉模型至少完整分析一份素材，验证失败重试、明确弃答和再次续跑零重复请求；不能把 fake provider 测试当成模型质量测试。模型大小、profile、冷热启动、镜头/证据数量、实际 token 与加载/生成耗时需分开记录。
4. 验证多文件/目录导入、暂停、取消、重试、退出和重新启动后的持久队列；凭证不得写入设置或队列文件。仅做少量队列功能测试时不得宣称通过 100 份视频或其他数量的压力基准。
5. 实际桌面窗口验证选择/拖拽导入、播放/声音控件、跨镜跳转、证据区域定位、字段修改、确认、重开和导出。确认静态区域只对应证据帧；检查 renderer 错误及后台进程退出，不影响其他应用或 Ollama。
6. `python -m build` 后在隔离 Python 环境安装 wheel，执行 `framepulse capabilities` 与真实图片分析。冻结后台也独立做真实媒体测量，检查 `capabilities` 中 OCR/ffprobe 是否可用，不用开发环境的能力冒充安装版能力。
7. 使用实际构建的 `dist/win-unpacked/FramePulse.exe` 验证窗口。显式 QA 参数 `--data <绝对测试库> --smoke-output <绝对输出目录>` 会输出 `desktop-smoke.json` 和 `desktop-window.png` 并退出；它检查实际 Electron 页面、桥接、隔离设置及 renderer 错误，不等于已走完安装向导。需在独立测试目录运行，报告为打包版且后台版本为 0.2.1，并确认退出后自己创建的后台已停止。
8. 分别验证 ZIP 解压启动和 NSIS 的安装、快捷方式、启动、升级/卸载、库保留。在无 Python/Node 的干净 Windows 用户环境再次验证；未执行的项目明确列为未验证。默认库、项目启动器库、自定义 `--data` 均需确认，避免误把空库当成数据丢失。
9. 清点包内容和第三方组件声明。源码 ZIP 由 `scripts/package-source.py` allowlist 生成；排除 `runtime`、`qa-output`、`.reference`、模型、密钥、私人素材、个人路径和虚拟环境。打包桌面资源也不得携带这些数据。核对应用/包版本一致，区分 dist 中遗留的旧版本产物。
10. 满足下述第三方分发条件后，才创建公开二进制 Release。发布后重新下载公开安装包、ZIP、源码和对应源代码包，逐个核对 SHA-256 与清单，并实际检查 Release 链接。没有公开 URL 和下载核验，不称为已上线。

## 第三方组件与对应源代码

[desktop/collect-notices.py](../desktop/collect-notices.py) 收集 Python 依赖声明、Python 许可证、FramePulse MIT 许可证、GPLv3 正文、依赖版本清单以及实际 FFmpeg 的 `-L` 输出。electron-builder 将 `build/desktop-licenses/` 复制为 `resources/third-party-notices/`；Electron/Chromium 自带的 `LICENSE.electron.txt`、`LICENSES.chromium.html` 也须随完整包保留。缺少声明的依赖需在发布前补核，脚本运行成功本身不代表完整许可审计。

本机当前收集到的 imageio-ffmpeg 二进制是 FFmpeg 7.1 essentials，构建选项包含 `--enable-gpl --enable-version3`，其 `-L` 明确为 **GPLv3-or-later**。FFmpeg 作为独立子进程运行，并不把这个二进制改成 MIT；FramePulse 自有源码的 MIT 声明不能代替组件许可证。每次升级/更换 wheel 都应重新检查实际二进制和配置，不能把所有 FFmpeg 构建统称为 LGPL。[FFmpeg 官方许可说明](https://ffmpeg.org/legal.html)

公开分发含该二进制的安装包或 ZIP 时，必须按适用许可证提供其 **Corresponding Source（对应源代码）**：核对精确版本、补丁、构建配置/脚本，以及适用的随附组件对应源代码。采用网络下载发布方式时，本项目发布流程要求在二进制下载旁给出可取得相应完整源代码的清楚链接，维持同等且无额外收费的取得方式与所需可用性。仅放 GPL 正文、FFmpeg 官网或一份普通上游链接，不足以证明已履行对应源代码交付；当前 notices 收集脚本也不生成该源代码包或书面要约。[GPLv3 第 6 条](https://www.gnu.org/licenses/gpl-3.0.html#section6)（仓库副本：[GPL-3.0.txt](../desktop/assets/GPL-3.0.txt)）

候选包应记录 FFmpeg 二进制 SHA-256、`-L` 输出、来源 wheel 版本和对应源码清单，公开前逐项核对。自有源码 ZIP 不自动满足 FFmpeg 对应源代码要求。模型权重未打包；模型服务、权重与素材的许可证仍按各自来源处理。

## 本地数据与回滚

分析库独立于程序。直接运行打包 APP，Windows 默认库为 `%APPDATA%/FramePulse/library`；项目 `Start-FramePulse.cmd` 明确传入项目绝对路径 `runtime/library`，优先启动 `dist/win-unpacked/FramePulse.exe`，否则使用源码 Electron 和项目 `.venv` / `.qa-venv` 后台。CLI 默认 `runtime/library` 则相对当前工作目录。`FramePulse.exe --data <绝对路径>` 可覆盖桌面库位置，移动程序不自动迁移库。

升级前暂停队列、取消活动任务并等检查点保存，再复制整个库，包括 SHA-256 资产目录、frames、settings.local.json 和 queue.local.json。源媒体仍位于原路径。正常结束的 `record.fmp.json` 自包含；崩溃遗留的私有 journal 应连同库保留并通过程序回放，不能只丢弃它再声称备份含最新结果。API JSON 导出会读取已回放视图。

应用 0.2 仍写 FMP 0.1.0；新增可选字段不要求重写旧 sidecar。协议未来发生迁移时必须保留旧记录备份，不能直接用新语义解释旧版本。更老程序不一定认识新队列、配置或恢复日志；程序回滚也应配对保留的库快照。

手动修订在 `history` 保存旧镜头；重测改变边界时保留原记录且不按镜号盲重映射。恢复某次修改时，应读取历史在当前revision基础上重新提交，而不是绕过运行中锁覆盖文件。

关闭桌面窗口时，APP 暂停后续派发，请求取消当前任务，并等待后台保存检查点；等待超时需要用户选择继续等待或强制退出。重新打开会保留待处理任务并暂停，点击开始/恢复才继续。配置/队列不保存 API key，重启后需重新输入或由环境提供。正常卸载是否保留库，仍以该版本实际安装/卸载验收为准。
